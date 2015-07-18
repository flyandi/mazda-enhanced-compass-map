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
    # Region: MK
    # Region Name: Macedonia

	render_tiles((20.98325,40.85702,21.12001,40.86268), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.12001,40.86268,20.98325,40.85702), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.34305,40.87193,21.12001,40.86268), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.983,40.89388,20.8075,40.90026), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.8075,40.90026,21.67249,40.90192), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.67249,40.90192,20.8075,40.90026), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.47166,40.91054,20.73975,40.91099), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.73975,40.91099,21.47166,40.91054), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.73388,40.91193,20.73975,40.91099), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.78027,40.92887,20.85888,40.93332), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.85888,40.93332,21.78027,40.92887), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.69444,40.93803,20.85888,40.93332), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.66999,41.08804,20.64491,41.09015), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.64491,41.09015,20.66999,41.08804), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.59749,41.09415,21.91611,41.09526), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.91611,41.09526,20.59749,41.09415), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.59027,41.11998,22.46888,41.12165), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.46888,41.12165,22.59027,41.11998), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.1311,41.12498,22.46888,41.12165), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.72055,41.14276,22.06666,41.15832), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.06666,41.15832,22.24694,41.17054), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.24694,41.17054,22.06666,41.15832), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.65388,41.18526,22.74117,41.18602), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.74117,41.18602,22.65388,41.18526), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.75471,41.21443,22.74117,41.18602), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.76529,41.24384,22.75471,41.21443), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.49332,41.32748,22.93595,41.3433), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.93595,41.3433,22.80138,41.34415), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.80138,41.34415,22.93595,41.3433), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.96499,41.39443,20.56221,41.40387), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.56221,41.40387,22.96499,41.39443), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.46249,41.5536,20.55527,41.58471), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.55527,41.58471,20.46249,41.5536), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.98555,41.66137,23.03139,41.72054), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((23.03139,41.72054,20.51416,41.72776), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.51416,41.72776,23.03139,41.72054), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((23.01277,41.76527,22.94749,41.80248), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.94749,41.80248,23.01277,41.76527), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.6317,41.8558,20.7025,41.8561), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.7025,41.8561,20.6317,41.8558), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.7331,41.8639,20.7025,41.8561), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.67,41.8719,20.7331,41.8639), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.58965,41.88455,20.67,41.8719), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.58965,41.88455,20.67,41.8719), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.7772,41.9256,20.58965,41.88455), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.7592,41.9933,22.80333,42.04777), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.80333,42.04777,20.7928,42.0825), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((20.7928,42.0825,21.3131,42.0936), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.3131,42.0936,21.2483,42.0997), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.2483,42.0997,22.60944,42.10332), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.60944,42.10332,21.2483,42.0997), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.3286,42.1081,22.60944,42.10332), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.2153,42.15,21.0336,42.1511), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.0336,42.1511,21.3061,42.1514), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.3061,42.1514,21.0336,42.1511), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.3481,42.1956,21.1419,42.1981), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.1419,42.1981,21.1047,42.1989), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.1047,42.1989,21.1419,42.1981), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.4394,42.2297,21.7383,42.2375), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.7383,42.2375,21.5572,42.2436), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.5572,42.2436,21.7383,42.2375), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.60978,42.25318,21.46,42.2625), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.46,42.2625,21.7886,42.2633), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.7886,42.2633,21.46,42.2625), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.51,42.2647,21.7886,42.2633), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.40777,42.27943,21.51,42.2647), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.0833,42.3006,21.8044,42.3008), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.8044,42.3008,22.0833,42.3006), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.9264,42.3067,21.8044,42.3008), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.195,42.3153,22.1247,42.3197), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.1247,42.3197,22.36361,42.31998), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.36361,42.31998,22.1247,42.3197), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.36942,42.32293,21.9958,42.3256), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.9958,42.3256,22.36942,42.32293), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((21.8503,42.3308,21.9958,42.3256), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.2464,42.3397,22.2219,42.3417), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.2219,42.3417,22.2464,42.3397), mapfile, tile_dir, 0, 11, "mk-macedonia")
	render_tiles((22.3244,42.3614,22.2219,42.3417), mapfile, tile_dir, 0, 11, "mk-macedonia")