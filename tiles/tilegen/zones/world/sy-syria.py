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
    # Region: SY
    # Region Name: Syria

	render_tiles((36.83776,32.3136,36.67471,32.3436), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.67471,32.3436,36.83776,32.3136), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.40026,32.38194,36.67471,32.3436), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.32499,32.4511,37.09859,32.47027), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.09859,32.47027,36.32499,32.4511), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.08027,32.53888,37.09859,32.47027), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.36749,32.61804,35.63345,32.68651), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.63345,32.68651,35.93998,32.69916), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.93998,32.69916,35.63345,32.68651), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.59917,32.71503,35.93998,32.69916), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.7936,32.74415,35.63165,32.74999), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.63165,32.74999,35.7936,32.74415), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.6361,32.76388,35.63165,32.74999), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.6394,32.81448,37.6361,32.76388), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.61482,32.89716,37.90637,32.90943), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.90637,32.90943,35.61482,32.89716), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.62248,32.98776,35.59054,33.01776), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.59054,33.01776,35.62248,32.98776), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.63943,33.04971,38.17776,33.05387), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.17776,33.05387,35.63943,33.04971), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.44971,33.19748,35.61928,33.24866), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.61928,33.24866,35.66971,33.25166), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.66971,33.25166,35.61928,33.24866), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.72276,33.34054,35.81054,33.36082), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.81054,33.36082,38.79455,33.37723), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.79455,33.37723,35.81054,33.36082), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.82443,33.40221,38.79455,33.37723), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.05915,33.57943,36.05276,33.59804), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.05276,33.59804,36.05915,33.57943), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.93498,33.6461,36.05276,33.59804), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.97109,33.71887,35.93498,33.6461), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.38251,33.83331,36.1111,33.83471), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.1111,33.83471,36.38251,33.83331), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.28333,33.91776,36.1111,33.83471), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.42082,34.05165,40.27748,34.07777), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((40.27748,34.07777,36.42082,34.05165), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.62498,34.20387,36.58971,34.23804), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.58971,34.23804,36.62498,34.20387), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.60054,34.30943,36.55915,34.3236), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.55915,34.3236,36.60054,34.30943), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.00388,34.41943,36.55109,34.42609), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.55109,34.42609,41.00388,34.41943), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.42443,34.50166,36.33887,34.51971), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.33887,34.51971,36.42443,34.50166), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.42915,34.60693,36.37804,34.6386), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.37804,34.6386,35.97072,34.64951), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.15359,34.6386,35.97072,34.64951), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.97072,34.64951,36.37804,34.6386), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.30554,34.66805,36.30859,34.68276), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.30859,34.68276,36.34443,34.68915), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.34443,34.68915,36.30859,34.68276), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.93304,34.73554,36.34443,34.68915), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.22915,34.78832,35.93304,34.73554), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.87471,34.90833,35.89657,35.00576), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.89657,35.00576,35.87471,34.90833), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.92499,35.1711,41.21137,35.1947), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.21137,35.1947,35.95943,35.19832), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.95943,35.19832,41.21137,35.1947), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.91999,35.4211,41.27776,35.49554), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.27776,35.49554,35.91999,35.4211), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.72527,35.5736,35.78304,35.62276), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.78304,35.62276,41.38387,35.62526), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.38387,35.62526,35.78304,35.62276), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.80221,35.68193,41.38387,35.62526), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.84554,35.74165,35.80221,35.68193), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.17416,35.82166,41.37804,35.83859), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.37804,35.83859,35.79916,35.84388), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.79916,35.84388,35.85666,35.84471), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.85666,35.84471,35.79916,35.84388), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.0172,35.87832,35.85666,35.84471), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((35.92324,35.92677,36.00582,35.93054), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.00582,35.93054,35.92324,35.92677), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.21915,35.96027,36.00582,35.93054), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.37389,36.01321,41.25499,36.05499), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.25499,36.05499,36.37389,36.01321), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.44082,36.20638,36.38251,36.22286), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.38251,36.22286,36.55499,36.22304), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.55499,36.22304,36.38251,36.22286), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.4936,36.23081,36.55499,36.22304), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.69221,36.23915,36.4936,36.23081), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.69026,36.28638,36.69221,36.23915), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.29027,36.35555,36.61859,36.35749), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.61859,36.35749,41.29027,36.35555), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.58082,36.40054,36.61859,36.35749), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.5536,36.4986,41.40304,36.52554), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.40304,36.52554,36.5536,36.4986), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.83526,36.59887,37.07693,36.62165), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.07693,36.62165,37.44248,36.63998), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.44248,36.63998,37.02443,36.65804), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.02443,36.65804,37.12748,36.65915), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.12748,36.65915,37.50555,36.65971), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.50555,36.65971,37.12748,36.65915), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((39.20582,36.66553,37.50555,36.65971), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.86054,36.69776,39.43923,36.69884), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((39.43923,36.69884,38.86054,36.69776), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.72637,36.70221,39.43923,36.69884), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.04305,36.71443,38.72637,36.70221), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((39.80998,36.75138,37.8186,36.76082), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((37.8186,36.76082,39.80998,36.75138), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.95554,36.7711,37.8186,36.76082), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((36.66026,36.8336,40.04832,36.83554), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((40.04832,36.83554,36.66026,36.8336), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.39082,36.89665,38.1811,36.90582), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((38.1811,36.90582,38.39082,36.89665), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((40.40026,36.99026,42.37385,37.05964), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.37385,37.05964,41.40054,37.07693), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.40054,37.07693,42.37385,37.05964), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.1411,37.09443,42.36609,37.11018), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.36609,37.11018,40.77554,37.11832), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.36609,37.11018,40.77554,37.11832), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((40.77554,37.11832,42.36609,37.11018), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((41.84026,37.12998,40.77554,37.11832), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.0636,37.19665,42.34526,37.23859), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.34526,37.23859,42.24971,37.27887), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.24971,37.27887,42.34526,37.23859), mapfile, tile_dir, 0, 11, "sy-syria")
	render_tiles((42.20787,37.32218,42.24971,37.27887), mapfile, tile_dir, 0, 11, "sy-syria")