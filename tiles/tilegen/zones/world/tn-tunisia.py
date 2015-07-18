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
    # Region: TN
    # Region Name: Tunisia

	render_tiles((10.865,33.6386,10.73722,33.70666), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.865,33.6386,10.73722,33.70666), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.73722,33.70666,10.84305,33.71277), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.84305,33.71277,10.73722,33.70666), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.80278,33.73415,10.71444,33.74055), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.71444,33.74055,10.80278,33.73415), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.05722,33.79804,10.71444,33.74055), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.72611,33.88082,10.79639,33.8961), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.79639,33.8961,10.72611,33.88082), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.53202,30.23606,9.88222,30.34749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.88222,30.34749,9.53202,30.23606), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.21527,30.7361,10.29055,30.90666), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.29055,30.90666,10.21527,30.7361), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.12333,31.42249,10.13944,31.50999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.13944,31.50999,9.19555,31.56916), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.19555,31.56916,10.13944,31.50999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.30639,31.71305,10.46667,31.72027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.46667,31.72027,10.30639,31.71305), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.38,31.73055,10.46667,31.72027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.61361,31.85638,10.62472,31.97055), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.62472,31.97055,10.78722,32.00888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.78722,32.00888,10.62472,31.97055), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.05916,32.09109,10.8825,32.13915), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.8825,32.13915,9.05916,32.09109), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.51667,32.40942,11.5675,32.44221), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.5675,32.44221,11.51667,32.40942), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.58333,32.49082,8.35111,32.5311), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.35111,32.5311,11.58333,32.49082), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.47055,32.62776,8.35111,32.5311), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.30611,32.83415,11.49166,33.03499), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.49166,33.03499,8.02278,33.11276), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.02278,33.11276,8.09027,33.11443), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.09027,33.11443,8.02278,33.11276), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.52714,33.16578,11.17583,33.20805), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.52714,33.16578,11.17583,33.20805), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.17583,33.20805,7.765,33.2086), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.765,33.2086,11.17583,33.20805), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.21528,33.21249,7.765,33.2086), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.73194,33.24832,11.29139,33.27999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.29139,33.27999,11.11861,33.28888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.11861,33.28888,11.29139,33.27999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.13167,33.31082,11.11861,33.28888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.10528,33.36832,11.13167,33.31082), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.72416,33.43942,10.75417,33.47276), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.75417,33.47276,7.72416,33.43942), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.115,33.5161,10.90361,33.5311), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.90361,33.5311,10.67222,33.53277), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.67222,33.53277,10.90361,33.5311), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.93222,33.56944,11.08889,33.58166), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.08889,33.58166,10.93222,33.56944), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.04861,33.61694,10.90028,33.61721), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.90028,33.61721,11.04861,33.61694), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.93361,33.62999,10.48667,33.63888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.48667,33.63888,10.93361,33.62999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.7225,33.66388,10.35611,33.6861), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.35611,33.6861,10.71833,33.70415), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.71833,33.70415,10.66917,33.70693), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.66917,33.70693,10.71833,33.70415), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.5225,33.7886,10.66917,33.70693), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.49167,33.89971,10.09639,33.91444), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.09639,33.91444,7.49167,33.89971), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.01917,34.08027,7.52722,34.10193), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.52722,34.10193,10.01917,34.08027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.02139,34.1911,7.65694,34.21249), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.65694,34.21249,10.02139,34.1911), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.7675,34.23637,7.65694,34.21249), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.11139,34.31471,7.7675,34.23637), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((7.83972,34.41248,10.375,34.43582), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.375,34.43582,7.83972,34.41248), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.4275,34.49527,10.375,34.43582), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.60583,34.59055,8.2475,34.6411), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.2475,34.6411,10.60583,34.59055), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.22666,34.69553,8.2475,34.6411), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.28861,34.75277,8.22666,34.69553), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.89194,34.86193,8.26139,34.91026), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.26139,34.91026,10.89194,34.86193), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.40111,35.19221,11.10305,35.2161), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.10305,35.2161,8.45083,35.23387), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.45083,35.23387,11.14805,35.23999), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.14805,35.23999,8.45083,35.23387), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.10082,35.26487,8.42722,35.26749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.42722,35.26749,11.10082,35.26487), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.345,35.28721,8.42722,35.26749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.02944,35.33138,8.30833,35.33915), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.30833,35.33915,11.02944,35.33138), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.30944,35.42721,11.03,35.4361), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.03,35.4361,8.30944,35.42721), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.05722,35.50943,8.36222,35.52193), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.36222,35.52193,11.02694,35.52583), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.02694,35.52583,8.36222,35.52193), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.02806,35.63971,8.34027,35.67748), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.34027,35.67748,10.83722,35.69888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.83722,35.69888,8.34027,35.67748), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.81417,35.72721,10.83722,35.69888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.27361,35.7586,10.72472,35.77221), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.72472,35.77221,10.82444,35.78249), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.82444,35.78249,10.72472,35.77221), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.60972,35.8561,8.26278,35.90109), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.26278,35.90109,10.60972,35.8561), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.48111,36.03749,10.45528,36.16943), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.45528,36.16943,10.48111,36.03749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.4975,36.32832,10.56194,36.39249), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.56194,36.39249,8.37361,36.4447), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.37361,36.4447,10.795,36.46304), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.795,36.46304,8.37361,36.4447), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.19083,36.49165,10.795,36.46304), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.18361,36.52415,8.19083,36.49165), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.23139,36.56721,8.18361,36.52415), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.44305,36.6547,8.47528,36.7172), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.47528,36.7172,10.415,36.72332), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.415,36.72332,8.47528,36.7172), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.33111,36.73721,10.415,36.72332), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.47166,36.75304,10.9775,36.75638), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.9775,36.75638,8.47166,36.75304), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.42111,36.76443,10.9775,36.75638), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.52806,36.77776,8.42389,36.79054), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.42389,36.79054,10.19333,36.79082), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.19333,36.79082,8.42389,36.79054), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.22805,36.7936,10.19333,36.79082), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.28944,36.81332,10.19806,36.82804), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.19806,36.82804,8.65,36.83526), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.65,36.83526,10.19806,36.82804), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.27222,36.8486,10.57,36.86137), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.57,36.86137,8.65722,36.86749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.65722,36.86749,11.12222,36.87027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.12222,36.87027,8.65722,36.86749), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.34555,36.87415,11.12222,36.87027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.61639,36.89499,10.34555,36.87415), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.63023,36.94299,10.8175,36.94888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.8175,36.94888,8.63023,36.94299), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.82139,36.97721,10.8175,36.94888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.91555,37.03082,10.89805,37.0461), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.89805,37.0461,10.16805,37.05888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.16805,37.05888,10.89805,37.0461), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((11.04694,37.08166,10.16805,37.05888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.23306,37.10888,8.99,37.11388), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((8.99,37.11388,10.23306,37.10888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.85361,37.13915,10.12556,37.14165), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.12556,37.14165,9.85361,37.13915), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.90611,37.16471,10.14333,37.16693), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.14333,37.16693,9.90611,37.16471), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.27167,37.17443,9.78444,37.17888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.78444,37.17888,9.14972,37.18138), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.14972,37.18138,9.78444,37.17888), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.92528,37.2136,9.77278,37.22027), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.77278,37.22027,9.92528,37.2136), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.85111,37.22832,9.33472,37.22943), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.33472,37.22943,9.85111,37.22832), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.19666,37.23193,9.33472,37.22943), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.85444,37.2561,10.05639,37.2611), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((10.05639,37.2611,9.85444,37.2561), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.86472,37.32471,9.6725,37.33804), mapfile, tile_dir, 0, 11, "tn-tunisia")
	render_tiles((9.6725,37.33804,9.86472,37.32471), mapfile, tile_dir, 0, 11, "tn-tunisia")