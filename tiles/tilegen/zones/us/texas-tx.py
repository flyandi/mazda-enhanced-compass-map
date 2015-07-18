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
    # Zone: us
    # Region: Texas
    # Region Name: TX

	render_tiles((-97.37286,25.84012,-97.42264,25.84038), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.42264,25.84038,-97.37286,25.84012), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.36008,25.86887,-97.45473,25.87934), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.45473,25.87934,-97.49686,25.88006), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.49686,25.88006,-97.45473,25.87934), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.36598,25.90245,-97.54296,25.92004), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.54296,25.92004,-97.33835,25.92313), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.33835,25.92313,-97.54296,25.92004), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.27716,25.93544,-97.58257,25.93786), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.58257,25.93786,-97.27716,25.93544), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.15661,25.94902,-97.58257,25.93786), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.20695,25.9609,-97.14557,25.97113), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.14557,25.97113,-97.20695,25.9609), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.64401,26.00661,-97.15192,26.01765), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.15192,26.01765,-97.69707,26.02346), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.69707,26.02346,-97.15192,26.01765), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.75884,26.03213,-97.69707,26.02346), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.03924,26.04128,-97.75884,26.03213), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.79529,26.05522,-98.14946,26.05581), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.14946,26.05581,-98.19705,26.05615), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.19705,26.05615,-98.14946,26.05581), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.86228,26.05775,-97.87119,26.05808), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.87119,26.05808,-97.86228,26.05775), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.20495,26.05874,-98.09104,26.05917), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.09104,26.05917,-98.20495,26.05874), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.94435,26.05962,-98.09104,26.05917), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.01097,26.06386,-97.94435,26.05962), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.24881,26.0731,-98.01097,26.06386), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.1588,26.08266,-98.24881,26.0731), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.30298,26.11005,-98.3082,26.11303), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.3082,26.11303,-98.30298,26.11005), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.38669,26.15787,-98.44254,26.19915), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.44254,26.19915,-98.50349,26.2148), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.50349,26.2148,-98.44254,26.19915), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.57619,26.23522,-98.65422,26.23596), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.65422,26.23596,-98.57619,26.23522), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.5933,26.24294,-98.65422,26.23596), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.61347,26.25203,-98.5933,26.24294), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.69886,26.26562,-98.61347,26.25203), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.19694,26.30587,-98.77991,26.32654), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.77991,26.32654,-97.19694,26.30587), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.89097,26.35757,-98.80735,26.36942), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.80735,26.36942,-98.89097,26.35757), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.95833,26.39406,-99.082,26.39651), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.082,26.39651,-98.95833,26.39406), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.22738,26.4115,-99.03232,26.41208), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.03232,26.41208,-97.22738,26.4115), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.11086,26.42628,-99.03232,26.41208), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.09164,26.47698,-99.10503,26.50034), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.10503,26.50034,-97.2538,26.50316), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.2538,26.50316,-99.10503,26.50034), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.1714,26.54985,-99.17682,26.56966), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.17682,26.56966,-99.1714,26.54985), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.28754,26.60034,-99.17682,26.56966), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.20052,26.65644,-97.32275,26.70175), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.32275,26.70175,-99.20891,26.72476), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.20891,26.72476,-97.32275,26.70175), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.24244,26.78826,-99.26861,26.84321), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.26861,26.84321,-99.3289,26.87976), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.3289,26.87976,-97.36687,26.88558), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.36687,26.88558,-99.3289,26.87976), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.36114,26.92892,-97.36687,26.88558), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.38737,26.9824,-99.44697,27.02603), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.44697,27.02603,-97.3787,27.06004), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.3787,27.06004,-99.44697,27.02603), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.44212,27.10684,-97.3787,27.06004), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.42998,27.15915,-99.44212,27.10684), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.44524,27.22334,-97.35847,27.2348), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.35847,27.2348,-99.44524,27.22334), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.46083,27.26224,-99.46331,27.26844), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.46331,27.26844,-99.46083,27.26224), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.34685,27.27796,-99.46331,27.26844), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.48794,27.29494,-99.52965,27.30605), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.52965,27.30605,-99.48794,27.29494), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.33612,27.31782,-99.52965,27.30605), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.48752,27.4124,-97.29606,27.42718), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.29606,27.42718,-99.48752,27.4124), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.4951,27.45152,-97.29606,27.42718), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.52832,27.4989,-99.49752,27.5005), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.49752,27.5005,-99.52832,27.4989), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.25733,27.51064,-99.49752,27.5005), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.22299,27.57661,-99.53014,27.58021), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.53014,27.58021,-97.22299,27.57661), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.21268,27.59642,-99.53014,27.58021), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.55681,27.61434,-97.21268,27.59642), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.62452,27.63452,-99.55681,27.61434), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.7046,27.65495,-99.62452,27.63452), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.14085,27.71669,-99.75853,27.71707), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.75853,27.71707,-97.14085,27.71669), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.80165,27.74177,-99.75853,27.71707), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.84474,27.77881,-97.09074,27.78589), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.09074,27.78589,-99.84474,27.77881), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.87784,27.82438,-97.04485,27.83447), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.04485,27.83447,-97.04368,27.83653), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.04368,27.83653,-97.04485,27.83447), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.90439,27.87528,-97.00333,27.90831), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.00333,27.90831,-99.91746,27.91797), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.91746,27.91797,-97.00333,27.90831), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.93216,27.96771,-99.98492,27.99073), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.98492,27.99073,-99.93216,27.96771), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.88646,28.03073,-96.85207,28.05982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.85207,28.05982,-100.02873,28.07312), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.02873,28.07312,-96.85207,28.05982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.79216,28.1105,-100.07547,28.12488), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.07547,28.12488,-96.79216,28.1105), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.71963,28.16459,-100.17441,28.17945), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.17441,28.17945,-96.71963,28.16459), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.19751,28.197,-100.17441,28.17945), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.63201,28.22282,-100.19751,28.197), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.2676,28.25027,-96.63201,28.22282), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.28755,28.30109,-96.44285,28.31767), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.44285,28.31767,-100.28755,28.30109), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.32039,28.36212,-96.39038,28.38182), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.39038,28.38182,-96.37853,28.38987), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.37853,28.38987,-96.39038,28.38182), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.32882,28.42366,-100.33706,28.42715), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.33706,28.42715,-96.32882,28.42366), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.36829,28.4772,-96.19441,28.50222), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.19441,28.50222,-100.38886,28.51575), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.38886,28.51575,-96.19441,28.50222), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.39727,28.57564,-96.00068,28.58824), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.00068,28.58824,-100.39727,28.57564), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.44865,28.61677,-96.00068,28.58824), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.50035,28.66196,-95.8125,28.66494), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.8125,28.66494,-100.50035,28.66196), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.5067,28.71675,-95.68409,28.73404), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.68409,28.73404,-100.5067,28.71675), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.53302,28.76328,-95.5888,28.78317), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.5888,28.78317,-100.53302,28.76328), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.53583,28.80589,-95.50704,28.82474), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.50704,28.82474,-100.57685,28.83617), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.57685,28.83617,-95.50704,28.82474), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.43959,28.85902,-95.38239,28.86635), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.38239,28.86635,-95.43959,28.85902), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.62721,28.90373,-95.29715,28.93407), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.29715,28.93407,-100.64699,28.95708), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.64699,28.95708,-95.29715,28.93407), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.12675,28.98212,-103.28119,28.98214), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.28119,28.98214,-103.12675,28.98212), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.2278,28.99153,-103.28119,28.98214), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.362,29.01891,-95.19139,29.02309), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.19139,29.02309,-103.10037,29.02688), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.10037,29.02688,-95.19139,29.02309), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.66021,29.0315,-103.10037,29.02688), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.4632,29.06682,-95.12513,29.06732), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.12513,29.06732,-103.4632,29.06682), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.11505,29.07555,-100.67122,29.08352), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.67122,29.08352,-103.07636,29.08572), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.07636,29.08572,-100.67122,29.08352), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.67466,29.09978,-103.03568,29.10303), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.03568,29.10303,-100.67466,29.09978), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.52461,29.121,-100.72746,29.12912), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.72746,29.12912,-103.52461,29.121), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.02622,29.14806,-103.59236,29.15026), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.59236,29.15026,-95.02622,29.14806), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.99569,29.16122,-100.77265,29.16849), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.77265,29.16849,-103.6602,29.17093), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.6602,29.17093,-100.77265,29.16849), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.91781,29.1907,-103.72474,29.19147), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.72474,29.19147,-102.91781,29.1907), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.79568,29.22773,-100.80187,29.23283), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.80187,29.23283,-100.79568,29.22773), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.87135,29.24163,-100.80187,29.23283), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.78903,29.2575,-103.79387,29.25924), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.79387,29.25924,-103.78903,29.2575), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.84866,29.27142,-94.8037,29.27924), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.8037,29.27924,-103.85689,29.28185), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.85689,29.28185,-94.8037,29.27924), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.89102,29.28711,-103.85689,29.28185), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.97524,29.29602,-102.89102,29.28711), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.88684,29.30785,-103.97524,29.29602), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.0556,29.33091,-94.72253,29.33145), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.72253,29.33145,-104.0556,29.33091), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.87186,29.35209,-100.99561,29.3634), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.99561,29.3634,-94.73105,29.36914), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.73105,29.36914,-100.99561,29.3634), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.14369,29.38328,-94.73105,29.36914), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.82456,29.39956,-104.14369,29.38328), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.18127,29.42627,-94.67039,29.43078), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.67039,29.43078,-104.18127,29.42627), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.83097,29.44427,-94.67039,29.43078), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.06015,29.45866,-94.59485,29.4679), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.59485,29.4679,-101.1375,29.47354), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.1375,29.47354,-94.59485,29.4679), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.22908,29.48105,-101.1375,29.47354), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.50081,29.50537,-101.19272,29.52029), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.19272,29.52029,-101.2549,29.52034), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.2549,29.52034,-101.19272,29.52029), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.80869,29.52232,-101.2549,29.52034), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.30881,29.52434,-102.80869,29.52232), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.37118,29.54306,-94.37082,29.55565), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.37082,29.55565,-102.77753,29.5565), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.77753,29.5565,-94.37082,29.55565), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.35412,29.5621,-102.77753,29.5565), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.30553,29.57793,-94.35412,29.5621), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.4523,29.60366,-102.73843,29.62193), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.73843,29.62193,-94.16155,29.63659), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.16155,29.63659,-101.30733,29.64072), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.30733,29.64072,-94.16155,29.63659), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.3672,29.66404,-94.05651,29.67116), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.05651,29.67116,-104.53976,29.67607), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.53976,29.67607,-102.69347,29.67651), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.69347,29.67651,-104.53976,29.67607), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.86129,29.67901,-102.69347,29.67651), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.96187,29.68221,-93.86129,29.67901), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.83797,29.69062,-93.96187,29.68221), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.8632,29.72406,-101.40064,29.73808), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.40064,29.73808,-102.67719,29.73826), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.67719,29.73826,-101.40064,29.73808), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.61288,29.74818,-102.55108,29.75236), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.55108,29.75236,-102.61288,29.74818), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.4535,29.75967,-93.89082,29.76167), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.89082,29.76167,-101.4535,29.75967), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.50322,29.76458,-101.65458,29.76516), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.65458,29.76516,-102.39291,29.76557), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.39291,29.76557,-101.65458,29.76516), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.71422,29.76766,-102.39291,29.76557), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.56569,29.77046,-101.71422,29.76766), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.76162,29.77886,-102.46895,29.77982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.46895,29.77982,-102.51269,29.7803), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.51269,29.7803,-102.46895,29.77982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.07365,29.78693,-101.80944,29.79016), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.80944,29.79016,-102.11568,29.79239), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.11568,29.79239,-101.8754,29.79402), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.8754,29.79402,-101.56157,29.79466), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.56157,29.79466,-101.8754,29.79402), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.02192,29.80249,-93.92921,29.80295), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.92921,29.80295,-102.02192,29.80249), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.96617,29.80734,-93.92921,29.80295), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.16167,29.81949,-102.36952,29.8204), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.36952,29.8204,-102.16167,29.81949), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.22755,29.84353,-104.61904,29.84445), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.61904,29.84445,-102.22755,29.84353), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.87245,29.85165,-104.61904,29.84445), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.34986,29.86232,-93.85231,29.87209), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.85231,29.87209,-102.31868,29.87219), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.31868,29.87219,-93.85231,29.87209), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.30138,29.87767,-102.31868,29.87219), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.83037,29.89436,-102.30138,29.87767), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.67233,29.91111,-93.83037,29.89436), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.80782,29.95455,-104.68548,29.98994), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.68548,29.98994,-93.74108,30.02157), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.74108,30.02157,-104.704,30.02421), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.704,30.02421,-93.74108,30.02157), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70634,30.05218,-93.70394,30.05429), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70394,30.05429,-93.70634,30.05218), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.69209,30.1073,-93.70244,30.11272), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70244,30.11272,-104.69209,30.1073), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70376,30.17394,-104.70279,30.21174), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.70279,30.21174,-93.71336,30.22526), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.71336,30.22526,-104.70279,30.21174), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.71106,30.24397,-104.74045,30.25945), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.74045,30.25945,-93.71106,30.24397), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70719,30.27551,-104.74045,30.25945), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.76163,30.30115,-93.70719,30.27551), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.76033,30.32992,-104.76163,30.30115), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.82431,30.37047,-104.85952,30.39041), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.85952,30.39041,-93.74533,30.39702), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.74533,30.39702,-93.73854,30.40226), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.73854,30.40226,-93.74533,30.39702), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.70267,30.42995,-93.73854,30.40226), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.86987,30.45865,-93.70267,30.42995), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.71012,30.5064,-104.88938,30.53514), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.88938,30.53514,-93.7292,30.54484), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.7292,30.54484,-104.88938,30.53514), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.68433,30.59259,-104.9248,30.60483), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.9248,30.60483,-104.97207,30.61026), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.97207,30.61026,-104.9248,30.60483), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.68512,30.6252,-104.98075,30.62881), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.98075,30.62881,-93.68512,30.6252), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.00124,30.67258,-93.6299,30.67994), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.6299,30.67994,-105.06233,30.6863), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.06233,30.6863,-93.6299,30.67994), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.09828,30.71891,-93.61769,30.73848), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.61769,30.73848,-105.16015,30.75706), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.16015,30.75706,-93.61769,30.73848), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.21866,30.80157,-93.5693,30.80297), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.5693,30.80297,-105.21866,30.80157), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.31486,30.81696,-93.5693,30.80297), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.39424,30.85298,-93.55862,30.86942), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.55862,30.86942,-93.55458,30.87747), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.55458,30.87747,-93.55862,30.86942), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.39961,30.88894,-93.55458,30.87747), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.53094,30.92453,-105.48803,30.94328), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.48803,30.94328,-93.53094,30.92453), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.54984,30.96712,-105.55743,30.99023), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.55743,30.99023,-93.53953,31.0085), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.53953,31.0085,-105.55743,30.99023), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.57954,31.0354,-93.53122,31.05168), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.53122,31.05168,-105.57954,31.0354), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.62735,31.09855,-93.54028,31.12887), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.54028,31.12887,-105.70949,31.13638), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.70949,31.13638,-93.54028,31.12887), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.77326,31.1669,-93.60244,31.18254), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.60244,31.18254,-93.6006,31.18262), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.6006,31.18262,-93.60244,31.18254), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.5525,31.18482,-93.5351,31.18561), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.5351,31.18561,-93.5525,31.18482), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.79439,31.20224,-93.5351,31.18561), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.61394,31.25938,-105.86935,31.28863), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.86935,31.28863,-93.67544,31.30104), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.67544,31.30104,-105.86935,31.28863), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.93845,31.31874,-93.67544,31.30104), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.95394,31.36475,-93.66815,31.3751), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.66815,31.3751,-105.95394,31.36475), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.99643,31.38784,-106.00493,31.39246), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.00493,31.39246,-105.99643,31.38784), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.08026,31.3987,-106.00493,31.39246), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.6976,31.42841,-106.17568,31.45628), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.17568,31.45628,-93.74948,31.46869), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.74948,31.46869,-106.17568,31.45628), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.72593,31.50409,-106.2368,31.51338), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.2368,31.51338,-93.72593,31.50409), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.78769,31.52734,-106.2368,31.51338), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.28081,31.56206,-93.83492,31.58621), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.83492,31.58621,-106.28081,31.56206), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.83492,31.58621,-106.28081,31.56206), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.30354,31.62041,-93.81684,31.62251), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.81684,31.62251,-106.30354,31.62041), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.35261,31.68695,-93.80342,31.70069), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.80342,31.70069,-106.37014,31.71071), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.37014,31.71071,-93.80342,31.70069), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.48464,31.74781,-106.41794,31.75201), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.41794,31.75201,-106.48464,31.74781), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.46764,31.75961,-106.41794,31.75201), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.52824,31.78315,-93.85339,31.80547), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.85339,31.80547,-106.58134,31.81391), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.58134,31.81391,-93.85339,31.80547), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.87825,31.84428,-106.63588,31.87151), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.63588,31.87151,-93.90956,31.89314), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.90956,31.89314,-106.62345,31.91403), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.62345,31.91403,-93.97746,31.92642), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.62345,31.91403,-93.97746,31.92642), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-93.97746,31.92642,-106.62345,31.91403), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.63011,31.97126,-94.02943,31.97969), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.02943,31.97969,-106.63011,31.97126), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04183,31.9924,-104.02452,32.00001), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.02452,32.00001,-103.98021,32.00003), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.98021,32.00003,-104.02452,32.00001), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.72285,32.00017,-103.98021,32.00003), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.3265,32.00037,-104.64353,32.00044), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.64353,32.00044,-104.84776,32.00046), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.84776,32.00046,-104.9183,32.00047), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-104.9183,32.00047,-104.84776,32.00046), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.15399,32.0005,-103.06442,32.00052), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.61849,32.0005,-103.06442,32.00052), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06442,32.00052,-105.15399,32.0005), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.37717,32.00124,-106.2007,32.00179), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-106.2007,32.00179,-105.99797,32.00197), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.99797,32.00197,-106.2007,32.00179), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-105.75053,32.00221,-105.99797,32.00197), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06442,32.08705,-94.04268,32.13796), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04268,32.13796,-103.06442,32.14501), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06442,32.14501,-94.04268,32.13796), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.0427,32.196,-103.06442,32.14501), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04274,32.36356,-94.04279,32.39228), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04279,32.39228,-94.04274,32.36356), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.0647,32.52219,-94.04308,32.56426), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04308,32.56426,-103.06476,32.58798), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06476,32.58798,-94.04308,32.56426), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04305,32.69303,-94.04303,32.79748), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04303,32.79748,-103.06489,32.84936), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06489,32.84936,-94.043,32.88109), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.043,32.88109,-103.06489,32.84936), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.06347,32.9591,-94.04296,33.01922), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04296,33.01922,-103.06347,32.9591), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04272,33.16029,-103.0601,33.21923), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.0601,33.21923,-94.04295,33.27124), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04295,33.27124,-103.0601,33.21923), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04307,33.3305,-103.0565,33.38841), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.0565,33.38841,-94.04299,33.43582), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04299,33.43582,-103.0565,33.38841), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04343,33.55143,-94.04383,33.55171), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.04383,33.55171,-94.04343,33.55143), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.35417,33.55645,-94.04383,33.55171), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.30374,33.56449,-94.38805,33.56551), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.38805,33.56551,-94.30374,33.56449), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.33842,33.56708,-94.38805,33.56551), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.05261,33.5706,-94.21361,33.57062), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.21361,33.57062,-103.05261,33.5706), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.07267,33.57223,-94.21361,33.57062), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.23887,33.57672,-94.41906,33.57722), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.41906,33.57722,-94.23887,33.57672), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.14302,33.57773,-94.41906,33.57722), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.1834,33.59221,-94.14302,33.57773), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.52893,33.62184,-94.48588,33.63787), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.48588,33.63787,-94.52893,33.62184), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.57287,33.66989,-94.63059,33.6734), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.63059,33.6734,-94.57287,33.66989), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.36314,33.69422,-94.71487,33.70726), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.71487,33.70726,-96.37966,33.71553), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.37966,33.71553,-94.73193,33.72083), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.73193,33.72083,-97.14939,33.72197), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.14939,33.72197,-94.73193,33.72083), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.30739,33.73501,-97.09107,33.73512), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.09107,33.73512,-96.30739,33.73501), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.84163,33.73943,-97.09107,33.73512), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.40351,33.74629,-96.22902,33.74802), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.22902,33.74802,-94.76615,33.74803), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.76615,33.74803,-96.22902,33.74802), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.27727,33.76974,-96.50229,33.77346), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.50229,33.77346,-97.08785,33.7741), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.08785,33.7741,-96.50229,33.77346), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.90228,33.77629,-97.08785,33.7741), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.43646,33.78005,-94.90228,33.77629), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.17303,33.80056,-97.20565,33.80982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.20565,33.80982,-94.93956,33.8105), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.93956,33.8105,-97.20565,33.80982), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.07859,33.81276,-94.93956,33.8105), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.52386,33.81811,-96.57294,33.8191), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.57294,33.8191,-97.37294,33.81945), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.37294,33.81945,-96.57294,33.8191), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.44419,33.82377,-103.04735,33.82468), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04735,33.82468,-97.44419,33.82377), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.59293,33.83092,-96.71242,33.83163), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.71242,33.83163,-96.15163,33.83195), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.15163,33.83195,-96.71242,33.83163), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.06392,33.84152,-96.77677,33.84198), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.77677,33.84198,-96.06392,33.84152), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.85059,33.84721,-97.16663,33.84731), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.16663,33.84731,-96.85059,33.84721), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.86577,33.84939,-97.16663,33.84731), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-94.98165,33.85228,-97.86577,33.84939), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.05584,33.85574,-95.8206,33.85847), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.8206,33.85847,-95.84488,33.86042), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.84488,33.86042,-95.8206,33.85847), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.04657,33.86257,-95.88749,33.86386), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.88749,33.86386,-97.31824,33.86512), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.31824,33.86512,-95.88749,33.86386), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.35234,33.86779,-95.44737,33.86885), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.44737,33.86885,-96.79428,33.86889), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.79428,33.86889,-95.44737,33.86885), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.45147,33.87093,-95.78964,33.87244), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.78964,33.87244,-95.31045,33.87384), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.31045,33.87384,-95.93533,33.8751), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.93533,33.8751,-95.31045,33.87384), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.6821,33.87665,-95.28345,33.87775), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.28345,33.87775,-97.95122,33.87842), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.95122,33.87842,-95.28345,33.87775), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.80347,33.88019,-96.59011,33.88067), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.59011,33.88067,-97.80347,33.88019), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.59467,33.88302,-96.59011,33.88067), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.52532,33.88549,-96.98557,33.88652), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.98557,33.88652,-95.52532,33.88549), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.95191,33.89123,-96.98557,33.88652), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.73751,33.89597,-97.55827,33.8971), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.55827,33.8971,-95.73751,33.89597), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.56124,33.89906,-97.24618,33.90034), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.24618,33.90034,-97.56124,33.89906), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.89719,33.90295,-95.095,33.90482), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.095,33.90482,-95.66998,33.90584), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.66998,33.90584,-95.095,33.90482), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.48414,33.91389,-97.20614,33.91428), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.20614,33.91428,-97.48414,33.91389), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.6599,33.91667,-97.48651,33.91699), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.48651,33.91699,-96.6599,33.91667), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.98875,33.91847,-97.48651,33.91699), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.59616,33.92211,-97.9537,33.92437), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.9537,33.92437,-97.75983,33.92521), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.75983,33.92521,-97.9537,33.92437), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.55692,33.92702,-95.60366,33.9272), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.60366,33.9272,-95.55692,33.92702), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.25362,33.92971,-95.60366,33.9272), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.14946,33.93634,-95.15591,33.93848), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.15591,33.93848,-95.14946,33.93634), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.95231,33.94458,-96.94462,33.94501), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.94462,33.94501,-96.95231,33.94458), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.93434,33.94559,-96.94462,33.94501), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-96.90525,33.94722,-96.93434,33.94559), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-95.22639,33.96195,-97.60909,33.96809), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.60909,33.96809,-95.22639,33.96195), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.94757,33.99105,-97.67177,33.99137), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-97.67177,33.99137,-97.94757,33.99105), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.00567,33.99596,-97.67177,33.99137), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.08284,34.00241,-98.00567,33.99596), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.0991,34.04864,-98.47507,34.06427), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.47507,34.06427,-103.04352,34.07938), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04352,34.07938,-98.42353,34.08195), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.42353,34.08195,-103.04352,34.07938), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.41443,34.08507,-98.42353,34.08195), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.5282,34.09496,-98.09933,34.1043), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.09933,34.1043,-103.04356,34.11283), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04356,34.11283,-98.16912,34.11417), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.16912,34.11417,-103.04356,34.11283), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.22528,34.12725,-98.39844,34.12846), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.39844,34.12846,-98.22528,34.12725), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.73723,34.13099,-98.69007,34.13316), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.69007,34.13316,-98.73723,34.13099), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.13849,34.14121,-98.31875,34.14642), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.31875,34.14642,-98.57714,34.14896), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.57714,34.14896,-98.31875,34.14642), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.12338,34.15454,-98.80681,34.1559), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.80681,34.1559,-98.61035,34.15621), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.61035,34.15621,-98.80681,34.1559), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.36402,34.15711,-98.61035,34.15621), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.87223,34.16045,-98.36402,34.15711), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.64807,34.16444,-98.87223,34.16045), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.0588,34.20126,-98.94022,34.20369), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.94022,34.20369,-98.95232,34.20467), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-98.95232,34.20467,-98.94022,34.20369), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.00292,34.20878,-99.13155,34.20935), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.13155,34.20935,-99.00292,34.20878), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.18951,34.21431,-99.13155,34.20935), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04384,34.30262,-103.04385,34.31275), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04385,34.31275,-99.2116,34.31397), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.2116,34.31397,-103.04385,34.31275), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.22161,34.32537,-99.2116,34.31397), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.60003,34.37469,-103.04395,34.37956), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04395,34.37956,-99.42043,34.38046), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.42043,34.38046,-99.69646,34.38104), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.69646,34.38104,-99.42043,34.38046), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.27534,34.3866,-99.69646,34.38104), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.47097,34.39647,-99.47502,34.39687), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.47502,34.39687,-99.47097,34.39647), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.58448,34.40767,-99.47502,34.39687), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.76488,34.43527,-99.35041,34.43708), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.35041,34.43708,-99.76488,34.43527), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.39496,34.4421,-99.35041,34.43708), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.81819,34.48784,-99.84206,34.50693), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.84206,34.50693,-99.81819,34.48784), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00038,34.56051,-99.99763,34.56114), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.99763,34.56114,-100.00038,34.56051), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-99.92933,34.57671,-99.99763,34.56114), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04307,34.61978,-99.92933,34.57671), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00038,34.74636,-103.04277,34.74736), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04277,34.74736,-100.00038,34.74636), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04274,34.9541,-100.00038,35.03038), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00038,35.03038,-103.04274,34.9541), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04271,35.14474,-100.00039,35.1827), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00039,35.1827,-103.04262,35.18316), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04262,35.18316,-100.00039,35.1827), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00039,35.42236,-100.00039,35.61912), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00039,35.61912,-103.04155,35.62249), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04155,35.62249,-100.00039,35.61912), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04136,35.73927,-103.04155,35.62249), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.0004,35.88095,-103.04136,35.73927), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04082,36.05523,-100.0004,36.05568), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.0004,36.05568,-103.04082,36.05523), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.08516,36.49924,-100.59261,36.49947), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.59261,36.49947,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.54615,36.49951,-100.95415,36.49953), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.95415,36.49953,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.62392,36.49953,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-101.82657,36.49965,-100.88417,36.49968), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.88417,36.49968,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.31102,36.49969,-100.00041,36.4997), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00041,36.4997,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-100.00376,36.4997,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.03234,36.50007,-102.16246,36.50033), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-102.16246,36.50033,-103.00243,36.5004), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.00243,36.5004,-103.04192,36.50044), mapfile, tile_dir, 0, 11, "texas-tx")
	render_tiles((-103.04192,36.50044,-103.00243,36.5004), mapfile, tile_dir, 0, 11, "texas-tx")