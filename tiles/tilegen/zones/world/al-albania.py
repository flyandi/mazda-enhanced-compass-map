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
    # Region: AL
    # Region Name: Albania

	render_tiles((20.26056,39.66776,20.26056,42.32443), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.26056,39.66776,20.26056,42.32443), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.26056,39.66776,20.26056,42.32443), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.26056,39.66776,20.26056,42.32443), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.0123,39.69023,19.71749,39.8486), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.0123,39.69023,19.71749,39.8486), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.0123,39.69023,19.71749,39.8486), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.0123,39.69023,19.71749,39.8486), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.30249,39.77832,19.71749,39.80665), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.30249,39.77832,19.71749,39.80665), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.30249,39.77832,19.71749,39.80665), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.30249,39.77832,19.71749,39.80665), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.38138,39.78721,19.71749,39.99915), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.38138,39.78721,19.71749,39.99915), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.38138,39.78721,19.71749,39.99915), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.38138,39.78721,19.71749,39.99915), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.99611,39.79471,19.71749,39.69023), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.99611,39.79471,19.71749,39.69023), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.99611,39.79471,19.71749,39.69023), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.99611,39.79471,19.71749,39.69023), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.29138,39.80665,19.71749,39.77832), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.29138,39.80665,19.71749,39.77832), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.29138,39.80665,19.71749,39.77832), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.29138,39.80665,19.71749,39.77832), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.41055,39.81193,19.71749,40.05109), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.41055,39.81193,19.71749,40.05109), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.41055,39.81193,19.71749,40.05109), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.41055,39.81193,19.71749,40.05109), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.01916,39.8486,19.71749,39.69023), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.01916,39.8486,19.71749,39.69023), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.01916,39.8486,19.71749,39.69023), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.01916,39.8486,19.71749,39.69023), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.36305,39.90749,20.26056,42.32443), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.36305,39.90749,20.26056,42.32443), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.36305,39.90749,20.26056,42.32443), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.36305,39.90749,20.26056,42.32443), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.31472,39.99026,19.71749,39.77832), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.31472,39.99026,19.71749,39.77832), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.31472,39.99026,19.71749,39.77832), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.31472,39.99026,19.71749,39.77832), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.39416,39.99915,19.71749,39.78721), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.39416,39.99915,19.71749,39.78721), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.39416,39.99915,19.71749,39.78721), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.39416,39.99915,19.71749,39.78721), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.41277,40.05109,19.71749,39.81193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.41277,40.05109,19.71749,39.81193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.41277,40.05109,19.71749,39.81193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.41277,40.05109,19.71749,39.81193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.79499,40.0536,20.26056,42.4697), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.79499,40.0536,20.26056,42.4697), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.79499,40.0536,20.26056,42.4697), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.79499,40.0536,20.26056,42.4697), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.55583,40.06638,20.26056,41.58471), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.55583,40.06638,20.26056,41.58471), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.55583,40.06638,20.26056,41.58471), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.55583,40.06638,20.26056,41.58471), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.51361,40.08138,20.26056,41.72776), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.51361,40.08138,20.26056,41.72776), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.51361,40.08138,20.26056,41.72776), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.51361,40.08138,20.26056,41.72776), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.66527,40.09415,19.71749,41.08804), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.66527,40.09415,19.71749,41.08804), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.66527,40.09415,19.71749,41.08804), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.66527,40.09415,19.71749,41.08804), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.4561,40.22249,19.71749,40.5611), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.4561,40.22249,19.71749,40.5611), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.4561,40.22249,19.71749,40.5611), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.4561,40.22249,19.71749,40.5611), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.41889,40.32693,20.26056,41.32388), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.41889,40.32693,20.26056,41.32388), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.41889,40.32693,20.26056,41.32388), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.41889,40.32693,20.26056,41.32388), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.47083,40.34193,20.26056,42.39999), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.47083,40.34193,20.26056,42.39999), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.47083,40.34193,20.26056,42.39999), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.47083,40.34193,20.26056,42.39999), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.78722,40.39471,19.71749,40.90026), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.78722,40.39471,19.71749,40.90026), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.78722,40.39471,19.71749,40.90026), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.78722,40.39471,19.71749,40.90026), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.2886,40.41749,20.26056,42.18554), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.2886,40.41749,20.26056,42.18554), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.2886,40.41749,20.26056,42.18554), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.2886,40.41749,20.26056,42.18554), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.31888,40.43888,19.71749,40.64054), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.31888,40.43888,19.71749,40.64054), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.31888,40.43888,19.71749,40.64054), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.31888,40.43888,19.71749,40.64054), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.48777,40.44082,19.71749,41.00193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.48777,40.44082,19.71749,41.00193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.48777,40.44082,19.71749,41.00193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.48777,40.44082,19.71749,41.00193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.93694,40.46443,19.71749,40.77165), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.93694,40.46443,19.71749,40.77165), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.93694,40.46443,19.71749,40.77165), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.93694,40.46443,19.71749,40.77165), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.84222,40.47498,19.71749,40.93332), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.84222,40.47498,19.71749,40.93332), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.84222,40.47498,19.71749,40.93332), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.84222,40.47498,19.71749,40.93332), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.42972,40.49971,19.71749,40.87054), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.42972,40.49971,19.71749,40.87054), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.42972,40.49971,19.71749,40.87054), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.42972,40.49971,19.71749,40.87054), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.45583,40.5611,19.71749,40.22249), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.45583,40.5611,19.71749,40.22249), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.45583,40.5611,19.71749,40.22249), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.45583,40.5611,19.71749,40.22249), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39583,40.5811,20.26056,42.08789), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39583,40.5811,20.26056,42.08789), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39583,40.5811,20.26056,42.08789), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39583,40.5811,20.26056,42.08789), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((21.05305,40.61859,19.71749,40.67665), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((21.05305,40.61859,19.71749,40.67665), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((21.05305,40.61859,19.71749,40.67665), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((21.05305,40.61859,19.71749,40.67665), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.31222,40.64054,19.71749,40.43888), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.31222,40.64054,19.71749,40.43888), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.31222,40.64054,19.71749,40.43888), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.31222,40.64054,19.71749,40.43888), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((21.05194,40.67665,19.71749,40.61859), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((21.05194,40.67665,19.71749,40.61859), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((21.05194,40.67665,19.71749,40.61859), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((21.05194,40.67665,19.71749,40.61859), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((21.03147,40.69825,19.71749,40.69936), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((21.03147,40.69825,19.71749,40.69936), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((21.03147,40.69825,19.71749,40.69936), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((21.03147,40.69825,19.71749,40.69936), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((21.03041,40.69936,19.71749,40.69825), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((21.03041,40.69936,19.71749,40.69825), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((21.03041,40.69936,19.71749,40.69825), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((21.03041,40.69936,19.71749,40.69825), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.96194,40.77165,19.71749,40.79118), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.96194,40.77165,19.71749,40.79118), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.96194,40.77165,19.71749,40.79118), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.96194,40.77165,19.71749,40.79118), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.96681,40.79118,19.71749,40.77165), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.96681,40.79118,19.71749,40.77165), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.96681,40.79118,19.71749,40.77165), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.96681,40.79118,19.71749,40.77165), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.98325,40.85702,19.71749,40.89388), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.98325,40.85702,19.71749,40.89388), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.98325,40.85702,19.71749,40.89388), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.98325,40.85702,19.71749,40.89388), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.41333,40.86638,20.26056,41.32388), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.41333,40.86638,20.26056,41.32388), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.41333,40.86638,20.26056,41.32388), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.41333,40.86638,20.26056,41.32388), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.42583,40.87054,19.71749,40.49971), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.42583,40.87054,19.71749,40.49971), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.42583,40.87054,19.71749,40.49971), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.42583,40.87054,19.71749,40.49971), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.983,40.89388,19.71749,40.85702), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.983,40.89388,19.71749,40.85702), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.983,40.89388,19.71749,40.85702), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.983,40.89388,19.71749,40.85702), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.8075,40.90026,19.71749,40.39471), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.8075,40.90026,19.71749,40.39471), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.8075,40.90026,19.71749,40.39471), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.8075,40.90026,19.71749,40.39471), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.37722,40.90443,20.26056,41.99943), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.37722,40.90443,20.26056,41.99943), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.37722,40.90443,20.26056,41.99943), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.37722,40.90443,20.26056,41.99943), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.52138,40.90804,20.26056,41.50249), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.52138,40.90804,20.26056,41.50249), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.52138,40.90804,20.26056,41.50249), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.52138,40.90804,20.26056,41.50249), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.73975,40.91099,19.71749,40.91193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.73975,40.91099,19.71749,40.91193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.73975,40.91099,19.71749,40.91193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.73975,40.91099,19.71749,40.91193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.73388,40.91193,19.71749,40.91099), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.73388,40.91193,19.71749,40.91099), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.73388,40.91193,19.71749,40.91099), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.73388,40.91193,19.71749,40.91099), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.85888,40.93332,19.71749,40.47498), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.85888,40.93332,19.71749,40.47498), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.85888,40.93332,19.71749,40.47498), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.85888,40.93332,19.71749,40.47498), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.44166,40.94305,20.26056,41.58499), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.44166,40.94305,20.26056,41.58499), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.44166,40.94305,20.26056,41.58499), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.44166,40.94305,20.26056,41.58499), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.47472,40.98637,19.71749,40.34193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.47472,40.98637,19.71749,40.34193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.47472,40.98637,19.71749,40.34193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.47472,40.98637,19.71749,40.34193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.4975,41.00193,20.26056,41.29276), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.4975,41.00193,20.26056,41.29276), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.4975,41.00193,20.26056,41.29276), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.4975,41.00193,20.26056,41.29276), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.43361,41.01082,19.71749,40.49971), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.43361,41.01082,19.71749,40.49971), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.43361,41.01082,19.71749,40.49971), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.43361,41.01082,19.71749,40.49971), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.66999,41.08804,19.71749,40.09415), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.66999,41.08804,19.71749,40.09415), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.66999,41.08804,19.71749,40.09415), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.66999,41.08804,19.71749,40.09415), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.64491,41.09015,19.71749,40.09415), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.64491,41.09015,19.71749,40.09415), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.64491,41.09015,19.71749,40.09415), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.64491,41.09015,19.71749,40.09415), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.59749,41.09415,20.26056,41.88455), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.59749,41.09415,20.26056,41.88455), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.59749,41.09415,20.26056,41.88455), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.59749,41.09415,20.26056,41.88455), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.51361,41.25193,20.26056,41.50249), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.51361,41.25193,20.26056,41.50249), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.51361,41.25193,20.26056,41.50249), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.51361,41.25193,20.26056,41.50249), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.50027,41.29276,19.71749,41.00193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.50027,41.29276,19.71749,41.00193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.50027,41.29276,19.71749,41.00193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.50027,41.29276,19.71749,41.00193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.41639,41.32388,19.71749,40.32693), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.41639,41.32388,19.71749,40.32693), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.41639,41.32388,19.71749,40.32693), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.41639,41.32388,19.71749,40.32693), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.49332,41.32748,19.71749,40.08138), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.49332,41.32748,19.71749,40.08138), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.49332,41.32748,19.71749,40.08138), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.49332,41.32748,19.71749,40.08138), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.43361,41.39943,19.71749,40.49971), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.43361,41.39943,19.71749,40.49971), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.43361,41.39943,19.71749,40.49971), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.43361,41.39943,19.71749,40.49971), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.56221,41.40387,19.71749,40.06638), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.56221,41.40387,19.71749,40.06638), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.56221,41.40387,19.71749,40.06638), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.56221,41.40387,19.71749,40.06638), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39055,41.40527,20.26056,42.08789), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39055,41.40527,20.26056,42.08789), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39055,41.40527,20.26056,42.08789), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39055,41.40527,20.26056,42.08789), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.51472,41.50249,20.26056,41.25193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.51472,41.50249,20.26056,41.25193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.51472,41.50249,20.26056,41.25193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.51472,41.50249,20.26056,41.25193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.50861,41.53471,20.26056,41.56915), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.50861,41.53471,20.26056,41.56915), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.50861,41.53471,20.26056,41.56915), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.50861,41.53471,20.26056,41.56915), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.46249,41.5536,20.26056,41.32748), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.46249,41.5536,20.26056,41.32748), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.46249,41.5536,20.26056,41.32748), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.46249,41.5536,20.26056,41.32748), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.45888,41.55471,19.71749,40.22249), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.45888,41.55471,19.71749,40.22249), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.45888,41.55471,19.71749,40.22249), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.45888,41.55471,19.71749,40.22249), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.50999,41.56915,20.26056,41.53471), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.50999,41.56915,20.26056,41.53471), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.50999,41.56915,20.26056,41.53471), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.50999,41.56915,20.26056,41.53471), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.55527,41.58471,19.71749,40.06638), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.55527,41.58471,19.71749,40.06638), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.55527,41.58471,19.71749,40.06638), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.55527,41.58471,19.71749,40.06638), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.44694,41.58499,19.71749,40.94305), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.44694,41.58499,19.71749,40.94305), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.44694,41.58499,19.71749,40.94305), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.44694,41.58499,19.71749,40.94305), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.60611,41.60165,20.26056,41.7711), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.60611,41.60165,20.26056,41.7711), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.60611,41.60165,20.26056,41.7711), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.60611,41.60165,20.26056,41.7711), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.51416,41.72776,19.71749,40.08138), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.51416,41.72776,19.71749,40.08138), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.51416,41.72776,19.71749,40.08138), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.51416,41.72776,19.71749,40.08138), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.56639,41.75277,20.26056,41.81055), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.56639,41.75277,20.26056,41.81055), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.56639,41.75277,20.26056,41.81055), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.56639,41.75277,20.26056,41.81055), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.59805,41.7711,20.26056,41.81055), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.59805,41.7711,20.26056,41.81055), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.59805,41.7711,20.26056,41.81055), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.59805,41.7711,20.26056,41.81055), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.59666,41.81055,20.26056,41.7711), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.59666,41.81055,20.26056,41.7711), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.59666,41.81055,20.26056,41.7711), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.59666,41.81055,20.26056,41.7711), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.36846,41.84932,19.71749,40.90443), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.36846,41.84932,19.71749,40.90443), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.36846,41.84932,19.71749,40.90443), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.36846,41.84932,19.71749,40.90443), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.58965,41.88455,19.71749,41.09415), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.58965,41.88455,19.71749,41.09415), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.58965,41.88455,19.71749,41.09415), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.58965,41.88455,19.71749,41.09415), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.62083,41.94971,19.71749,41.09415), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.62083,41.94971,19.71749,41.09415), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.62083,41.94971,19.71749,41.09415), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.62083,41.94971,19.71749,41.09415), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.3811,41.99943,19.71749,40.90443), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.3811,41.99943,19.71749,40.90443), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.3811,41.99943,19.71749,40.90443), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.3811,41.99943,19.71749,40.90443), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39509,42.08789,19.71749,40.5811), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39509,42.08789,19.71749,40.5811), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39509,42.08789,19.71749,40.5811), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39509,42.08789,19.71749,40.5811), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39833,42.10832,20.26056,42.31707), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39833,42.10832,20.26056,42.31707), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39833,42.10832,20.26056,42.31707), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39833,42.10832,20.26056,42.31707), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.28249,42.18554,19.71749,40.41749), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.28249,42.18554,19.71749,40.41749), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.28249,42.18554,19.71749,40.41749), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.28249,42.18554,19.71749,40.41749), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.5261,42.21165,20.26056,41.72776), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.5261,42.21165,20.26056,41.72776), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.5261,42.21165,20.26056,41.72776), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.5261,42.21165,20.26056,41.72776), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.39732,42.31707,20.26056,42.10832), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.25222,42.32443,19.71749,39.66776), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.25222,42.32443,19.71749,39.66776), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.25222,42.32443,19.71749,39.66776), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.25222,42.32443,19.71749,39.66776), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.35166,42.32443,19.71749,39.90749), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.35166,42.32443,19.71749,39.90749), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.35166,42.32443,19.71749,39.90749), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.35166,42.32443,19.71749,39.90749), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.46971,42.39999,19.71749,40.34193), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.46971,42.39999,19.71749,40.34193), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.46971,42.39999,19.71749,40.34193), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.46971,42.39999,19.71749,40.34193), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.83027,42.4697,19.71749,40.0536), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.83027,42.4697,19.71749,40.0536), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.83027,42.4697,19.71749,40.0536), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.83027,42.4697,19.71749,40.0536), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.1686,42.50694,20.26056,42.32443), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.1686,42.50694,20.26056,42.32443), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.1686,42.50694,20.26056,42.32443), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.1686,42.50694,20.26056,42.32443), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.74666,42.54305,20.26056,42.63971), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.74666,42.54305,20.26056,42.63971), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.74666,42.54305,20.26056,42.63971), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.74666,42.54305,20.26056,42.63971), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.07272,42.55844,20.26056,42.56304), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.07272,42.55844,20.26056,42.56304), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.07272,42.55844,20.26056,42.56304), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.07272,42.55844,20.26056,42.56304), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((20.06416,42.56304,20.26056,42.55844), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((20.06416,42.56304,20.26056,42.55844), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((20.06416,42.56304,20.26056,42.55844), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((20.06416,42.56304,20.26056,42.55844), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.63583,42.60609,20.26056,41.60165), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.63583,42.60609,20.26056,41.60165), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.63583,42.60609,20.26056,41.60165), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.63583,42.60609,20.26056,41.60165), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.75583,42.63971,20.26056,42.54305), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.75583,42.63971,20.26056,42.54305), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.75583,42.63971,20.26056,42.54305), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.75583,42.63971,20.26056,42.54305), mapfile, tile_dir, 17, 17, "al-albania")
	render_tiles((19.71749,42.66137,20.26056,42.54305), mapfile, tile_dir, 0, 11, "al-albania")
	render_tiles((19.71749,42.66137,20.26056,42.54305), mapfile, tile_dir, 13, 13, "al-albania")
	render_tiles((19.71749,42.66137,20.26056,42.54305), mapfile, tile_dir, 15, 15, "al-albania")
	render_tiles((19.71749,42.66137,20.26056,42.54305), mapfile, tile_dir, 17, 17, "al-albania")