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
    # Region: GE
    # Region Name: Georgia

	render_tiles((46.5208,41.05,46.4839,41.0556), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.4839,41.0556,46.5208,41.05), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.5453,41.0953,46.4403,41.0964), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.4403,41.0964,46.5453,41.0953), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.6225,41.1006,46.3783,41.1042), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3783,41.1042,43.47324,41.10621), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.47324,41.10621,46.5722,41.1081), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.5722,41.1081,43.47324,41.10621), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.7822,41.1158,46.5722,41.1081), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.5617,41.1364,43.47304,41.14304), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.47304,41.14304,43.5617,41.1364), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.67,41.155,43.8625,41.1622), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.8625,41.1622,43.9856,41.1631), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.9856,41.1631,43.8625,41.1622), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.0314,41.1703,43.9856,41.1631), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.23304,41.17832,46.0314,41.1703), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.4608,41.1864,44.1575,41.1883), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.5542,41.1864,44.1575,41.1883), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.5775,41.1864,44.1575,41.1883), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.1575,41.1883,44.4608,41.1864), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.07,41.1917,44.3019,41.195), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.3019,41.195,44.07,41.1917), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.1783,41.1992,43.37526,41.20221), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.37526,41.20221,46.1403,41.2039), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.1403,41.2039,43.37526,41.20221), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2433,41.2056,46.1403,41.2039), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.7114,41.2128,44.5292,41.2142), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.5292,41.2142,44.8636,41.2147), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8636,41.2147,44.5292,41.2142), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.3656,41.2197,44.8636,41.2147), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.8131,41.2264,44.8778,41.2286), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8778,41.2286,45.8131,41.2264), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.6344,41.2317,44.8778,41.2286), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.1928,41.2364,44.6344,41.2317), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.19942,41.25526,46.7108,41.2553), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.1236,41.25526,46.7108,41.2553), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.7108,41.2553,43.19942,41.25526), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.9553,41.2617,44.8219,41.2619), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8219,41.2619,44.9553,41.2617), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8339,41.2869,45.7086,41.2903), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7086,41.2903,44.8339,41.2869), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.0225,41.2986,45.7086,41.2903), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.20776,41.30693,46.6939,41.3122), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.6939,41.3122,43.16081,41.31276), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.16081,41.31276,46.6939,41.3122), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7197,41.3206,43.16081,41.31276), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.6283,41.34,45.7706,41.3433), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7706,41.3433,46.6283,41.34), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.6211,41.365,46.5933,41.3794), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.5933,41.3794,46.6211,41.365), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.4992,41.3972,45.5383,41.3986), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.5383,41.3986,46.4992,41.3972), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.4011,41.4283,41.83332,41.42832), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.83332,41.42832,45.4011,41.4283), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.4767,41.4408,42.50665,41.44193), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.50665,41.44193,45.4767,41.4408), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.2789,41.4556,42.36693,41.46027), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.36693,41.46027,46.4136,41.4625), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.4136,41.4625,42.36693,41.46027), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.3367,41.4625,42.36693,41.46027), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.84721,41.47304,46.4136,41.4625), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.33,41.4853,42.84721,41.47304), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.8886,41.50804,42.78638,41.51027), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.78638,41.51027,42.8886,41.50804), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.95499,41.51638,46.3353,41.5169), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3353,41.5169,41.95499,41.51638), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.5312,41.52348,46.3353,41.5169), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2992,41.5503,46.3436,41.5575), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3436,41.5575,46.2992,41.5503), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3472,41.5708,42.58804,41.57638), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.58804,41.57638,46.3472,41.5708), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.83193,41.58332,46.2864,41.5892), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2864,41.5892,46.2567,41.5894), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2567,41.5894,46.2864,41.5892), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.72637,41.59109,46.2567,41.5894), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3242,41.5936,46.2372,41.595), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2372,41.595,46.3242,41.5936), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.265,41.6119,46.2828,41.6153), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2828,41.6153,46.265,41.6119), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2017,41.6594,46.2828,41.6153), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.1981,41.725,41.73637,41.74165), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.73637,41.74165,46.2189,41.7572), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.2189,41.7572,46.3319,41.7592), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3319,41.7592,46.2189,41.7572), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.4206,41.8378,46.45209,41.89657), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.45209,41.89657,46.32,41.9389), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.32,41.9389,46.3942,41.9406), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.3942,41.9406,46.32,41.9389), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.76249,41.99221,46.1039,41.9942), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.1039,41.9942,41.76249,41.99221), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.24,42.0014,46.1039,41.9942), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.0392,42.0233,45.9606,42.0239), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.9606,42.0239,46.0392,42.0233), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.0781,42.0342,46.0108,42.0431), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((46.0108,42.0431,46.0781,42.0342), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.9103,42.105,45.8092,42.1239), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.8092,42.1239,41.65415,42.13554), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.65415,42.13554,45.8092,42.1239), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7389,42.1736,45.6475,42.2047), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.6475,42.2047,41.64499,42.22443), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.64499,42.22443,45.6386,42.2289), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.6386,42.2289,41.64499,42.22443), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.55332,42.38582,45.7569,42.4822), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7569,42.4822,45.7006,42.5161), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.7006,42.5161,45.3517,42.5294), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.3517,42.5294,45.7006,42.5161), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.5528,42.5503,43.9694,42.5581), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.9694,42.5581,41.52249,42.56138), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.52249,42.56138,43.9694,42.5581), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.8675,42.5992,43.7783,42.6031), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.7783,42.6031,43.8675,42.5992), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.7972,42.6158,44.0536,42.6181), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.0536,42.6181,44.1378,42.6197), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.1378,42.6197,44.0536,42.6181), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.2208,42.6369,43.7353,42.6453), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.7353,42.6453,45.2419,42.6508), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.2419,42.6508,43.7353,42.6453), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8083,42.6653,45.2419,42.6508), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.7547,42.6889,45.0603,42.6925), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.0603,42.6925,44.7547,42.6889), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((45.1589,42.7058,44.3769,42.7089), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.3769,42.7089,45.1589,42.7058), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.2867,42.7128,44.3769,42.7089), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.8361,42.7278,41.4386,42.73166), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.4386,42.73166,43.8361,42.7278), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.6897,42.7364,41.4386,42.73166), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.855,42.7433,43.8325,42.7483), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.8325,42.7483,44.855,42.7433), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.9411,42.7578,41.36416,42.75916), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.36416,42.75916,44.5497,42.7594), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.5497,42.7594,41.36416,42.75916), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((44.8867,42.7603,44.5497,42.7594), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.15582,42.78915,43.6708,42.7911), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.6708,42.7911,41.15582,42.78915), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.09471,42.85082,43.5606,42.8608), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.5606,42.8608,41.09471,42.85082), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.3933,42.8992,41.07277,42.92805), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.07277,42.92805,43.2136,42.9317), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.2136,42.9317,41.07277,42.92805), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.1447,42.9639,43.2136,42.9317), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.0108,43.0567,40.83054,43.07193), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.83054,43.07193,43.0108,43.0567), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.67416,43.08777,40.83054,43.07193), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((43.0211,43.1044,40.67416,43.08777), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.9364,43.1261,40.32638,43.14193), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.32638,43.14193,42.6258,43.1431), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.6258,43.1431,40.32638,43.14193), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.38471,43.16554,42.85,43.1792), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.85,43.1792,42.5344,43.1811), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.5344,43.1811,42.7011,43.1825), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.7011,43.1825,42.5344,43.1811), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.0578,43.1861,42.7011,43.1825), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.1203,43.2003,41.8225,43.2042), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.8225,43.2042,42.1203,43.2003), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.9397,43.2175,41.6053,43.2203), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.6053,43.2203,42.4786,43.2228), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.4786,43.2228,41.6053,43.2203), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.7306,43.2278,41.565,43.2322), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.565,43.2322,42.1811,43.2356), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.1811,43.2356,41.565,43.2322), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((42.4153,43.2392,42.1811,43.2356), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.5231,43.2694,41.4356,43.2961), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.4356,43.2961,40.21835,43.3189), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.21835,43.3189,41.2956,43.3361), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.2956,43.3361,41.3933,43.3461), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.3933,43.3461,41.2956,43.3361), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.05138,43.37027,41.0442,43.375), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.0442,43.375,40.05138,43.37027), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((41.1681,43.3872,40.00651,43.3982), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.00651,43.3982,41.1681,43.3872), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.00651,43.3982,41.1681,43.3872), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.8825,43.47,40.7497,43.5044), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.7497,43.5044,40.5431,43.5086), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.5431,43.5086,40.7497,43.5044), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.6922,43.5442,40.6425,43.5444), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.6425,43.5444,40.6922,43.5442), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.085,43.555,40.6425,43.5444), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.2619,43.58,40.1775,43.5825), mapfile, tile_dir, 0, 11, "ge-georgia")
	render_tiles((40.1775,43.5825,40.2619,43.58), mapfile, tile_dir, 0, 11, "ge-georgia")