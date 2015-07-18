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
    # Region: LT
    # Region Name: Lithuania

	render_tiles((24.3869,53.8886,24.2778,53.8997), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.2778,53.8997,23.6444,53.9047), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.6444,53.9047,24.3325,53.9061), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.3325,53.9061,23.6444,53.9047), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.7797,53.9178,23.8336,53.9258), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.8336,53.9258,23.72,53.9267), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.72,53.9267,23.8336,53.9258), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.5792,53.9361,24.0886,53.9375), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.0886,53.9375,23.7875,53.9381), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.7875,53.9381,24.0886,53.9375), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.9861,53.9389,23.7875,53.9381), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.50375,53.94716,24.2314,53.9531), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.2314,53.9531,23.935,53.9558), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.935,53.9558,24.2314,53.9531), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.1756,53.9675,24.7278,53.9686), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.7278,53.9686,24.1756,53.9675), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8208,53.9772,24.7278,53.9686), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.6122,53.9922,23.47499,53.99526), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.47499,53.99526,24.6122,53.9922), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.6917,54.0017,23.47499,53.99526), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.51778,54.03027,24.8406,54.0344), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8406,54.0344,23.51778,54.03027), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.7956,54.1014,24.8356,54.1136), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8356,54.1136,24.7956,54.1014), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.0244,54.1311,25.0719,54.1347), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.0719,54.1347,25.6692,54.1361), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.6692,54.1361,25.0719,54.1347), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.48444,54.13832,25.6692,54.1361), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8403,54.1422,23.48444,54.13832), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5344,54.1469,24.8403,54.1422), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7003,54.1547,24.9689,54.1586), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.9689,54.1586,25.7853,54.1606), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7853,54.1606,24.9689,54.1586), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.1619,54.1725,25.5047,54.1831), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5047,54.1831,25.1619,54.1725), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5478,54.2033,25.7872,54.2181), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7872,54.2181,25.5183,54.2258), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5183,54.2258,25.2094,54.23), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.2094,54.23,25.5183,54.2258), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5772,54.2403,23.34194,54.24332), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.34194,54.24332,25.5772,54.2403), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.8078,54.2481,23.34194,54.24332), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.3917,54.2558,25.8078,54.2481), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.2178,54.2644,25.3917,54.2558), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7703,54.2878,25.7272,54.2906), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7272,54.2906,25.7703,54.2878), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.4519,54.2997,25.4928,54.3053), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.4928,54.3053,23.06611,54.30804), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.06611,54.30804,25.4928,54.3053), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.6111,54.3114,23.06611,54.30804), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.35333,54.32832,25.5475,54.3286), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5475,54.3286,21.35333,54.32832), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7142,54.3311,25.5475,54.3286), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.07472,54.33498,25.7142,54.3311), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.77,54.35999,25.5572,54.3661), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5572,54.3661,20.64166,54.36665), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.64166,54.36665,22.7828,54.36666), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.7828,54.36666,20.64166,54.36665), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.7828,54.36666,20.64166,54.36665), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23,54.38304,22.7828,54.36666), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.86333,54.40859,19.99083,54.42054), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.99083,54.42054,25.6364,54.4272), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.6364,54.4272,19.99083,54.42054), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.81192,54.44605,25.6364,54.4272), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.66563,54.46691,19.63651,54.47107), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.63651,54.47107,19.66563,54.46691), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.6331,54.4778,19.63651,54.47107), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.93916,54.49638,25.6331,54.4778), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.6489,54.5175,19.93916,54.49638), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.06389,54.55082,19.99667,54.57332), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.99667,54.57332,25.7642,54.5792), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7642,54.5792,19.99667,54.57332), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.86472,54.62971,19.96111,54.63443), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.96111,54.63443,19.86472,54.62971), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.07444,54.65276,19.94416,54.65916), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.94416,54.65916,20.07444,54.65276), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7272,54.6667,19.94416,54.65916), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.405,54.68277,20.23833,54.69138), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.23833,54.69138,19.95472,54.69943), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.95472,54.69943,20.23833,54.69138), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.01139,54.72027,25.7492,54.7283), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7492,54.7283,20.01139,54.72027), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.96111,54.77693,25.7367,54.7894), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7367,54.7894,19.96111,54.77693), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.8036,54.8139,25.7367,54.7894), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.7889,54.8703,19.91972,54.8711), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.91972,54.8711,25.7889,54.8703), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.91805,54.90083,25.8625,54.9108), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.8625,54.9108,20.91805,54.90083), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.93666,54.9211,25.8625,54.9108), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.2225,54.93193,20.56639,54.93777), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.56639,54.93777,21.2225,54.93193), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.8817,54.9442,20.4275,54.94943), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.4275,54.94943,25.8817,54.9442), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.24694,54.95499,20.4275,54.94943), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((19.98528,54.96193,21.24694,54.95499), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.54472,54.97471,26.1608,54.9772), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.1608,54.9772,20.54472,54.97471), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.2194,55.03,26.2483,55.0711), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.2483,55.0711,20.69861,55.07971), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.69861,55.07971,26.2483,55.0711), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.19639,55.11499,26.2533,55.1236), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.2533,55.1236,26.6158,55.1247), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6158,55.1247,26.2533,55.1236), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.4503,55.1328,26.6158,55.1247), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6417,55.1425,26.2842,55.1481), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.2842,55.1481,26.6417,55.1425), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.4817,55.155,26.2842,55.1481), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.8675,55.17915,26.6417,55.1908), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6417,55.1908,21.26028,55.20055), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.26028,55.20055,21.18999,55.20638), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.18999,55.20638,21.26028,55.20055), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6686,55.2181,21.27444,55.2286), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.27444,55.2286,26.6686,55.2181), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.7133,55.2431,21.27131,55.24969), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.27131,55.24969,26.7753,55.2506), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.7753,55.2506,21.27131,55.24969), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.96858,55.28003,26.8197,55.2811), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.8197,55.2811,20.96858,55.28003), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((20.93203,55.29006,26.8197,55.2811), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.5547,55.3131,26.7669,55.3136), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.7669,55.3136,26.5547,55.3131), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6314,55.3311,26.4558,55.3414), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.4558,55.3414,21.18278,55.34471), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.18278,55.34471,26.4558,55.3414), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.005,55.35582,21.18278,55.34471), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.25166,55.36804,21.005,55.35582), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.5025,55.39,21.25166,55.36804), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.10194,55.41415,26.5025,55.39), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.5236,55.4439,21.24778,55.45915), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.24778,55.45915,26.5694,55.4683), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.5694,55.4683,21.24778,55.45915), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.545,55.5111,21.0925,55.5311), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.0925,55.5311,26.545,55.5111), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.6267,55.5931,21.0925,55.5311), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.11116,55.66891,26.61409,55.67605), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.61409,55.67605,21.11116,55.66891), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.5108,55.6839,26.61409,55.67605), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.08624,55.7063,26.3386,55.7264), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.3386,55.7264,21.08624,55.7063), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.2689,55.7694,21.05833,55.78165), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.05833,55.78165,26.2689,55.7694), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.1981,55.8625,21.05833,55.78165), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((26.0028,55.9583,21.06722,55.99138), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.06722,55.99138,26.0028,55.9583), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.04962,56.07677,21.2181,56.09), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.2181,56.09,21.04962,56.07677), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.715,56.09,21.04962,56.07677), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5939,56.1481,25.685,56.1517), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.685,56.1517,25.5939,56.1481), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.4958,56.1567,25.685,56.1517), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.2444,56.1683,25.3275,56.1692), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.3275,56.1692,21.2444,56.1683), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.5461,56.1725,25.3275,56.1692), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.0969,56.2011,25.5461,56.1725), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.3544,56.24,21.4278,56.2419), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.4278,56.2419,21.3544,56.24), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.1478,56.2614,24.4733,56.2692), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.4733,56.2692,25.0472,56.2708), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25.0472,56.2708,24.4733,56.2692), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.5583,56.2883,21.5625,56.2933), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.5625,56.2933,25,56.2956), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((25,56.2956,21.5625,56.2933), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.0961,56.3058,24.3397,56.3108), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.3397,56.3108,21.7094,56.3153), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.7094,56.3153,21.5911,56.3186), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((21.5911,56.3186,21.7094,56.3153), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.0375,56.3286,23.9489,56.3322), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.9489,56.3322,23.5253,56.3342), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.5253,56.3342,23.7464,56.3358), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.7464,56.3358,23.5253,56.3342), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.1803,56.3456,23.7464,56.3358), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.6914,56.3567,23.7411,56.36), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.7411,56.36,23.6519,56.3603), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.6519,56.3603,23.7411,56.36), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.1711,56.3619,23.5969,56.3622), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.5969,56.3622,23.1711,56.3619), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.6533,56.3658,23.5969,56.3622), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.8272,56.3792,23.2961,56.3808), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((23.2961,56.3808,22.8272,56.3792), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.6233,56.3869,23.2961,56.3808), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.9928,56.3931,22.2489,56.3975), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.2489,56.3975,24.7267,56.3978), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.7267,56.3978,22.2489,56.3975), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.5192,56.4044,24.7267,56.3978), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8644,56.4128,22.0672,56.4194), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.0672,56.4194,22.9414,56.4239), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.9414,56.4239,22.1544,56.4242), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((22.1544,56.4242,22.9414,56.4239), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.9222,56.44,24.8722,56.4436), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8722,56.4436,24.9222,56.44), mapfile, tile_dir, 0, 11, "lt-lithuania")
	render_tiles((24.8997,56.4506,24.8722,56.4436), mapfile, tile_dir, 0, 11, "lt-lithuania")