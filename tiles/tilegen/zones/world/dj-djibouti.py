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
    # Region: DJ
    # Region Name: Djibouti

	render_tiles((41.82582,10.97083,41.78721,10.98499), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.78721,10.98499,42.81415,10.98777), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.81415,10.98777,41.78721,10.98499), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.26443,10.99249,42.81415,10.98777), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.94328,11.00391,42.26443,10.99249), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.77276,11.01639,42.94328,11.00391), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.75193,11.07666,41.79797,11.09725), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.79797,11.09725,42.63443,11.09805), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.63443,11.09805,41.79797,11.09725), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.81248,11.24861,41.80883,11.26637), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.80883,11.26637,41.81248,11.24861), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.80883,11.26637,41.81248,11.24861), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.24862,11.47285,42.67693,11.47694), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.67693,11.47694,43.24862,11.47285), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.75971,11.50528,42.53416,11.50694), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.53416,11.50694,41.75971,11.50528), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.66305,11.52778,43.17304,11.53139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.17304,11.53139,42.66305,11.52778), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.62721,11.54444,43.17304,11.53139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.52832,11.56444,43.1011,11.57389), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.1011,11.57389,42.52832,11.56444), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85304,11.59278,43.1011,11.57389), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.14999,11.61167,42.85304,11.59278), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.78499,11.74028,41.82943,11.74194), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.82943,11.74194,42.78499,11.74028), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.05221,11.80139,41.94859,11.81667), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((41.94859,11.81667,43.05221,11.80139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.19916,11.95194,43.36916,11.99), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.36916,11.99,43.19916,11.95194), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.41388,12.05694,43.36916,11.99), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.41999,12.145,43.41388,12.05694), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.69861,12.36389,42.6986,12.3639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.69859,12.36389,42.6986,12.3639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.6986,12.3639,42.69861,12.36389), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.69863,12.36391,42.6986,12.3639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.69848,12.36399,42.69863,12.36391), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.68306,12.375,42.74417,12.38583), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.74417,12.38583,42.74778,12.38639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.74778,12.38639,42.73534,12.38648), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.73534,12.38648,42.74778,12.38639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.66611,12.38694,42.73534,12.38648), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.75195,12.38889,42.66611,12.38694), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.755,12.39305,43.35387,12.39639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.35387,12.39639,42.33942,12.39694), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.33942,12.39694,43.35387,12.39639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.75722,12.39778,42.64917,12.39861), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.64917,12.39861,42.75722,12.39778), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.75825,12.40056,42.64917,12.39861), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.63639,12.41306,42.75825,12.40056), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.80093,12.42679,42.80389,12.42778), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.80389,12.42778,42.80093,12.42679), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.80889,12.42972,42.62028,12.43083), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.62028,12.43083,42.80889,12.42972), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.61858,12.43284,42.8125,12.43306), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.8125,12.43306,42.61858,12.43284), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.81611,12.43611,42.8125,12.43306), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.81916,12.44028,42.81611,12.43611), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.82167,12.44472,42.81916,12.44028), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.8225,12.45111,42.82222,12.45583), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.82222,12.45583,42.82093,12.45916), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.82093,12.45916,42.82222,12.45583), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.53582,12.50416,42.45583,12.52887), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.45583,12.52887,42.45611,12.52916), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.45611,12.52916,42.45583,12.52887), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.84105,12.55509,42.84111,12.55528), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.84111,12.55528,42.84105,12.55509), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.84472,12.55861,42.84972,12.56028), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.84972,12.56028,42.85555,12.56139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85555,12.56139,42.84972,12.56028), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85917,12.56472,42.85555,12.56139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.86083,12.57028,42.85972,12.57583), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85972,12.57583,42.86083,12.57028), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85861,12.58139,42.85972,12.57583), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85885,12.58712,42.85989,12.58898), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.85989,12.58898,42.85885,12.58712), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.8625,12.59139,42.85989,12.58898), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.86694,12.59389,42.8625,12.59139), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.87139,12.59639,42.86694,12.59389), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.87416,12.60056,42.87139,12.59639), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.87666,12.60528,42.87416,12.60056), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.87694,12.61195,42.91167,12.61667), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.91167,12.61667,42.87722,12.61889), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.87722,12.61889,42.9075,12.61944), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.9075,12.61944,42.87722,12.61889), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.8777,12.62091,42.9075,12.61944), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.90361,12.6225,42.92972,12.62389), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.92972,12.62389,42.90361,12.6225), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.90167,12.62611,42.88134,12.62744), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.88134,12.62744,42.88139,12.6275), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.88139,12.6275,42.88147,12.62753), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.88147,12.62753,42.88139,12.6275), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.8975,12.62889,42.88827,12.62982), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.88827,12.62982,42.89194,12.63056), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.89194,12.63056,42.88827,12.62982), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.94778,12.63194,42.89194,12.63056), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.96584,12.64,42.98361,12.6475), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((42.98361,12.6475,42.96584,12.64), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.00167,12.65555,42.98361,12.6475), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.01972,12.66361,43.03778,12.67111), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.03778,12.67111,43.01972,12.66361), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.05583,12.67917,43.07361,12.68722), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.07361,12.68722,43.09167,12.69472), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.09167,12.69472,43.07361,12.68722), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.10972,12.70278,43.12336,12.70882), mapfile, tile_dir, 0, 11, "dj-djibouti")
	render_tiles((43.12336,12.70882,43.10972,12.70278), mapfile, tile_dir, 0, 11, "dj-djibouti")