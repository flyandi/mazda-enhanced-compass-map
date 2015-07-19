#!/usr/bin/env python

# Tooling Template for Tile Generation
# DO NOT MODIFY 


from math import pi,cos,sin,log,exp,atan
from subprocess import call
import sys, os
from Queue import Queue
import threading
import mapnik

DEG_TO_RAD = pi/180
RAD_TO_DEG = 180/pi

# Default number of rendering threads to spawn, should be roughly equal to number of CPU cores available
NUM_THREADS = 6


def minmax (a,b,c):
    a = max(a,b)
    a = min(a,c)
    return a

class GoogleProjection:
    def __init__(self,levels=18):
        self.Bc = []
        self.Cc = []
        self.zc = []
        self.Ac = []
        c = 256
        for d in range(0,levels):
            e = c/2;
            self.Bc.append(c/360.0)
            self.Cc.append(c/(2 * pi))
            self.zc.append((e,e))
            self.Ac.append(c)
            c *= 2
                
    def fromLLtoPixel(self,ll,zoom):
         d = self.zc[zoom]
         e = round(d[0] + ll[0] * self.Bc[zoom])
         f = minmax(sin(DEG_TO_RAD * ll[1]),-0.9999,0.9999)
         g = round(d[1] + 0.5*log((1+f)/(1-f))*-self.Cc[zoom])
         return (e,g)
     
    def fromPixelToLL(self,px,zoom):
         e = self.zc[zoom]
         f = (px[0] - e[0])/self.Bc[zoom]
         g = (px[1] - e[1])/-self.Cc[zoom]
         h = RAD_TO_DEG * ( 2 * atan(exp(g)) - 0.5 * pi)
         return (f,h)



class RenderThread:
    def __init__(self, tile_dir, mapfile, q, printLock, maxZoom):
        self.tile_dir = tile_dir
        self.q = q
        self.m = mapnik.Map(256, 256)
        self.printLock = printLock
        # Load style XML
        mapnik.load_map(self.m, mapfile, True)
        # Obtain <Map> projection
        self.prj = mapnik.Projection(self.m.srs)
        # Projects between tile pixel co-ordinates and LatLong (EPSG:4326)
        self.tileproj = GoogleProjection(maxZoom+1)


    def render_tile(self, tile_uri, x, y, z):

        # Calculate pixel positions of bottom-left & top-right
        p0 = (x * 256, (y + 1) * 256)
        p1 = ((x + 1) * 256, y * 256)

        # Convert to LatLong (EPSG:4326)
        l0 = self.tileproj.fromPixelToLL(p0, z);
        l1 = self.tileproj.fromPixelToLL(p1, z);

        # Convert to map projection (e.g. mercator co-ords EPSG:900913)
        c0 = self.prj.forward(mapnik.Coord(l0[0],l0[1]))
        c1 = self.prj.forward(mapnik.Coord(l1[0],l1[1]))

        # Bounding box for the tile
        if hasattr(mapnik,'mapnik_version') and mapnik.mapnik_version() >= 800:
            bbox = mapnik.Box2d(c0.x,c0.y, c1.x,c1.y)
        else:
            bbox = mapnik.Envelope(c0.x,c0.y, c1.x,c1.y)
        render_size = 256
        self.m.resize(render_size, render_size)
        self.m.zoom_to_box(bbox)
        if(self.m.buffer_size < 128):
            self.m.buffer_size = 128

        # Render image with default Agg renderer
        im = mapnik.Image(render_size, render_size)
        mapnik.render(self.m, im)
        im.save(tile_uri, 'png256')


    def loop(self):
        while True:
            #Fetch a tile from the queue and render it
            r = self.q.get()
            if (r == None):
                self.q.task_done()
                break
            else:
                (name, tile_uri, x, y, z) = r

            exists= ""
            if os.path.isfile(tile_uri):
                exists= "exists"
            else:
                self.render_tile(tile_uri, x, y, z)
            bytes=os.stat(tile_uri)[6]
            empty= ''

            if bytes == 103:
                empty = " Empty Tile "
                os.remove(tile_uri)

            self.printLock.acquire()
            print name, ":", z, x, y, exists, empty
            self.printLock.release()
            self.q.task_done()



def render_tiles(bbox, mapfile, tile_dir, minZoom=1,maxZoom=18, name="unknown", num_threads=NUM_THREADS, tms_scheme=False):
    print "render_tiles(",bbox, mapfile, tile_dir, minZoom,maxZoom, name,")"

    tile_dir = tile_dir + name + "/";

    # Launch rendering threads
    queue = Queue(32)
    printLock = threading.Lock()
    renderers = {}
    for i in range(num_threads):
        renderer = RenderThread(tile_dir, mapfile, queue, printLock, maxZoom)
        render_thread = threading.Thread(target=renderer.loop)
        render_thread.start()
        #print "Started render thread %s" % render_thread.getName()
        renderers[i] = render_thread

    if not os.path.exists(tile_dir):
         os.makedirs(tile_dir)

    gprj = GoogleProjection(maxZoom+1) 

    ll0 = (bbox[0],bbox[3])
    ll1 = (bbox[2],bbox[1])

    for z in range(minZoom,maxZoom + 1):
        px0 = gprj.fromLLtoPixel(ll0,z)
        px1 = gprj.fromLLtoPixel(ll1,z)

        # check if we have directories in place
        zoom = "%s" % z
        if not os.path.isdir(tile_dir + zoom):
            os.mkdir(tile_dir + zoom)
        for x in range(int(px0[0]/256.0),int(px1[0]/256.0)+1):
            # Validate x co-ordinate
            if (x < 0) or (x >= 2**z):
                continue
            # check if we have directories in place
            str_x = "%s" % x
            if not os.path.isdir(tile_dir + zoom + '/' + str_x):
                os.mkdir(tile_dir + zoom + '/' + str_x)
            for y in range(int(px0[1]/256.0),int(px1[1]/256.0)+1):
                # Validate x co-ordinate
                if (y < 0) or (y >= 2**z):
                    continue
                # flip y to match OSGEO TMS spec
                if tms_scheme:
                    str_y = "%s" % ((2**z-1) - y)
                else:
                    str_y = "%s" % y
                tile_uri = tile_dir + zoom + '/' + str_x + '/' + str_y + '.png'
                # Submit tile to be rendered into the queue
                t = (name, tile_uri, x, y, z)
                try:
                    queue.put(t)
                except KeyboardInterrupt:
                    raise SystemExit("Ctrl-c detected, exiting...")

    # Signal render threads to exit by sending empty request to queue
    for i in range(num_threads):
        queue.put(None)
    # wait for pending rendering jobs to complete
    queue.join()
    for i in range(num_threads):
        renderers[i].join()




if __name__ == "__main__":
    home = os.environ['HOME']
    try:
        mapfile = "../tilestyles/mazda/mazda.xml"
    except KeyError:
        print("[MapFile] Not found")
        sys.exit(1)
    try:
        # ./tilegen/zones/[zone]/[region]
        tile_dir = "../../../output/"
    except KeyError:
        print("[OutputDir] No output directory found")
        sys.exit(1)

    if not tile_dir.endswith('/'):
        tile_dir = tile_dir + '/'


    # ------------------------------------------------------------------------
    # Tile Render Data
    # Zone: world
    # Region: EH
    # Region Name: Western Sahara

	render_tiles((-17.05435,20.77007,-8.66929,20.80416), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-17.05435,20.77007,-8.66929,20.80416), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-17.05435,20.77007,-8.66929,20.80416), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-17.05435,20.77007,-8.66929,20.80416), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-17.07361,20.80416,-8.66929,20.77007), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-17.07361,20.80416,-8.66929,20.77007), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-17.07361,20.80416,-8.66929,20.77007), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-17.07361,20.80416,-8.66929,20.77007), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-17.05435,20.91793,-8.66929,20.80416), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-17.05435,20.91793,-8.66929,20.80416), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-17.05435,20.91793,-8.66929,20.80416), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-17.05435,20.91793,-8.66929,20.80416), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-17.02611,21.30166,-8.66929,20.77007), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-17.02611,21.30166,-8.66929,20.77007), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-17.02611,21.30166,-8.66929,20.77007), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-17.02611,21.30166,-8.66929,20.77007), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.17028,21.33694,-17.05435,24.44333), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.17028,21.33694,-17.05435,24.44333), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.17028,21.33694,-17.05435,24.44333), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.17028,21.33694,-17.05435,24.44333), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13,21.33805,-8.66929,23.02472), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13,21.33805,-8.66929,23.02472), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13,21.33805,-8.66929,23.02472), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13,21.33805,-8.66929,23.02472), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.95305,21.33833,-8.66929,21.83333), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.95305,21.33833,-8.66929,21.83333), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.95305,21.33833,-8.66929,21.83333), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.95305,21.33833,-8.66929,21.83333), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.39555,21.34027,-17.05435,27.08805), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.39555,21.34027,-17.05435,27.08805), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.39555,21.34027,-17.05435,27.08805), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.39555,21.34027,-17.05435,27.08805), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.95695,21.83333,-8.66929,21.33833), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.95695,21.83333,-8.66929,21.33833), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.95695,21.83333,-8.66929,21.33833), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.95695,21.83333,-8.66929,21.33833), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.85083,22.07555,-8.66929,21.33833), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.85083,22.07555,-8.66929,21.33833), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.85083,22.07555,-8.66929,21.33833), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.85083,22.07555,-8.66929,21.33833), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.62195,22.27472,-8.66929,22.29638), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.62195,22.27472,-8.66929,22.29638), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.62195,22.27472,-8.66929,22.29638), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.62195,22.27472,-8.66929,22.29638), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.66889,22.29638,-8.66929,22.27472), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.66889,22.29638,-8.66929,22.27472), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.66889,22.29638,-8.66929,22.27472), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.66889,22.29638,-8.66929,22.27472), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.49695,22.32583,-8.66929,22.43805), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.49695,22.32583,-8.66929,22.43805), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.49695,22.32583,-8.66929,22.43805), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.49695,22.32583,-8.66929,22.43805), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.4525,22.43805,-8.66929,22.32583), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.4525,22.43805,-8.66929,22.32583), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.4525,22.43805,-8.66929,22.32583), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.4525,22.43805,-8.66929,22.32583), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.07917,22.51055,-8.66929,22.89305), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.07917,22.51055,-8.66929,22.89305), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.07917,22.51055,-8.66929,22.89305), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.07917,22.51055,-8.66929,22.89305), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.15028,22.7575,-17.05435,27.66508), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.15028,22.7575,-17.05435,27.66508), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.15028,22.7575,-17.05435,27.66508), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.15028,22.7575,-17.05435,27.66508), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.10555,22.89305,-8.66929,22.51055), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.10555,22.89305,-8.66929,22.51055), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.10555,22.89305,-8.66929,22.51055), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.10555,22.89305,-8.66929,22.51055), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.28722,22.90055,-8.66929,23.08388), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.28722,22.90055,-8.66929,23.08388), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.28722,22.90055,-8.66929,23.08388), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.28722,22.90055,-8.66929,23.08388), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.20556,22.9286,-8.66929,23.08388), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.20556,22.9286,-8.66929,23.08388), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.20556,22.9286,-8.66929,23.08388), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.20556,22.9286,-8.66929,23.08388), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.15861,23.00055,-8.66929,23.06888), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.15861,23.00055,-8.66929,23.06888), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.15861,23.00055,-8.66929,23.06888), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.15861,23.00055,-8.66929,23.06888), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-12.99861,23.02472,-8.66929,21.33805), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-12.99861,23.02472,-8.66929,21.33805), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-12.99861,23.02472,-8.66929,21.33805), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-12.99861,23.02472,-8.66929,21.33805), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.15195,23.06888,-8.66929,23.00055), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.15195,23.06888,-8.66929,23.00055), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.15195,23.06888,-8.66929,23.00055), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.15195,23.06888,-8.66929,23.00055), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.21639,23.08388,-8.66929,22.9286), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.21639,23.08388,-8.66929,22.9286), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.21639,23.08388,-8.66929,22.9286), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.21639,23.08388,-8.66929,22.9286), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-12.59639,23.27643,-8.66929,23.02472), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-12.59639,23.27643,-8.66929,23.02472), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-12.59639,23.27643,-8.66929,23.02472), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-12.59639,23.27643,-8.66929,23.02472), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-16.02723,23.37527,-8.66929,23.66333), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-16.02723,23.37527,-8.66929,23.66333), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-16.02723,23.37527,-8.66929,23.66333), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-16.02723,23.37527,-8.66929,23.66333), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-12.00056,23.45444,-17.05435,26), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-12.00056,23.45444,-17.05435,26), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-12.00056,23.45444,-17.05435,26), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-12.00056,23.45444,-17.05435,26), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.975,23.66333,-8.66929,23.37527), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.975,23.66333,-8.66929,23.37527), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.975,23.66333,-8.66929,23.37527), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.975,23.66333,-8.66929,23.37527), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.76306,23.79138,-8.66929,23.91), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.76306,23.79138,-8.66929,23.91), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.76306,23.79138,-8.66929,23.91), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.76306,23.79138,-8.66929,23.91), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.90139,23.83277,-8.66929,23.66333), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.90139,23.83277,-8.66929,23.66333), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.90139,23.83277,-8.66929,23.66333), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.90139,23.83277,-8.66929,23.66333), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.78167,23.91,-8.66929,23.79138), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.78167,23.91,-8.66929,23.79138), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.78167,23.91,-8.66929,23.79138), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.78167,23.91,-8.66929,23.79138), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.67527,24.00047,-8.66929,23.79138), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.67527,24.00047,-8.66929,23.79138), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.67527,24.00047,-8.66929,23.79138), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.67527,24.00047,-8.66929,23.79138), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.41361,24.24166,-17.05435,24.44333), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.41361,24.24166,-17.05435,24.44333), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.41361,24.24166,-17.05435,24.44333), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.41361,24.24166,-17.05435,24.44333), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.22667,24.44333,-8.66929,21.33694), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.22667,24.44333,-8.66929,21.33694), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.22667,24.44333,-8.66929,21.33694), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.22667,24.44333,-8.66929,21.33694), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-15.03139,24.54166,-17.05435,24.70833), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-15.03139,24.54166,-17.05435,24.70833), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-15.03139,24.54166,-17.05435,24.70833), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-15.03139,24.54166,-17.05435,24.70833), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.89306,24.70833,-17.05435,25.05639), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.89306,24.70833,-17.05435,25.05639), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.89306,24.70833,-17.05435,25.05639), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.89306,24.70833,-17.05435,25.05639), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.83195,25.05639,-17.05435,25.31944), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.83195,25.05639,-17.05435,25.31944), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.83195,25.05639,-17.05435,25.31944), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.83195,25.05639,-17.05435,25.31944), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.82278,25.31944,-17.05435,25.05639), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.82278,25.31944,-17.05435,25.05639), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.82278,25.31944,-17.05435,25.05639), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.82278,25.31944,-17.05435,25.05639), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.71167,25.52583,-17.05435,25.62388), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.71167,25.52583,-17.05435,25.62388), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.71167,25.52583,-17.05435,25.62388), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.71167,25.52583,-17.05435,25.62388), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.68278,25.62388,-17.05435,25.52583), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.68278,25.62388,-17.05435,25.52583), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.68278,25.62388,-17.05435,25.52583), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.68278,25.62388,-17.05435,25.52583), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.51722,25.93027,-17.05435,26.15555), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.51722,25.93027,-17.05435,26.15555), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.51722,25.93027,-17.05435,26.15555), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.51722,25.93027,-17.05435,26.15555), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-8.76722,25.99971,-17.05435,27.2802), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-8.76722,25.99971,-17.05435,27.2802), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-8.76722,25.99971,-17.05435,27.2802), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-8.76722,25.99971,-17.05435,27.2802), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-12.00083,26,-8.66929,23.45444), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-12.00083,26,-8.66929,23.45444), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-12.00083,26,-8.66929,23.45444), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-12.00083,26,-8.66929,23.45444), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-8.66722,26.00027,-17.05435,27.2802), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-8.66722,26.00027,-17.05435,27.2802), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-8.66722,26.00027,-17.05435,27.2802), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-8.66722,26.00027,-17.05435,27.2802), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.48667,26.15555,-17.05435,25.93027), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.48667,26.15555,-17.05435,25.93027), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.48667,26.15555,-17.05435,25.93027), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.48667,26.15555,-17.05435,25.93027), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.40917,26.25944,-17.05435,26.15555), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.40917,26.25944,-17.05435,26.15555), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.40917,26.25944,-17.05435,26.15555), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.40917,26.25944,-17.05435,26.15555), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.29139,26.30194,-17.05435,26.42777), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.29139,26.30194,-17.05435,26.42777), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.29139,26.30194,-17.05435,26.42777), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.29139,26.30194,-17.05435,26.42777), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.17695,26.42777,-17.05435,26.43388), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.17695,26.42777,-17.05435,26.43388), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.17695,26.42777,-17.05435,26.43388), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.17695,26.42777,-17.05435,26.43388), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-14.08,26.43388,-17.05435,26.42777), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-14.08,26.43388,-17.05435,26.42777), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-14.08,26.43388,-17.05435,26.42777), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-14.08,26.43388,-17.05435,26.42777), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.61806,26.68722,-17.05435,26.76888), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.61806,26.68722,-17.05435,26.76888), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.61806,26.68722,-17.05435,26.76888), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.61806,26.68722,-17.05435,26.76888), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.54583,26.76888,-17.05435,26.68722), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.54583,26.76888,-17.05435,26.68722), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.54583,26.76888,-17.05435,26.68722), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.54583,26.76888,-17.05435,26.68722), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.42195,27.08805,-8.66929,21.34027), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.42195,27.08805,-8.66929,21.34027), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.42195,27.08805,-8.66929,21.34027), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.42195,27.08805,-8.66929,21.34027), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-8.66929,27.2802,-17.05435,26.00027), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-8.66929,27.2802,-17.05435,26.00027), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-8.66929,27.2802,-17.05435,26.00027), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-8.66929,27.2802,-17.05435,26.00027), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.32889,27.28916,-8.66929,21.34027), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.32889,27.28916,-8.66929,21.34027), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.32889,27.28916,-8.66929,21.34027), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.32889,27.28916,-8.66929,21.34027), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-13.17693,27.66508,-8.66929,22.7575), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-9.93389,27.66666,-17.05435,25.99971), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-9.93389,27.66666,-17.05435,25.99971), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-9.93389,27.66666,-17.05435,25.99971), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-9.93389,27.66666,-17.05435,25.99971), mapfile, tile_dir, 17, 17, "eh-western-sahara")
	render_tiles((-8.66929,27.66885,-17.05435,26.00027), mapfile, tile_dir, 0, 11, "eh-western-sahara")
	render_tiles((-8.66929,27.66885,-17.05435,26.00027), mapfile, tile_dir, 13, 13, "eh-western-sahara")
	render_tiles((-8.66929,27.66885,-17.05435,26.00027), mapfile, tile_dir, 15, 15, "eh-western-sahara")
	render_tiles((-8.66929,27.66885,-17.05435,26.00027), mapfile, tile_dir, 17, 17, "eh-western-sahara")