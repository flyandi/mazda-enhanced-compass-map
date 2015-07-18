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
    # Region: LA
    # Region Name: Laos

	render_tiles((106.0919,13.92,105.9391,13.92694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.9391,13.92694,106.0919,13.92), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.857,14.00024,105.8492,14.00722), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.8492,14.00722,105.857,14.00024), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.1744,14.05028,105.8492,14.00722), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.3641,14.10944,105.7324,14.11149), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.7324,14.11149,105.3641,14.10944), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5172,14.14083,105.5983,14.15166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5983,14.15166,105.5172,14.14083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.1019,14.18666,105.5983,14.15166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.0416,14.22472,106.1019,14.18666), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.21,14.28444,106.8383,14.3125), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.8383,14.3125,105.21,14.28444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.9939,14.34333,105.2104,14.35256), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.2104,14.35256,106.0283,14.36027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.0283,14.36027,106.9975,14.36388), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.9975,14.36388,106.0283,14.36027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2097,14.37555,106.9975,14.36388), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.1089,14.40111,106.2097,14.37555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4419,14.43222,106.7336,14.4425), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.7336,14.4425,107.0502,14.44305), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.0502,14.44305,106.7336,14.4425), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.3324,14.44532,106.2469,14.44639), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2469,14.44639,106.3324,14.44532), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.6277,14.46694,106.2469,14.44639), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.4383,14.4875,106.2511,14.49333), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2511,14.49333,106.4383,14.4875), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.4333,14.52333,107.4225,14.54555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4225,14.54555,107.4541,14.55777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4541,14.55777,107.278,14.55972), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.278,14.55972,107.4541,14.55777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5378,14.57777,107.278,14.55972), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.2961,14.59861,106.5417,14.59898), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.5417,14.59898,107.2961,14.59861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.3266,14.60694,106.5417,14.59898), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.5294,14.69361,107.5459,14.70496), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.5459,14.70496,107.5294,14.69361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5214,14.79,107.5136,14.80361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.5136,14.80361,105.5214,14.79), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.5458,14.83694,107.5136,14.80361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.5847,14.89944,107.5458,14.83694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.478,14.97361,105.6191,14.99166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.6191,14.99166,107.478,14.97361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4797,15.03778,107.58,15.04416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.58,15.04416,107.4797,15.03778), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4689,15.125,105.4936,15.20527), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4936,15.20527,107.6952,15.27083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.6952,15.27083,105.5964,15.27472), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5964,15.27472,107.6952,15.27083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.588,15.33552,105.483,15.34166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.483,15.34166,107.62,15.34277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.62,15.34277,105.483,15.34166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.483,15.37694,107.62,15.34277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.6,15.43305,105.483,15.37694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4591,15.51111,105.6,15.43305), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.6344,15.59472,107.2616,15.65027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.2616,15.65027,105.6325,15.67861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.6325,15.67861,107.2616,15.65027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.2536,15.72639,105.5964,15.72805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.5964,15.72805,107.2536,15.72639), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.1877,15.75944,105.4269,15.77583), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4269,15.77583,107.1877,15.75944), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.175,15.795,105.4269,15.77583), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.2066,15.86139,107.3983,15.91389), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.3983,15.91389,105.3497,15.93805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.3497,15.93805,107.3983,15.91389), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4069,15.99222,107.4666,16.00889), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4666,16.00889,105.4119,16.0186), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4119,16.0186,107.4666,16.00889), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.3341,16.05694,105.183,16.05888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.183,16.05888,107.3341,16.05694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.4605,16.08249,105.183,16.05888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.0466,16.1286,107.4605,16.08249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.1544,16.19777,105.0216,16.23638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.0216,16.23638,107.1489,16.26083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.1489,16.26083,105.0216,16.23638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.9891,16.29777,107.0911,16.30194), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((107.0911,16.30194,106.9891,16.29777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.9397,16.32249,107.0911,16.30194), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.9072,16.39249,106.7347,16.43055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.7347,16.43055,106.8983,16.44999), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.8983,16.44999,106.7842,16.45356), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.7842,16.45356,106.8983,16.44999), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.6744,16.4786,106.7842,16.45356), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7461,16.53111,106.8852,16.53249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.8852,16.53249,104.7461,16.53111), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.848,16.53722,106.8852,16.53249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.633,16.60499,106.5868,16.62709), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.5868,16.62709,106.633,16.60499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7689,16.69749,106.5868,16.62709), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.753,16.88778,106.5563,16.89998), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.5563,16.89998,104.753,16.88778), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.4711,16.98249,106.5611,16.99694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.5611,16.99694,106.4711,16.98249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.3614,17.13555,104.8094,17.19388), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.8094,17.19388,106.3355,17.23416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.3355,17.23416,106.2536,17.24582), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2536,17.24582,106.3355,17.23416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2116,17.26166,106.2536,17.24582), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.2875,17.29389,106.2116,17.26166), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((106.0436,17.40277,104.7894,17.41472), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7894,17.41472,106.0436,17.40277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1522,17.4611,101.0991,17.49805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0991,17.49805,101.23,17.52753), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.23,17.52753,104.6664,17.54249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6664,17.54249,101.23,17.52753), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.9189,17.56889,104.6664,17.54249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.9102,17.59638,105.8586,17.62055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.8586,17.62055,100.9102,17.59638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.785,17.65944,100.9625,17.66721), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.9625,17.66721,104.4444,17.67332), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.4444,17.67332,100.9625,17.66721), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.3905,17.69249,104.4444,17.67332), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.7364,17.71749,101.3905,17.69249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.9805,17.765,101.558,17.78472), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.558,17.78472,102.6666,17.8025), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.6666,17.8025,101.5591,17.81479), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.5591,17.81479,102.6666,17.8025), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0303,17.83611,102.5891,17.845), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.5891,17.845,101.0303,17.83611), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.6716,17.86194,105.615,17.87138), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.615,17.87138,102.6716,17.86194), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.6208,17.88194,105.615,17.87138), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.2608,17.88194,105.615,17.87138), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7269,17.90916,102.6127,17.9186), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.6127,17.9186,101.7269,17.90916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.6247,17.94777,102.5903,17.95777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.5903,17.95777,105.6247,17.94777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.0186,17.97861,102.5903,17.95777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9386,18.0036,103.0672,18.02499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.0672,18.02499,101.8903,18.03), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8903,18.03,103.0672,18.02499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.2933,18.05222,101.8316,18.05389), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8316,18.05389,101.7725,18.05444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7725,18.05444,101.8316,18.05389), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1908,18.05777,104.15,18.06083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.15,18.06083,101.1908,18.05777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.0901,18.13741,105.383,18.16027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.383,18.16027,103.1408,18.16527), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.1408,18.16527,105.383,18.16027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4997,18.17999,103.1408,18.16527), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.1764,18.19555,105.4336,18.20777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.4336,18.20777,102.1055,18.21027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.1055,18.21027,105.4336,18.20777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.1705,18.24777,101.1516,18.25777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1516,18.25777,103.1705,18.24777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.8686,18.27999,103.2825,18.29888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.2825,18.29888,103.9958,18.3075), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.9958,18.3075,103.2825,18.29888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.185,18.31833,103.9958,18.3075), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.95,18.33055,101.1805,18.3336), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1805,18.3336,103.95,18.33055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.2366,18.34666,103.6758,18.35194), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.6758,18.35194,103.2366,18.34666), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0916,18.37694,105.1916,18.37833), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1916,18.37833,101.0916,18.37694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.2816,18.40722,105.1319,18.40999), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1319,18.40999,103.2816,18.40722), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.3972,18.43499,101.0622,18.45444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0622,18.45444,105.1033,18.46693), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1033,18.46693,101.0622,18.45444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.14,18.52749,105.1033,18.46693), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1494,18.59972,105.1897,18.60249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1897,18.60249,105.1494,18.59972), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1938,18.64249,105.1897,18.60249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2689,18.68694,105.1344,18.70638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((105.1344,18.70638,101.2689,18.68694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2286,18.72916,104.9575,18.73888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.9575,18.73888,101.2286,18.72916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2544,18.78499,104.9575,18.73888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6722,18.83694,101.2544,18.78499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.5208,18.97943,104.4536,18.9811), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.4536,18.9811,104.5208,18.97943), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.3519,19.05166,101.2872,19.1075), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2872,19.1075,104.2239,19.13249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.2239,19.13249,101.2872,19.1075), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.9647,19.25361,103.8791,19.29343), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.8791,19.29343,103.9647,19.25361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2028,19.39083,103.9877,19.39916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.9877,19.39916,101.2028,19.39083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.0698,19.40983,103.9877,19.39916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.7753,19.48444,100.575,19.49361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.575,19.49361,104.1225,19.49722), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.1225,19.49722,100.575,19.49361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2852,19.52138,100.4844,19.54472), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.4844,19.54472,100.6366,19.55055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.6366,19.55055,100.4844,19.54472), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.19,19.56861,101.2694,19.57916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2694,19.57916,101.19,19.56861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2191,19.59555,104.523,19.60471), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.523,19.60471,104.0366,19.6111), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.0366,19.6111,104.645,19.61555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.645,19.61555,101.0322,19.61944), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0322,19.61944,100.8947,19.61971), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.8947,19.61971,101.0322,19.61944), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.1391,19.65999,104.3206,19.6615), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.3206,19.6615,104.1391,19.65999), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6616,19.66888,104.3206,19.6615), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.3086,19.68694,104.1641,19.68832), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.1641,19.68832,104.3086,19.68694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.4136,19.69333,104.0404,19.69444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.0404,19.69444,104.4136,19.69333), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.4061,19.76444,104.8364,19.79138), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.8364,19.79138,100.4061,19.76444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.8461,19.85332,100.4833,19.85805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.4833,19.85805,104.8461,19.85332), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7788,19.87416,100.4833,19.85805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.8525,19.94527,104.96,19.98693), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.96,19.98693,104.9808,20.0186), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.9808,20.0186,104.96,19.98693), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5205,20.14499,100.5811,20.15762), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5811,20.15762,100.5205,20.14499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.935,20.18555,100.4528,20.19333), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.4528,20.19333,104.935,20.18555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6805,20.21888,100.1597,20.23499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.1597,20.23499,104.6805,20.21888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.0972,20.2561,100.1597,20.23499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6788,20.28055,104.7139,20.29027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7139,20.29027,104.6788,20.28055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.3761,20.34416,104.7069,20.34555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.7069,20.34555,100.3761,20.34416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.0923,20.34929,104.7069,20.34555), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.0966,20.35833,100.0923,20.34929), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.25,20.37916,100.3086,20.3936), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.3086,20.3936,100.25,20.37916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.5105,20.40805,104.6025,20.41971), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6025,20.41971,104.5105,20.40805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.3886,20.43861,104.6025,20.41971), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.3813,20.46055,104.3886,20.43861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.5511,20.51638,104.4708,20.53722), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.4708,20.53722,104.5511,20.51638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.1861,20.65332,103.6872,20.65833), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.6872,20.65833,104.6436,20.66027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.6436,20.66027,103.6872,20.65833), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.7383,20.66999,104.5853,20.67388), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.5853,20.67388,103.7383,20.66999), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.7294,20.72416,103.7894,20.7486), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.7894,20.7486,103.7294,20.72416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.4169,20.79416,100.5158,20.80388), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5158,20.80388,103.4169,20.79416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5705,20.81777,103.4486,20.82777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.4486,20.82777,100.3761,20.82805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.3761,20.82805,103.4486,20.82777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.348,20.83444,100.3761,20.82805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.8036,20.84583,103.1664,20.85083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.1664,20.85083,103.8036,20.84583), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.538,20.86694,100.648,20.8736), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.648,20.8736,100.538,20.86694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5133,20.88777,100.6336,20.89138), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.6336,20.89138,100.5133,20.88777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.0769,20.95832,104.1091,20.97721), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((104.1091,20.97721,104.0769,20.95832), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.5455,21.02527,100.6091,21.04277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.6091,21.04277,103.0308,21.06), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((103.0308,21.06,100.6091,21.04277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9669,21.07861,103.0308,21.06), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7847,21.14277,101.7175,21.14805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7175,21.14805,101.7847,21.14277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2819,21.18027,101.6055,21.18221), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.6055,21.18221,101.2819,21.18027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9166,21.23444,101.605,21.23499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.605,21.23499,102.9166,21.23444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8511,21.24554,101.605,21.23499), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2344,21.25639,102.823,21.26083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.823,21.26083,101.2344,21.25639), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.8347,21.30249,102.9005,21.30277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9005,21.30277,100.8347,21.30249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7405,21.31361,100.7322,21.31416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((100.7322,21.31416,101.7405,21.31361), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2653,21.37611,101.0052,21.40083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.0052,21.40083,101.1936,21.41277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1936,21.41277,101.0052,21.40083), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.875,21.43277,101.1936,21.41277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9438,21.45471,102.875,21.43277), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1616,21.53055,102.9619,21.54416), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9619,21.54416,101.1616,21.53055), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.2089,21.55861,101.1524,21.56467), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1524,21.56467,101.2089,21.55861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1524,21.56467,101.2089,21.55861), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.1518,21.57086,101.1524,21.56467), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7997,21.58416,101.1518,21.57086), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8289,21.63055,102.6711,21.66249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.6711,21.66249,102.7469,21.66638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.7469,21.66638,102.6711,21.66249), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9889,21.68277,102.7469,21.66638), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.8614,21.71832,101.7525,21.72444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7525,21.72444,102.9394,21.72471), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.9394,21.72471,101.7525,21.72444), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.8175,21.7336,102.9394,21.72471), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.965,21.74888,102.8175,21.7336), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7847,21.82499,102.8583,21.82694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.8583,21.82694,102.8072,21.82805), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.8072,21.82805,102.8583,21.82694), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.8289,21.84777,102.6405,21.86305), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.6405,21.86305,102.8289,21.84777), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.5055,21.96221,101.6139,22.02888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.6139,22.02888,102.5055,21.96221), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.4194,22.12055,101.6139,22.02888), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.2675,22.21777,101.57,22.27916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.57,22.27916,101.6352,22.30527), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.6352,22.30527,101.57,22.27916), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8947,22.38416,101.8661,22.39027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8661,22.39027,102.1405,22.39589), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.1405,22.39589,101.8661,22.39027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((102.1058,22.43749,101.9947,22.4486), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.9947,22.4486,102.1058,22.43749), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.8233,22.46693,101.6894,22.47027), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.6894,22.47027,101.8233,22.46693), mapfile, tile_dir, 0, 11, "la-laos")
	render_tiles((101.7689,22.50083,101.6894,22.47027), mapfile, tile_dir, 0, 11, "la-laos")