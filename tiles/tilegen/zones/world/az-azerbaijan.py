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
    # Region: AZ
    # Region Name: Azerbaijan

	render_tiles((46.16666,38.83998,46.17796,38.84422), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.17796,38.84422,46.16666,38.83998), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.87193,38.87998,46.17796,38.84422), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.1072,38.9361,45.87193,38.87998), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.43054,39.00471,46.1072,38.9361), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0428,39.0783,45.43054,39.00471), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9703,39.1664,45.33776,39.17249), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.33776,39.17249,45.9703,39.1664), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9719,39.2067,45.18748,39.21027), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.18748,39.21027,45.9719,39.2067), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0089,39.2508,45.9781,39.28), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9781,39.28,46.0089,39.2508), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.11582,39.3122,45.8858,39.3194), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8858,39.3194,45.11582,39.3122), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8556,39.3481,45.7956,39.3564), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7956,39.3564,45.8556,39.3481), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7761,39.3747,45.7956,39.3564), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.95026,39.43581,45.83,39.4494), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.83,39.4494,44.95026,39.43581), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4575,39.4936,45.8147,39.495), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8147,39.495,45.4575,39.4936), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4086,39.53,45.3261,39.5383), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.3261,39.5383,45.8231,39.5453), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8231,39.5453,45.3261,39.5383), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6267,39.56,45.7833,39.5692), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7833,39.5692,45.2044,39.5722), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2044,39.5722,45.7833,39.5692), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1711,39.5792,45.7322,39.5806), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7322,39.5806,45.1711,39.5792), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.3075,39.5881,45.7322,39.5806), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.8886,39.60582,45.2539,39.6078), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2539,39.6078,45.2819,39.6086), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2819,39.6086,45.2539,39.6078), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.81049,39.64273,45.1758,39.6725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.81049,39.64273,45.1758,39.6725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1758,39.6725,44.78999,39.69859), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.78999,39.69859,44.77858,39.70665), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.77858,39.70665,44.78999,39.69859), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.9331,39.7214,44.8583,39.725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((44.8583,39.725,44.9331,39.7214), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0619,39.7789,44.8583,39.725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.66553,38.39054,48.61054,38.40582), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.61054,38.40582,48.66553,38.39054), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.8605,38.44025,48.61054,38.40582), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.3336,38.6011,48.42999,38.62609), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.42999,38.62609,48.3336,38.6011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.2436,38.66721,48.42999,38.62609), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.24387,38.72776,48.10304,38.7836), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.10304,38.7836,48.24387,38.72776), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.01388,38.85693,48.83221,38.86499), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.83221,38.86499,48.01388,38.85693), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.53992,38.87672,48.83221,38.86499), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.90554,38.89665,48.01888,38.90915), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.01888,38.90915,48.90554,38.89665), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4947,38.9597,48.93555,38.9636), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.93555,38.9636,49.07221,38.96387), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.07221,38.96387,48.93555,38.9636), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.14054,38.97721,48.27609,38.98165), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.27609,38.98165,49.14054,38.97721), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.05166,39.00138,48.27609,38.98165), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.20193,39.02471,49.12305,39.0261), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.12305,39.0261,48.32388,39.02637), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.32388,39.02637,49.12305,39.0261), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5236,39.0442,48.93027,39.0586), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.93027,39.0586,46.5236,39.0442), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.14999,39.08166,46.5108,39.0969), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5108,39.0969,49.13471,39.10332), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.13471,39.10332,49.06721,39.10777), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.06721,39.10777,49.13471,39.10332), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.29388,39.11249,49.06721,39.10777), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4936,39.1286,48.29388,39.11249), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.83832,39.15526,46.4239,39.1639), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4239,39.1639,46.83832,39.15526), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.05249,39.17277,48.96138,39.17443), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.96138,39.17443,49.05249,39.17277), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5456,39.1894,47.02554,39.18942), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.02554,39.18942,49.00277,39.18943), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.00277,39.18943,47.02554,39.18942), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4161,39.2014,48.13499,39.20888), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.13499,39.20888,46.4161,39.2014), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4522,39.2186,46.6225,39.2247), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.6225,39.2247,46.4522,39.2186), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.11998,39.2636,46.5442,39.2789), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5442,39.2789,49.40638,39.28333), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.40638,39.28333,46.5442,39.2789), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.09971,39.30443,48.15026,39.30915), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.15026,39.30915,47.09971,39.30443), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.25804,39.31944,49.33884,39.32718), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.33884,39.32718,46.5181,39.3303), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5181,39.3303,49.33884,39.32718), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.29721,39.34332,49.39555,39.35027), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.39555,39.35027,49.29721,39.34332), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4039,39.3786,49.4136,39.38388), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.4136,39.38388,48.35971,39.38471), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.35971,39.38471,49.4136,39.38388), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3806,39.425,48.33887,39.42554), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.33887,39.42554,46.3806,39.425), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.35526,39.43748,48.33887,39.42554), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4008,39.4508,47.35526,39.43748), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5167,39.4758,49.28749,39.48082), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.28749,39.48082,46.5167,39.4758), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.45443,39.49638,49.28749,39.48082), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5097,39.5133,47.45443,39.49638), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5464,39.5386,46.5097,39.5133), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5414,39.5644,46.5133,39.5836), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5133,39.5836,46.4139,39.5839), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4139,39.5839,46.5133,39.5836), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2503,39.595,46.2042,39.5981), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2042,39.5981,46.2503,39.595), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.31332,39.61471,47.74693,39.62109), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.74693,39.62109,49.31332,39.61471), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.35,39.6281,47.74693,39.62109), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.1536,39.6578,47.80665,39.67721), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.80665,39.67721,46.0678,39.6894), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0678,39.6894,49.42138,39.7011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.42138,39.7011,46.0678,39.6894), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.97637,39.7197,49.42138,39.7011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0061,39.7731,45.9264,39.7881), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9264,39.7881,46.0061,39.7731), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8567,39.8236,49.41277,39.83777), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.41277,39.83777,45.8567,39.8236), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8164,39.8656,49.44444,39.8686), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.44444,39.8686,45.8164,39.8656), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.41999,39.92416,45.7842,39.9375), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7842,39.9375,49.41999,39.92416), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5928,39.9825,49.48277,39.9836), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.48277,39.9836,45.5928,39.9825), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6039,40.0078,45.9056,40.0206), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9056,40.0206,45.6381,40.0217), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6381,40.0217,45.9056,40.0206), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.42554,40.04832,45.6381,40.0217), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9244,40.0925,45.9769,40.1169), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9769,40.1169,45.9244,40.0925), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.56221,40.20638,46.0028,40.2203), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0028,40.2203,50.38499,40.22137), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.38499,40.22137,46.0028,40.2203), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9133,40.2647,45.8758,40.2697), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8758,40.2697,45.9583,40.2722), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.9583,40.2722,45.8758,40.2697), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8414,40.3033,50.30888,40.30777), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.30888,40.30777,45.8414,40.3033), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.21138,40.33943,50.08554,40.34055), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.08554,40.34055,50.21138,40.33943), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.85805,40.3436,50.08554,40.34055), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.3636,40.35555,49.88527,40.36332), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.88527,40.36332,50.3636,40.35555), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6628,40.3758,49.88527,40.36332), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5403,40.4533,50.2361,40.49026), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.2361,40.49026,45.5069,40.5125), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5069,40.5125,50.2361,40.49026), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4692,40.5381,50.07193,40.55249), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((50.07193,40.55249,45.4692,40.5381), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.74582,40.57416,45.4592,40.5811), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4592,40.5811,49.99915,40.58638), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.99915,40.58638,45.4592,40.5811), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4206,40.6,49.99915,40.58638), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.57721,40.62527,45.3911,40.6433), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.3911,40.6433,49.57721,40.62527), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.3883,40.6711,49.51332,40.67805), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.51332,40.67805,45.3883,40.6711), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4292,40.7292,49.53777,40.7786), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.53777,40.7786,45.5942,40.7878), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5942,40.7878,49.53777,40.7786), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6208,40.8439,45.6181,40.8719), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.6181,40.8719,45.5661,40.8778), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5661,40.8778,45.6181,40.8719), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4878,40.9467,45.4372,40.9569), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4372,40.9569,45.4878,40.9467), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.25138,40.98721,45.345,41.0006), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.345,41.0006,49.25138,40.98721), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2328,41.0217,45.4389,41.0225), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4389,41.0225,45.2328,41.0217), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5208,41.05,46.4839,41.0556), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4839,41.0556,45.1328,41.0567), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1328,41.0567,46.4839,41.0556), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0914,41.0628,45.1328,41.0567), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1386,41.0828,45.1569,41.0856), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1569,41.0856,45.1386,41.0828), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5453,41.0953,46.4403,41.0964), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4403,41.0964,46.5453,41.0953), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.6225,41.1006,45.0733,41.1011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0733,41.1011,46.6225,41.1006), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3783,41.1042,45.0733,41.1011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5722,41.1081,46.3783,41.1042), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0814,41.1161,45.1947,41.1169), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1947,41.1169,45.0814,41.1161), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2228,41.1419,49.15499,41.15166), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.15499,41.15166,46.67,41.155), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.67,41.155,49.15499,41.15166), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.0314,41.1703,46.67,41.155), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.7669,41.1961,45.1478,41.2011), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.1478,41.2011,45.0528,41.2033), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0528,41.2033,46.1403,41.2039), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.1403,41.2039,45.0528,41.2033), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2433,41.2056,46.1403,41.2039), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.5717,41.2111,46.2433,41.2056), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.8131,41.2264,47.6597,41.2356), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.92,41.2264,47.6597,41.2356), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.6597,41.2356,45.8131,41.2264), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0603,41.2453,47.5219,41.2542), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.5219,41.2542,46.7108,41.2553), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.7108,41.2553,47.5219,41.2542), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.3711,41.2719,49.1361,41.27776), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((49.1361,41.27776,47.3711,41.2719), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7086,41.2903,47.9083,41.2908), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.9083,41.2908,45.7086,41.2903), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.0225,41.2986,47.9083,41.2908), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.2856,41.3122,45.7197,41.3206), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.6939,41.3122,45.7197,41.3206), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7197,41.3206,47.9539,41.3244), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.9539,41.3244,45.7197,41.3206), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.2658,41.3311,47.9539,41.3244), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.6283,41.34,45.7706,41.3433), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.7706,41.3433,46.6283,41.34), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.9625,41.3594,48.0228,41.3619), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.0228,41.3619,47.9625,41.3594), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.6211,41.365,48.0228,41.3619), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5933,41.3794,46.6211,41.365), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4992,41.3972,45.5383,41.3986), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.5383,41.3986,46.4992,41.3972), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.2589,41.4247,45.4011,41.4283), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4011,41.4283,47.2589,41.4247), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.4767,41.4408,45.4011,41.4283), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.2789,41.4556,46.4136,41.4625), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4136,41.4625,48.0744,41.4678), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((45.3367,41.4625,48.0744,41.4678), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.0744,41.4678,46.4136,41.4625), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.33,41.4853,47.2128,41.4886), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.2128,41.4886,46.33,41.4853), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.2314,41.5025,47.1781,41.5106), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.1781,41.5106,46.3353,41.5169), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3353,41.5169,47.1781,41.5106), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2992,41.5503,46.3436,41.5575), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3436,41.5575,47.0431,41.5583), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.0431,41.5583,46.3436,41.5575), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.1606,41.5611,47.0431,41.5583), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3472,41.5708,47.1278,41.5781), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.1278,41.5781,46.3472,41.5708), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.3997,41.5892,46.2567,41.5894), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2864,41.5892,46.2567,41.5894), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2567,41.5894,48.3997,41.5892), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3242,41.5936,46.2372,41.595), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2372,41.595,46.3242,41.5936), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.265,41.6119,46.2828,41.6153), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2828,41.6153,46.265,41.6119), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((47.0156,41.6272,46.2828,41.6153), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2017,41.6594,48.78832,41.66582), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.78832,41.66582,46.2017,41.6594), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.9325,41.6914,48.78832,41.66582), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.9256,41.7178,48.4714,41.7236), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.4714,41.7236,46.1981,41.725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.1981,41.725,48.4714,41.7236), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.8728,41.7289,46.1981,41.725), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.2189,41.7572,46.3319,41.7592), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.3319,41.7592,46.2189,41.7572), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.5314,41.7672,46.3319,41.7592), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5917,41.7922,46.7708,41.7967), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.7708,41.7967,46.5917,41.7922), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5667,41.8078,46.7708,41.7967), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.7256,41.8356,46.4206,41.8378), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.4206,41.8378,46.7256,41.8356), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((48.58904,41.84237,46.4206,41.8378), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.7608,41.8639,46.5683,41.8825), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.5683,41.8825,46.45209,41.89657), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.45209,41.89657,46.5683,41.8825), mapfile, tile_dir, 0, 11, "az-azerbaijan")
	render_tiles((46.45209,41.89657,46.5683,41.8825), mapfile, tile_dir, 0, 11, "az-azerbaijan")