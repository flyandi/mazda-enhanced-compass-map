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
    # Region: TG
    # Region Name: Togo

	render_tiles((1.20385,6.0991,-0.15096,6.16), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.20385,6.0991,-0.15096,6.16), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.20385,6.0991,-0.15096,6.16), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.20385,6.0991,-0.15096,6.16), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.20111,6.16,-0.15096,6.0991), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.20111,6.16,-0.15096,6.0991), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.20111,6.16,-0.15096,6.0991), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.20111,6.16,-0.15096,6.0991), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.1,6.16055,-0.15096,6.32778), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.1,6.16055,-0.15096,6.32778), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.1,6.16055,-0.15096,6.32778), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.1,6.16055,-0.15096,6.32778), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.63417,6.21068,-0.15096,8.35777), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.63417,6.21068,-0.15096,8.35777), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.63417,6.21068,-0.15096,8.35777), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.63417,6.21068,-0.15096,8.35777), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.79996,6.28093,-0.15096,6.3925), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.79996,6.28093,-0.15096,6.3925), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.79996,6.28093,-0.15096,6.3925), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.79996,6.28093,-0.15096,6.3925), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.00028,6.32778,1.20385,10.99576), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.00028,6.32778,1.20385,10.99576), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.00028,6.32778,1.20385,10.99576), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.00028,6.32778,1.20385,10.99576), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.90472,6.32944,1.20385,10.99576), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.90472,6.32944,1.20385,10.99576), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.90472,6.32944,1.20385,10.99576), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.90472,6.32944,1.20385,10.99576), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.77139,6.3925,-0.15096,6.28093), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.77139,6.3925,-0.15096,6.28093), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.77139,6.3925,-0.15096,6.28093), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.77139,6.3925,-0.15096,6.28093), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.73361,6.49083,-0.15096,8.285), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.73361,6.49083,-0.15096,8.285), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.73361,6.49083,-0.15096,8.285), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.73361,6.49083,-0.15096,8.285), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.68371,6.58733,-0.15096,8.40555), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.68371,6.58733,-0.15096,8.40555), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.68371,6.58733,-0.15096,8.40555), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.68371,6.58733,-0.15096,8.40555), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.57528,6.67972,1.20385,9.11694), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.57528,6.67972,1.20385,9.11694), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.57528,6.67972,1.20385,9.11694), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.57528,6.67972,1.20385,9.11694), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.62305,6.75778,-0.15096,8.54805), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.62305,6.75778,-0.15096,8.54805), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.62305,6.75778,-0.15096,8.54805), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.62305,6.75778,-0.15096,8.54805), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.57111,6.81333,-0.15096,7.38528), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.57111,6.81333,-0.15096,7.38528), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.57111,6.81333,-0.15096,7.38528), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.57111,6.81333,-0.15096,7.38528), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.52917,6.82583,-0.15096,7.58583), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.52917,6.82583,-0.15096,7.58583), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.52917,6.82583,-0.15096,7.58583), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.52917,6.82583,-0.15096,7.58583), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.56389,6.9175,-0.15096,7.38528), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.56389,6.9175,-0.15096,7.38528), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.56389,6.9175,-0.15096,7.38528), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.56389,6.9175,-0.15096,7.38528), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.51389,6.97083,1.20385,8.91194), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.51389,6.97083,1.20385,8.91194), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.51389,6.97083,1.20385,8.91194), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.51389,6.97083,1.20385,8.91194), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.54861,6.99528,-0.15096,6.67972), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.54861,6.99528,-0.15096,6.67972), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.54861,6.99528,-0.15096,6.67972), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.54861,6.99528,-0.15096,6.67972), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.6425,6.99555,-0.15096,7.26555), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.6425,6.99555,-0.15096,7.26555), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.6425,6.99555,-0.15096,7.26555), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.6425,6.99555,-0.15096,7.26555), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.60806,7.01444,-0.15096,7.76917), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.60806,7.01444,-0.15096,7.76917), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.60806,7.01444,-0.15096,7.76917), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.60806,7.01444,-0.15096,7.76917), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.64444,7.26555,-0.15096,6.99555), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.64444,7.26555,-0.15096,6.99555), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.64444,7.26555,-0.15096,6.99555), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.64444,7.26555,-0.15096,6.99555), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.66583,7.30361,1.20385,10.99666), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.66583,7.30361,1.20385,10.99666), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.66583,7.30361,1.20385,10.99666), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.66583,7.30361,1.20385,10.99666), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.57028,7.38528,-0.15096,6.81333), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.57028,7.38528,-0.15096,6.81333), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.57028,7.38528,-0.15096,6.81333), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.57028,7.38528,-0.15096,6.81333), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.64667,7.4,-0.15096,8.49166), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.64667,7.4,-0.15096,8.49166), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.64667,7.4,-0.15096,8.49166), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.64667,7.4,-0.15096,8.49166), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.51056,7.46055,-0.15096,6.97083), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.51056,7.46055,-0.15096,6.97083), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.51056,7.46055,-0.15096,6.97083), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.51056,7.46055,-0.15096,6.97083), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.52111,7.58583,1.20385,8.91194), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.52111,7.58583,1.20385,8.91194), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.52111,7.58583,1.20385,8.91194), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.52111,7.58583,1.20385,8.91194), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.58194,7.62083,-0.15096,7.69416), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.58194,7.62083,-0.15096,7.69416), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.58194,7.62083,-0.15096,7.69416), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.58194,7.62083,-0.15096,7.69416), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.58722,7.69416,-0.15096,8.1975), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.58722,7.69416,-0.15096,8.1975), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.58722,7.69416,-0.15096,8.1975), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.58722,7.69416,-0.15096,8.1975), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.62694,7.76917,-0.15096,8.49166), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.62694,7.76917,-0.15096,8.49166), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.62694,7.76917,-0.15096,8.49166), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.62694,7.76917,-0.15096,8.49166), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.58889,8.1975,-0.15096,7.69416), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.58889,8.1975,-0.15096,7.69416), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.58889,8.1975,-0.15096,7.69416), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.58889,8.1975,-0.15096,7.69416), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.72694,8.285,-0.15096,6.49083), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.72694,8.285,-0.15096,6.49083), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.72694,8.285,-0.15096,6.49083), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.72694,8.285,-0.15096,6.49083), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.63472,8.35777,-0.15096,6.21068), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.63472,8.35777,-0.15096,6.21068), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.63472,8.35777,-0.15096,6.21068), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.63472,8.35777,-0.15096,6.21068), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.61305,8.37472,-0.15096,6.75778), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.61305,8.37472,-0.15096,6.75778), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.61305,8.37472,-0.15096,6.75778), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.61305,8.37472,-0.15096,6.75778), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.67417,8.40555,1.20385,10.99666), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.67417,8.40555,1.20385,10.99666), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.67417,8.40555,1.20385,10.99666), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.67417,8.40555,1.20385,10.99666), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.63389,8.49166,-0.15096,7.76917), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.63389,8.49166,-0.15096,7.76917), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.63389,8.49166,-0.15096,7.76917), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.63389,8.49166,-0.15096,7.76917), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.66083,8.49694,-0.15096,7.26555), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.66083,8.49694,-0.15096,7.26555), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.66083,8.49694,-0.15096,7.26555), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.66083,8.49694,-0.15096,7.26555), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.62472,8.54805,-0.15096,6.75778), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.62472,8.54805,-0.15096,6.75778), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.62472,8.54805,-0.15096,6.75778), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.62472,8.54805,-0.15096,6.75778), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.4725,8.59277,1.20385,9.48833), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.4725,8.59277,1.20385,9.48833), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.4725,8.59277,1.20385,9.48833), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.4725,8.59277,1.20385,9.48833), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.38306,8.76222,1.20385,10.27363), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.38306,8.76222,1.20385,10.27363), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.38306,8.76222,1.20385,10.27363), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.38306,8.76222,1.20385,10.27363), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.39,8.7875,1.20385,10.30833), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.39,8.7875,1.20385,10.30833), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.39,8.7875,1.20385,10.30833), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.39,8.7875,1.20385,10.30833), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.4825,8.79166,-0.15096,8.59277), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.4825,8.79166,-0.15096,8.59277), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.4825,8.79166,-0.15096,8.59277), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.4825,8.79166,-0.15096,8.59277), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.62778,8.88111,-0.15096,8.54805), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.62778,8.88111,-0.15096,8.54805), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.62778,8.88111,-0.15096,8.54805), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.62778,8.88111,-0.15096,8.54805), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.51444,8.91194,-0.15096,6.97083), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.51444,8.91194,-0.15096,6.97083), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.51444,8.91194,-0.15096,6.97083), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.51444,8.91194,-0.15096,6.97083), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.44806,9.02,1.20385,9.48833), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.44806,9.02,1.20385,9.48833), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.44806,9.02,1.20385,9.48833), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.44806,9.02,1.20385,9.48833), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.59639,9.11694,-0.15096,8.37472), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.59639,9.11694,-0.15096,8.37472), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.59639,9.11694,-0.15096,8.37472), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.59639,9.11694,-0.15096,8.37472), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.47361,9.25639,-0.15096,6.99528), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.47361,9.25639,-0.15096,6.99528), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.47361,9.25639,-0.15096,6.99528), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.47361,9.25639,-0.15096,6.99528), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.55194,9.36111,1.20385,9.415), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.55194,9.36111,1.20385,9.415), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.55194,9.36111,1.20385,9.415), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.55194,9.36111,1.20385,9.415), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.54889,9.415,1.20385,9.36111), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.54889,9.415,1.20385,9.36111), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.54889,9.415,1.20385,9.36111), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.54889,9.415,1.20385,9.36111), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.28778,9.42138,1.20385,10.41389), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.28778,9.42138,1.20385,10.41389), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.28778,9.42138,1.20385,10.41389), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.28778,9.42138,1.20385,10.41389), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.22901,9.43228,1.20385,9.57222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.22901,9.43228,1.20385,9.57222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.22901,9.43228,1.20385,9.57222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.22901,9.43228,1.20385,9.57222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.22111,9.47277,1.20385,9.52861), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.22111,9.47277,1.20385,9.52861), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.22111,9.47277,1.20385,9.52861), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.22111,9.47277,1.20385,9.52861), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.275,9.48055,1.20385,9.68), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.275,9.48055,1.20385,9.68), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.275,9.48055,1.20385,9.68), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.275,9.48055,1.20385,9.68), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.46278,9.48833,-0.15096,8.59277), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.46278,9.48833,-0.15096,8.59277), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.46278,9.48833,-0.15096,8.59277), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.46278,9.48833,-0.15096,8.59277), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.36444,9.48972,1.20385,10.0275), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.36444,9.48972,1.20385,10.0275), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.36444,9.48972,1.20385,10.0275), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.36444,9.48972,1.20385,10.0275), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.29722,9.50972,1.20385,9.42138), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.29722,9.50972,1.20385,9.42138), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.29722,9.50972,1.20385,9.42138), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.29722,9.50972,1.20385,9.42138), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.22306,9.52861,1.20385,9.47277), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.22306,9.52861,1.20385,9.47277), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.22306,9.52861,1.20385,9.47277), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.22306,9.52861,1.20385,9.47277), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.3375,9.5425,1.20385,9.99527), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.3375,9.5425,1.20385,9.99527), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.3375,9.5425,1.20385,9.99527), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.3375,9.5425,1.20385,9.99527), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.34861,9.56444,1.20385,9.61166), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.34861,9.56444,1.20385,9.61166), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.34861,9.56444,1.20385,9.61166), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.34861,9.56444,1.20385,9.61166), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.23306,9.57222,1.20385,9.43228), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.23306,9.57222,1.20385,9.43228), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.23306,9.57222,1.20385,9.43228), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.23306,9.57222,1.20385,9.43228), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.37639,9.59055,1.20385,8.76222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.37639,9.59055,1.20385,8.76222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.37639,9.59055,1.20385,8.76222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.37639,9.59055,1.20385,8.76222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.28528,9.59111,1.20385,10.41389), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.28528,9.59111,1.20385,10.41389), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.28528,9.59111,1.20385,10.41389), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.28528,9.59111,1.20385,10.41389), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.36833,9.59666,1.20385,9.99527), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.36833,9.59666,1.20385,9.99527), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.36833,9.59666,1.20385,9.99527), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.36833,9.59666,1.20385,9.99527), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.34917,9.61166,1.20385,9.69222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.34917,9.61166,1.20385,9.69222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.34917,9.61166,1.20385,9.69222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.34917,9.61166,1.20385,9.69222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.33583,9.64972,1.20385,9.56444), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.33583,9.64972,1.20385,9.56444), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.33583,9.64972,1.20385,9.56444), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.33583,9.64972,1.20385,9.56444), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.25306,9.65361,1.20385,9.57222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.25306,9.65361,1.20385,9.57222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.25306,9.65361,1.20385,9.57222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.25306,9.65361,1.20385,9.57222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.27528,9.68,1.20385,9.48055), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.27528,9.68,1.20385,9.48055), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.27528,9.68,1.20385,9.48055), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.27528,9.68,1.20385,9.48055), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.34944,9.69222,1.20385,9.61166), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.34944,9.69222,1.20385,9.61166), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.34944,9.69222,1.20385,9.61166), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.34944,9.69222,1.20385,9.61166), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.3225,9.72916,1.20385,10.33055), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.3225,9.72916,1.20385,10.33055), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.3225,9.72916,1.20385,10.33055), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.3225,9.72916,1.20385,10.33055), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((1.355,9.99527,1.20385,9.59666), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((1.355,9.99527,1.20385,9.59666), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((1.355,9.99527,1.20385,9.59666), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((1.355,9.99527,1.20385,9.59666), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.36806,10.0275,1.20385,9.48972), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.36806,10.0275,1.20385,9.48972), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.36806,10.0275,1.20385,9.48972), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.36806,10.0275,1.20385,9.48972), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.39944,10.06111,1.20385,10.30833), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.39944,10.06111,1.20385,10.30833), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.39944,10.06111,1.20385,10.30833), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.39944,10.06111,1.20385,10.30833), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.34972,10.11527,1.20385,9.69222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.34972,10.11527,1.20385,9.69222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.34972,10.11527,1.20385,9.69222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.34972,10.11527,1.20385,9.69222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.38324,10.27363,1.20385,8.76222), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.38324,10.27363,1.20385,8.76222), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.38324,10.27363,1.20385,8.76222), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.38324,10.27363,1.20385,8.76222), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.39333,10.30833,1.20385,8.7875), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.39333,10.30833,1.20385,8.7875), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.39333,10.30833,1.20385,8.7875), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.39333,10.30833,1.20385,8.7875), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.32083,10.33055,1.20385,9.72916), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.32083,10.33055,1.20385,9.72916), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.32083,10.33055,1.20385,9.72916), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.32083,10.33055,1.20385,9.72916), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.77667,10.37666,1.20385,10.52444), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.77667,10.37666,1.20385,10.52444), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.77667,10.37666,1.20385,10.52444), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.77667,10.37666,1.20385,10.52444), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.19639,10.39778,1.20385,9.47277), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.19639,10.39778,1.20385,9.47277), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.19639,10.39778,1.20385,9.47277), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.19639,10.39778,1.20385,9.47277), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.28639,10.41389,1.20385,9.59111), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.28639,10.41389,1.20385,9.59111), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.28639,10.41389,1.20385,9.59111), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.28639,10.41389,1.20385,9.59111), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.78528,10.52444,1.20385,10.37666), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.78528,10.52444,1.20385,10.37666), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.78528,10.52444,1.20385,10.37666), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.78528,10.52444,1.20385,10.37666), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.04583,10.58611,1.20385,11.075), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.04583,10.58611,1.20385,11.075), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.04583,10.58611,1.20385,11.075), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.04583,10.58611,1.20385,11.075), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.06833,10.63389,1.20385,10.73777), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.06833,10.63389,1.20385,10.73777), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.06833,10.63389,1.20385,10.73777), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.06833,10.63389,1.20385,10.73777), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.07139,10.73777,1.20385,10.63389), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.07139,10.73777,1.20385,10.63389), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.07139,10.73777,1.20385,10.63389), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.07139,10.73777,1.20385,10.63389), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.50417,10.93694,1.20385,11.0025), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.50417,10.93694,1.20385,11.0025), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.50417,10.93694,1.20385,11.0025), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.50417,10.93694,1.20385,11.0025), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.0325,10.98972,1.20385,11.075), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.0325,10.98972,1.20385,11.075), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.0325,10.98972,1.20385,11.075), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.0325,10.98972,1.20385,11.075), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.91744,10.99576,-0.15096,6.32944), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.91744,10.99576,-0.15096,6.32944), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.91744,10.99576,-0.15096,6.32944), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.91744,10.99576,-0.15096,6.32944), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.67167,10.99666,-0.15096,8.40555), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.67167,10.99666,-0.15096,8.40555), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.67167,10.99666,-0.15096,8.40555), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.67167,10.99666,-0.15096,8.40555), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.50694,11.0025,1.20385,10.93694), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.50694,11.0025,1.20385,10.93694), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.50694,11.0025,1.20385,10.93694), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.50694,11.0025,1.20385,10.93694), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((0.03444,11.075,1.20385,10.98972), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((0.03444,11.075,1.20385,10.98972), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((0.03444,11.075,1.20385,10.98972), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((0.03444,11.075,1.20385,10.98972), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.02111,11.10361,1.20385,10.63389), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.02111,11.10361,1.20385,10.63389), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.02111,11.10361,1.20385,10.63389), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.02111,11.10361,1.20385,10.63389), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.13389,11.11139,1.20385,11.13927), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.13389,11.11139,1.20385,11.13927), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.13389,11.11139,1.20385,11.13927), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.13389,11.11139,1.20385,11.13927), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 17, 17, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 0, 11, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 13, 13, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 15, 15, "tg-togo")
	render_tiles((-0.15096,11.13927,1.20385,11.11139), mapfile, tile_dir, 17, 17, "tg-togo")