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
    # Region: SA
    # Region Name: Saudi Arabia

	render_tiles((56.21471,25.61527,56.2719,25.6349), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.2719,25.6349,56.21471,25.61527), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.2719,25.6349,56.21471,25.61527), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.1436,25.6761,56.2719,25.6349), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.30166,25.74722,56.1436,25.6761), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.37193,25.84055,56.30166,25.74722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.4486,25.94555,56.39915,25.97611), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.39915,25.97611,56.4486,25.94555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.18554,26.01472,56.42582,26.01777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.42582,26.01777,56.18554,26.01472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.38554,26.04666,56.08121,26.06644), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.08121,26.06644,56.46693,26.08278), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.46693,26.08278,56.15887,26.0836), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.15887,26.0836,56.46693,26.08278), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.09332,26.10027,56.38943,26.105), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.38943,26.105,56.09332,26.10027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.46333,26.14194,56.36638,26.14305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.36638,26.14305,56.46333,26.14194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.3211,26.16416,56.36638,26.14305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.29694,26.1936,56.37225,26.20596), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.37225,26.20596,56.23444,26.21805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.23444,26.21805,56.40582,26.22083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.40582,26.22083,56.23444,26.21805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.30721,26.22694,56.40582,26.22083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.48193,26.23916,56.30721,26.22694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.20999,26.26555,56.4036,26.27999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.4036,26.27999,56.20999,26.26555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.51305,26.31694,56.46638,26.32), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.46638,26.32,56.51305,26.31694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.40833,26.34361,56.50332,26.35111), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.50332,26.35111,56.40833,26.34361), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.36665,26.3825,56.50332,26.35111), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((45.59082,15.10555,42.79027,16.37722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.79027,16.37722,43.06666,16.54944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.06666,16.54944,42.72027,16.56721), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.72027,16.56721,43.06666,16.54944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.7386,16.66916,43.20609,16.67221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.20609,16.67221,42.7386,16.66916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.10249,16.67944,43.20609,16.67221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.70832,16.70388,43.10249,16.67944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.18526,16.75222,43.23054,16.77666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.23054,16.77666,43.18526,16.75222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.22803,16.80972,43.23054,16.77666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.15359,16.8486,42.53915,16.87499), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.53915,16.87499,43.15359,16.8486), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.54804,17.00333,42.43665,17.06472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.43665,17.06472,42.3636,17.09333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.3636,17.09333,42.43665,17.06472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.41388,17.15749,42.35805,17.17611), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.35805,17.17611,42.41388,17.15749), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.17304,17.2111,42.35805,17.17611), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.27387,17.26361,43.2561,17.30722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.2561,17.30722,43.95693,17.30805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.95693,17.30805,43.2561,17.30722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.90971,17.31333,43.95693,17.30805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.18054,17.3325,44.11471,17.34305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.11471,17.34305,43.79221,17.3461), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.79221,17.3461,44.11471,17.34305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.90971,17.35888,43.79221,17.3461), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.02387,17.37499,43.90971,17.35888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.7036,17.39499,44.49887,17.39582), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.49887,17.39582,43.7036,17.39499), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.31749,17.40805,44.17332,17.41194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.17332,17.41194,44.34054,17.41388), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.34054,17.41388,44.17332,17.41194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.27082,17.42555,44.34054,17.41388), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.27693,17.45611,43.27082,17.42555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.52637,17.51667,43.44915,17.5275), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.44915,17.5275,43.52637,17.51667), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((47.46194,17.57415,43.44915,17.5275), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.93526,17.76027,41.78526,17.83222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.78526,17.83222,41.93526,17.76027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.64888,18.01361,41.59193,18.12777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.59193,18.12777,41.49527,18.22166), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.49527,18.22166,41.5086,18.26999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.5086,18.26999,41.46999,18.27944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.46999,18.27944,41.5086,18.26999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.44415,18.3975,41.46999,18.27944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.35416,18.56694,41.29193,18.57249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.29193,18.57249,41.35416,18.56694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.20554,18.70027,41.29193,18.57249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.25054,18.8311,41.21721,18.84833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.21721,18.84833,41.25054,18.8311), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.1336,18.95277,51.99915,18.99888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((51.99915,18.99888,41.1336,18.95277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.17443,19.07222,41.05916,19.14333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.05916,19.14333,41.17443,19.07222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.0336,19.26221,40.95249,19.33249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.95249,19.33249,41.0336,19.26221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.94249,19.50305,40.79804,19.605), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.79804,19.605,40.75888,19.6086), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.75888,19.6086,40.79804,19.605), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.78944,19.71277,40.6436,19.77388), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.6436,19.77388,40.72804,19.79194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.72804,19.79194,40.65471,19.79361), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.65471,19.79361,40.72804,19.79194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.52138,19.96972,40.33554,20.0736), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.33554,20.0736,40.52138,19.96972), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.01138,20.2725,40.09444,20.27305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.09444,20.27305,40.01138,20.2725), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.76471,20.34638,40.09444,20.27305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.57058,20.56553,39.51888,20.67805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.51888,20.67805,39.42915,20.76027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.42915,20.76027,39.42582,20.80722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.42582,20.80722,39.38999,20.83888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.38999,20.83888,39.42582,20.80722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.33443,20.92666,39.38999,20.83888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.01139,21.23065,39.08193,21.31305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.08193,21.31305,56.01139,21.23065), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.17027,21.40416,39.08193,21.31305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.15971,21.50444,39.10999,21.51833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.10999,21.51833,39.15971,21.50444), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.07526,21.68749,39.06248,21.72388), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.06248,21.72388,39.11416,21.74083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.11416,21.74083,39.06248,21.72388), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.03387,21.83777,39.00082,21.86777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.00082,21.86777,39.03387,21.83777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.99249,21.90722,38.94499,21.91138), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.94499,21.91138,38.99249,21.90722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((55.50226,22.05716,39.01916,22.13444), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.01916,22.13444,55.50226,22.05716), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((56.01139,22.28859,39.0886,22.3736), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((55.69401,22.28859,39.0886,22.3736), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.0886,22.3736,39.14499,22.40361), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.14499,22.40361,39.0886,22.3736), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((52.09041,22.52001,39.0786,22.57027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.0786,22.57027,52.09041,22.52001), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.97887,22.72499,38.9686,22.74805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.9686,22.74805,39.01749,22.76249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.01749,22.76249,38.9686,22.74805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.96471,22.78944,39.01749,22.76249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.9486,22.84527,38.96138,22.86833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.96138,22.86833,38.9011,22.87694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.9011,22.87694,38.96138,22.86833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.85832,22.91527,38.9011,22.87694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.89304,22.96249,38.81194,22.97861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.81194,22.97861,38.89304,22.96249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.7811,23.03833,38.81194,22.97861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.80249,23.09889,38.7811,23.03833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.77082,23.17555,38.80249,23.09889), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.55916,23.54111,38.59888,23.56083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.59888,23.56083,38.55916,23.54111), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((51.49532,23.60439,38.59888,23.56083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.44249,23.79361,38.30638,23.89027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.30638,23.89027,38.44249,23.79361), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.91749,24.16027,37.8961,24.16972), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.8961,24.16972,37.84277,24.17416), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.84277,24.17416,37.8961,24.16972), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.89027,24.20555,37.9336,24.22028), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.9336,24.22028,37.89027,24.20555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.59888,24.25,50.84734,24.25238), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.84734,24.25238,37.59888,24.25), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.51582,24.28833,37.66888,24.29916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.66888,24.29916,37.51582,24.28833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.42138,24.49722,37.66888,24.29916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.77693,24.71972,37.22943,24.72778), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.22943,24.72778,50.77693,24.71972), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.8261,24.74751,37.22943,24.72778), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.18638,24.78944,37.22638,24.80083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.22638,24.80083,37.18638,24.78944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.73166,24.82722,37.21249,24.83916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.21249,24.83916,37.15137,24.84666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.15137,24.84666,37.21249,24.83916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.25555,24.86555,37.15137,24.84666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.2786,24.97583,50.65221,24.98138), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.65221,24.98138,37.2786,24.97583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.58305,25.06277,50.65221,24.98138), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.23554,25.1825,50.51582,25.22833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.51582,25.22833,37.23554,25.1825), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.12027,25.30222,50.51582,25.22833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.48471,25.40889,37.07499,25.44083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.07499,25.44083,50.36721,25.4561), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.36721,25.4561,37.07499,25.44083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.37999,25.51333,50.36721,25.4561), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.25416,25.62694,50.18388,25.70055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.18388,25.70055,36.80027,25.71694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.80027,25.71694,50.21333,25.73333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.21333,25.73333,36.82332,25.74833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.82332,25.74833,36.71888,25.75249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.71888,25.75249,36.79082,25.75528), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.79082,25.75528,36.71888,25.75249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.11777,25.8586,36.65332,25.86166), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.65332,25.86166,50.11777,25.8586), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.70249,25.94472,50.11471,25.97638), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.11471,25.97638,50.01138,25.99222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.01138,25.99222,49.99249,25.99666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.99249,25.99666,36.7061,26.00083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.7061,26.00083,49.99249,25.99666), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.02999,26.03333,50.12916,26.03777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.12916,26.03777,50.02999,26.03333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.67082,26.04694,50.12916,26.03777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.58665,26.0611,36.67082,26.04694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.49944,26.1236,50.16026,26.13583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.16026,26.13583,49.97943,26.13777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.97943,26.13777,50.16026,26.13583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.18916,26.15666,49.97943,26.13777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.06443,26.17777,50.16582,26.17833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.16582,26.17833,50.06443,26.17777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.02277,26.19611,50.16582,26.17833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.21888,26.29499,50.02277,26.19611), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.18388,26.41222,50.05138,26.46277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.05138,26.46277,36.30527,26.50055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.30527,26.50055,50.05138,26.46277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.00054,26.565,36.30527,26.50055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.15083,26.66944,50.11054,26.68027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.11054,26.68027,50.03749,26.68166), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.03749,26.68166,50.11054,26.68027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.16943,26.68305,50.03749,26.68166), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.98305,26.69944,36.16943,26.68305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((50.01415,26.71917,49.98305,26.69944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.96221,26.845,36.0286,26.90138), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.0286,26.90138,49.96221,26.845), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.68082,26.96583,36.0286,26.90138), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.82166,27.09555,49.56888,27.10527), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.56888,27.10527,35.82166,27.09555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.52082,27.13222,49.39027,27.15499), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.39027,27.15499,49.49443,27.17722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.49443,27.17722,49.52943,27.18055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.52943,27.18055,49.49443,27.17722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.33054,27.19583,49.41444,27.20222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.41444,27.20222,49.33054,27.19583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.37888,27.21944,49.41444,27.20222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.72527,27.31055,49.21944,27.32722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.21944,27.32722,35.72527,27.31055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.30721,27.34444,49.21944,27.32722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.6311,27.36499,49.30721,27.34444), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.22388,27.39805,49.26221,27.40416), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.26221,27.40416,49.22388,27.39805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.30777,27.44221,49.14082,27.44277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.14082,27.44277,49.30777,27.44221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.24971,27.53694,35.50804,27.54277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.50804,27.54277,49.24971,27.53694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.90305,27.56638,49.0461,27.56777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((49.0461,27.56777,48.90305,27.56638), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.50749,27.60249,48.96193,27.62472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.96193,27.62472,48.85805,27.63277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.85805,27.63277,48.96193,27.62472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.4536,27.66499,48.81638,27.6861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.81638,27.6861,48.84637,27.7), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.84637,27.7,48.81638,27.6861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.77471,27.71444,48.84637,27.7), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.84721,27.76555,48.87693,27.76583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.87693,27.76583,48.84721,27.76555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.81416,27.79027,48.87693,27.76583), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.33582,27.855,48.8261,27.87805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.8261,27.87805,35.33582,27.855), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.74277,27.96028,35.23249,27.96416), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.23249,27.96416,48.74277,27.96028), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.77249,27.97916,35.23249,27.96416), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.16305,28.0025,48.75082,28.01722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.75082,28.01722,35.19582,28.02694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.19582,28.02694,34.61388,28.02888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.61388,28.02888,35.19582,28.02694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.65638,28.03305,34.61388,28.02888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.66415,28.06583,34.86332,28.08027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.86332,28.08027,35.10165,28.08222), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.10165,28.08222,34.86332,28.08027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.60304,28.09166,34.78971,28.09972), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.78971,28.09972,34.60304,28.09166), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.0086,28.10888,34.84026,28.11055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.84026,28.11055,35.0086,28.10888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.60138,28.12611,34.67693,28.12749), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.67693,28.12749,48.60138,28.12611), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.70594,28.13705,34.67693,28.12749), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.63138,28.175,48.62444,28.20861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.62444,28.20861,34.63138,28.175), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.67471,28.24888,48.62444,28.20861), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.53165,28.29861,34.67471,28.24888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.73444,28.38139,48.53665,28.4075), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.53665,28.4075,34.73444,28.38139), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.80777,28.53639,48.41973,28.54436), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((48.41973,28.54436,34.80777,28.53639), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((47.68415,28.56194,48.41973,28.54436), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.7911,28.64139,47.57526,28.69999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((47.57526,28.69999,34.7911,28.64139), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.81471,28.76083,45.50888,28.77472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((45.50888,28.77472,34.81471,28.76083), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((47.53915,28.8561,45.76499,28.85694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((45.76499,28.85694,47.53915,28.8561), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.83276,28.90472,45.76499,28.85694), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((47.46082,28.99888,34.8736,29.06833), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.8736,29.06833,46.69804,29.08638), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((46.69804,29.08638,46.54161,29.10416), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((46.54161,29.10416,46.69804,29.08638), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.06998,29.18888,44.74193,29.19055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.74193,29.19055,36.06998,29.18888), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.98888,29.20193,44.71489,29.20538), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((44.71489,29.20538,35.98888,29.20193), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.91277,29.23277,35.73248,29.24249), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.73248,29.24249,34.91277,29.23277), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.47581,29.28249,35.21887,29.32194), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((35.21887,29.32194,34.9611,29.35999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.9611,29.35999,34.96096,29.36243), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((34.96096,29.36243,34.9611,29.35999), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.32804,29.37722,34.96096,29.36243), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.99988,29.46003,36.32804,29.37722), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.54916,29.57527,43.59082,29.60777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.59082,29.60777,36.54916,29.57527), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.69859,29.79805,36.7436,29.86472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((36.7436,29.86472,37.00277,29.91221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.00277,29.91221,37.26221,29.95916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.26221,29.95916,37.00277,29.91221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.51054,30.01805,37.26221,29.95916), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.61276,30.10277,37.51054,30.01805), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.61998,30.24027,37.66749,30.33638), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.66749,30.33638,43.65832,30.37944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.65832,30.37944,43.06248,30.41472), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((43.06248,30.41472,43.65832,30.37944), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.93082,30.4686,38.00138,30.50417), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.00138,30.50417,37.93082,30.4686), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.97748,30.72777,37.77693,30.73221), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.77693,30.73221,42.97748,30.72777), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.55193,30.96027,42.42387,30.96333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((42.42387,30.96333,37.55193,30.96027), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.32638,31.1861,42.42387,30.96333), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.09804,31.41333,41.22221,31.4711), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((41.22221,31.4711,37.00526,31.50555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.00526,31.50555,41.22221,31.4711), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.26943,31.57333,37.53387,31.64055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.53387,31.64055,37.79887,31.70721), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((37.79887,31.70721,38.06248,31.77305), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.06248,31.77305,37.79887,31.70721), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.32832,31.83944,38.59443,31.90527), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.59443,31.90527,38.86082,31.97055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((38.86082,31.97055,39.00499,32.00555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.00499,32.00555,38.86082,31.97055), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((40.00249,32.0611,39.00499,32.00555), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.27582,32.21665,39.30308,32.23734), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.30308,32.23734,39.27582,32.21665), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")
	render_tiles((39.30308,32.23734,39.27582,32.21665), mapfile, tile_dir, 0, 11, "sa-saudi-arabia")