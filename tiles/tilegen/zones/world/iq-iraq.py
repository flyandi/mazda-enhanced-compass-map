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
    # Region: IQ
    # Region Name: Iraq

	render_tiles((46.54161,29.10416,46.55526,29.11249), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.55526,29.11249,46.54161,29.10416), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.71489,29.20538,46.55526,29.11249), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.77415,29.3686,43.99988,29.46003), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.99988,29.46003,45.46499,29.47443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.46499,29.47443,43.99988,29.46003), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.8836,29.50277,45.46499,29.47443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.59082,29.60777,46.8836,29.50277), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.50166,29.92416,48.54518,29.94454), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.54518,29.94454,48.50166,29.92416), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.12887,29.97471,48.44776,29.99305), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.44776,29.99305,47.12887,29.97471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.95044,30.01302,47.16998,30.01527), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.16998,30.01527,47.95044,30.01302), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.09388,30.04555,47.16998,30.01527), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.35693,30.08221,47.73137,30.08388), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.73137,30.08388,47.35693,30.08221), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.61276,30.10277,47.73137,30.08388), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.37943,30.12944,43.61276,30.10277), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.40276,30.20277,48.37943,30.12944), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.19804,30.31777,48.27415,30.32916), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.27415,30.32916,48.19804,30.31777), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.65832,30.37944,43.06248,30.41472), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.06248,30.41472,48.15359,30.42166), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.15359,30.42166,43.06248,30.41472), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.06959,30.46073,48.15359,30.42166), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.03333,30.5536,48.06959,30.46073), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.97748,30.72777,48.03333,30.5536), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.42387,30.96333,48.03693,30.99471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((48.03693,30.99471,47.69387,31.00111), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.69387,31.00111,48.03693,30.99471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.6972,31.40777,41.22221,31.4711), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.22221,31.4711,47.6972,31.40777), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.86443,31.7986,40.00249,32.0611), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((40.00249,32.0611,47.5172,32.14832), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.5172,32.14832,40.00249,32.0611), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((39.30308,32.23734,47.50304,32.2536), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.50304,32.2536,39.30308,32.23734), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.41331,32.34109,47.44082,32.38332), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.44082,32.38332,47.41331,32.34109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.1547,32.45776,39.20277,32.46443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((39.20277,32.46443,47.1547,32.45776), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((47.36249,32.4747,39.20277,32.46443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((39.10221,32.69165,46.78304,32.70833), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.78304,32.70833,39.10221,32.69165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.63804,32.81999,39.00138,32.91776), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((39.00138,32.91776,46.42387,32.93748), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.42387,32.93748,46.14804,32.95304), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.14804,32.95304,46.2836,32.96666), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.2836,32.96666,46.10332,32.97109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.10332,32.97109,46.2836,32.96666), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.09748,33.00555,46.10332,32.97109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.14499,33.04804,46.14638,33.06944), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.14638,33.06944,46.04916,33.09082), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.04916,33.09082,46.14638,33.06944), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.11137,33.11276,46.05554,33.12165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.05554,33.12165,46.11137,33.11276), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((38.89971,33.14471,46.05554,33.12165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.19859,33.19109,38.89971,33.14471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.1247,33.3086,38.79749,33.37165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((38.79749,33.37165,38.79455,33.37723), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((38.79455,33.37723,38.79749,33.37165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.8747,33.49165,45.99693,33.49554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.99693,33.49554,45.8747,33.49165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.94859,33.55665,45.75193,33.58887), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.75193,33.58887,45.94859,33.55665), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.90549,33.63319,45.75193,33.58887), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.55026,33.8886,45.40582,33.97109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.40582,33.97109,45.55026,33.8886), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((40.27748,34.07777,45.40582,33.97109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.56749,34.2197,45.58471,34.30248), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.58471,34.30248,45.48776,34.33582), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.48776,34.33582,45.55193,34.34415), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.55193,34.34415,45.48776,34.33582), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.00388,34.41943,45.43554,34.44859), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.43554,34.44859,41.00388,34.41943), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.52276,34.49776,45.43554,34.44859), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.71471,34.55721,45.51332,34.58166), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.51332,34.58166,45.53193,34.60054), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.53193,34.60054,45.51332,34.58166), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.70832,34.65915,45.53193,34.60054), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.65109,34.72693,41.22915,34.78832), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.22915,34.78832,45.69193,34.81915), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.69193,34.81915,45.7661,34.84582), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.7661,34.84582,45.69193,34.81915), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.87026,34.90971,45.77248,34.91443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.77248,34.91443,45.87026,34.90971), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.03249,35.05776,45.92137,35.0786), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.92137,35.0786,45.94193,35.09554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.94193,35.09554,46.15526,35.09915), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.15526,35.09915,45.94193,35.09554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.21137,35.1947,46.19304,35.2111), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.19304,35.2111,41.21137,35.1947), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.11915,35.24443,46.19304,35.2111), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.14804,35.30165,46.11915,35.24443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.99221,35.48109,41.27776,35.49554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.27776,35.49554,45.99221,35.48109), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.02026,35.5761,45.97998,35.58471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.97998,35.58471,46.02026,35.5761), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.38387,35.62526,45.97998,35.58471), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.01915,35.67554,46.13443,35.6972), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.13443,35.6972,46.01915,35.67554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.3186,35.76971,46.34887,35.79999), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.34887,35.79999,45.78526,35.81499), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.78526,35.81499,46.34026,35.82555), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((46.34026,35.82555,45.74332,35.82804), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.74332,35.82804,46.34026,35.82555), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.37804,35.83859,45.74332,35.82804), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.63499,35.96554,45.38471,35.98193), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.38471,35.98193,45.63499,35.96554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.33498,36.00443,45.50555,36.02054), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.50555,36.02054,45.33498,36.00443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.25499,36.05499,45.36915,36.08027), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.36915,36.08027,41.25499,36.05499), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.29027,36.35555,45.12137,36.41193), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.12137,36.41193,45.25082,36.42027), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.25082,36.42027,45.12137,36.41193), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.40304,36.52554,45.01471,36.53526), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.01471,36.53526,41.40304,36.52554), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((41.83526,36.59887,45.01471,36.53526), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.06666,36.67387,41.83526,36.59887), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((45.01693,36.74999,44.8672,36.78526), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.8672,36.78526,44.84165,36.81971), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.84165,36.81971,44.8672,36.78526), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.90665,36.88832,44.84165,36.81971), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.31721,36.97054,44.25526,36.98665), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.25526,36.98665,44.31721,36.97054), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.90971,37.02387,44.3511,37.04832), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.3511,37.04832,42.37385,37.05964), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.37385,37.05964,44.3511,37.04832), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.37385,37.05964,44.3511,37.04832), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.76804,37.10526,42.36609,37.11018), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.36609,37.11018,44.19776,37.1111), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.19776,37.1111,42.36609,37.11018), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.52915,37.12026,44.19776,37.1111), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.78667,37.14815,44.26776,37.16749), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.26776,37.16749,42.6011,37.18665), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.6011,37.18665,44.63804,37.18748), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.63804,37.18748,42.6011,37.18665), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.90359,37.22165,43.62304,37.22998), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.62304,37.22998,43.90359,37.22165), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.26166,37.24193,43.62304,37.22998), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.23109,37.27637,43.30637,37.30998), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.30637,37.30998,44.00388,37.31499), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.00388,37.31499,44.11638,37.31638), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((44.11638,37.31638,44.00388,37.31499), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.95054,37.32249,44.11638,37.31638), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.34582,37.33193,42.95054,37.32249), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.72665,37.35555,43.14443,37.37804), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((43.14443,37.37804,42.7861,37.38443), mapfile, tile_dir, 0, 11, "iq-iraq")
	render_tiles((42.7861,37.38443,43.14443,37.37804), mapfile, tile_dir, 0, 11, "iq-iraq")