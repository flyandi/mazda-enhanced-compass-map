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
    # Region: TR
    # Region Name: Turkey

	render_tiles((26.18111,40.04527,26.15722,40.05527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.15722,40.05527,26.18111,40.04527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.32194,40.1086,26.15722,40.05527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.35138,40.20582,26.27083,40.24638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.27083,40.24638,26.35138,40.20582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.55083,40.30638,26.20833,40.32027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.20833,40.32027,26.55083,40.30638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.62249,40.39249,26.20833,40.32027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.55083,40.48471,26.90472,40.5436), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.90472,40.5436,26.82444,40.58415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.82444,40.58415,26.13833,40.59277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.13833,40.59277,26.82444,40.58415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.08305,40.60666,26.35055,40.60721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.35055,40.60721,26.08305,40.60666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.80527,40.63749,26.77916,40.65943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.77916,40.65943,26.80527,40.63749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.27361,40.69166,26.77916,40.65943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.03331,40.73489,26.12471,40.74999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.12471,40.74999,26.03331,40.73489), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.13416,40.78137,26.12471,40.74999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.19999,40.82887,26.2036,40.86276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.2036,40.86276,26.19999,40.82887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.32722,40.94193,28.64277,40.96054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.64277,40.96054,28.5975,40.96915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.5975,40.96915,27.95805,40.97193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.95805,40.97193,28.5975,40.96915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.90583,40.97971,27.50972,40.9836), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.50972,40.9836,28.54305,40.98721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.54305,40.98721,27.50972,40.9836), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.56777,41.02304,28.01083,41.03555), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.01083,41.03555,28.56777,41.02304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.57138,41.06443,28.1786,41.07638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.1786,41.07638,28.52499,41.07915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.52499,41.07915,28.1786,41.07638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.05666,41.0861,28.52499,41.07915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.03833,41.15694,29.07305,41.17666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.07305,41.17666,26.32083,41.18471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.32083,41.18471,29.07305,41.17666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.10361,41.23777,26.44944,41.28526), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.44944,41.28526,29.10361,41.23777), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.61777,41.33693,28.63555,41.35499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.63555,41.35499,26.61777,41.33693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.63721,41.37859,28.63555,41.35499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.19971,41.52888,26.59361,41.6111), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.59361,41.6111,26.49582,41.66443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.49582,41.66443,26.36188,41.70155), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.36188,41.70155,28.05111,41.71027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.36188,41.70155,28.05111,41.71027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.05111,41.71027,26.36188,41.70155), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.33277,41.75304,28.05111,41.71027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.37749,41.82193,26.54499,41.83082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.54499,41.83082,26.37749,41.82193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.96333,41.85666,26.54499,41.83082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.04749,41.88943,27.98388,41.89138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.98388,41.89138,28.04749,41.88943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.56666,41.90776,27.98388,41.89138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.57972,41.93748,27.82166,41.96665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.82166,41.96665,26.84333,41.97221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.84333,41.97221,27.82166,41.96665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.01249,41.98055,28.01305,41.9822), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.01305,41.9822,26.62527,41.98332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.62527,41.98332,28.01305,41.9822), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.83472,42.00193,26.62527,41.98332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.20166,42.06054,27.07027,42.08998), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.07027,42.08998,27.2436,42.10721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.2436,42.10721,27.07027,42.08998), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.17416,35.82166,36.0172,35.87832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.0172,35.87832,35.92324,35.92677), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.92324,35.92677,36.00582,35.93054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.00582,35.93054,35.92324,35.92677), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.21915,35.96027,36.00582,35.93054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.98026,35.99554,36.37389,36.01321), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.37389,36.01321,32.80554,36.0211), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.80554,36.0211,36.37389,36.01321), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.09415,36.07471,32.93888,36.09721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.93888,36.09721,33.09415,36.07471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.67055,36.13138,33.54276,36.13165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.54276,36.13165,29.67055,36.13138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.68999,36.1336,33.54276,36.13165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.4286,36.14304,33.68999,36.1336), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.45638,36.15527,32.4286,36.14304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.70527,36.17944,33.60638,36.1811), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.60638,36.1811,33.70527,36.17944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.65443,36.18748,33.60638,36.1811), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.61861,36.19999,29.89333,36.20304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.89333,36.20304,30.40694,36.2036), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.40694,36.2036,29.89333,36.20304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.44082,36.20638,30.40694,36.2036), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.96471,36.21526,36.38251,36.22286), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.38251,36.22286,36.55499,36.22304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.55499,36.22304,36.38251,36.22286), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.41555,36.2286,36.4936,36.23081), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.4936,36.23081,29.41555,36.2286), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.09583,36.23693,36.69221,36.23915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.69221,36.23915,30.09583,36.23693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.83332,36.24527,29.32027,36.24638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.32027,36.24638,35.83332,36.24527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.9411,36.26221,29.39472,36.26332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.39472,36.26332,33.9411,36.26221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.35722,36.26638,29.39472,36.26332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.69026,36.28638,30.48444,36.28721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.48444,36.28721,36.69026,36.28638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.14583,36.28971,34.00555,36.28999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.00555,36.28999,30.14583,36.28971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.27082,36.29582,35.78333,36.29638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.78333,36.29638,32.27082,36.29582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.22138,36.3036,35.78333,36.29638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.88304,36.31248,34.07693,36.31944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.07693,36.31944,33.88304,36.31248), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.81888,36.35499,36.61859,36.35749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.61859,36.35749,35.81888,36.35499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.58082,36.40054,30.47749,36.40499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.47749,36.40499,34.08054,36.4086), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.08054,36.4086,30.47749,36.40499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.12444,36.41444,34.08054,36.4086), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.5536,36.4986,35.33999,36.53915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.33999,36.53915,32.02388,36.53944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.02388,36.53944,35.33999,36.53915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.01999,36.54137,32.02388,36.53944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.10444,36.54887,27.98805,36.55415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.98805,36.55415,29.10444,36.54887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.11027,36.56471,30.58638,36.5711), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.58638,36.5711,36.11027,36.56471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.55666,36.57915,30.58638,36.5711), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.49638,36.58777,28.83527,36.59138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.83527,36.59138,34.28971,36.59165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.28971,36.59165,28.83527,36.59138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.60971,36.59332,34.28971,36.59165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.95888,36.59693,28.05083,36.59776), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.05083,36.59776,27.95888,36.59693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.86833,36.60082,28.05083,36.59776), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.49888,36.61193,28.11666,36.61304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.11666,36.61304,35.49888,36.61193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.5536,36.61582,28.11666,36.61304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.07693,36.62165,35.63582,36.6261), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.63582,36.6261,29.1161,36.62832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.1161,36.62832,35.63582,36.6261), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.08666,36.63832,28.87639,36.63915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.87639,36.63915,29.07471,36.63943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.07471,36.63943,28.87639,36.63915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.44248,36.63998,29.07471,36.63943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.2111,36.64082,37.44248,36.63998), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.65777,36.65582,37.02443,36.65804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.02443,36.65804,27.67194,36.65833), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.67194,36.65833,37.02443,36.65804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.12748,36.65915,37.50555,36.65971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.50555,36.65971,37.12748,36.65915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.40583,36.66165,37.50555,36.65971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.20582,36.66553,28.79499,36.66916), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.79499,36.66916,39.20582,36.66553), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.51194,36.67443,28.79499,36.66916), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.04638,36.68526,27.98166,36.68582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.98166,36.68582,29.04638,36.68526), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.04472,36.68777,27.98166,36.68582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.63165,36.69415,28.22333,36.69554), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.22333,36.69554,35.63165,36.69415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.86054,36.69776,28.62235,36.69878), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.62235,36.69878,39.43923,36.69884), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.43923,36.69884,28.62235,36.69878), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.67444,36.69943,39.43923,36.69884), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.72637,36.70221,27.3586,36.70332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.3586,36.70332,38.72637,36.70221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.57638,36.71027,37.04305,36.71443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.04305,36.71443,34.99082,36.71665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.99082,36.71665,37.04305,36.71443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.63916,36.71915,28.12194,36.71944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.69138,36.71915,28.12194,36.71944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.12194,36.71944,28.63916,36.71915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.28444,36.72276,28.12194,36.71944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.96444,36.74638,39.80998,36.75138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.80998,36.75138,28.92916,36.75249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.92916,36.75249,39.80998,36.75138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.73194,36.75694,37.8186,36.76082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.8186,36.76082,27.73194,36.75694), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.10444,36.76582,35.79777,36.76749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.79777,36.76749,28.10444,36.76582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.20582,36.77082,36.95554,36.7711), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.95554,36.7711,36.20582,36.77082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.75916,36.7811,28.39861,36.78165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.39861,36.78165,27.75916,36.7811), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.60387,36.78555,28.07666,36.78832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.07666,36.78832,34.60387,36.78555), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.12333,36.79499,28.05027,36.79999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.67277,36.79499,28.05027,36.79999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.05027,36.79999,28.23416,36.80027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.23416,36.80027,28.05027,36.79999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.58527,36.80249,28.23416,36.80027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.77721,36.8086,27.89888,36.80943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.89888,36.80943,34.77721,36.8086), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.46638,36.81082,31.31083,36.81165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.31083,36.81165,28.46638,36.81082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.41805,36.81554,31.31083,36.81165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.56277,36.82304,28.30388,36.8286), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.30388,36.8286,36.66026,36.8336), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.66026,36.8336,40.04832,36.83554), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.04832,36.83554,36.66026,36.8336), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.37638,36.84721,28.26749,36.84749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.26749,36.84749,28.37638,36.84721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.83833,36.84971,28.26749,36.84749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.02027,36.85582,30.83833,36.84971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.45861,36.88026,30.68277,36.88527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.68277,36.88527,28.04277,36.88915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.04277,36.88915,30.68277,36.88527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.39082,36.89665,28.04277,36.88915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.1811,36.90582,36.07138,36.90804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.07138,36.90804,38.1811,36.90582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.01693,36.92638,28.04166,36.93332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.04166,36.93332,28.18416,36.93748), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.18416,36.93748,28.04166,36.93332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.27222,36.95582,44.31721,36.97054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.31721,36.97054,27.27222,36.95582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.25526,36.98665,40.40026,36.99026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.40026,36.99026,44.25526,36.98665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.55888,36.99026,44.25526,36.98665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.33833,36.99527,40.40026,36.99026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.26722,37.01443,27.93333,37.02499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.93333,37.02499,28.3275,37.02554), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.3275,37.02554,27.93333,37.02499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.3236,37.04499,44.3511,37.04832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.3511,37.04832,28.3236,37.04499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.22555,37.05193,44.3511,37.04832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.40054,37.07693,27.46888,37.07832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.46888,37.07832,41.40054,37.07693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.1411,37.09443,42.36609,37.11018), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.36609,37.11018,44.19776,37.1111), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.19776,37.1111,42.36609,37.11018), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.77554,37.11832,44.52915,37.12026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.52915,37.12026,40.77554,37.11832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.84026,37.12998,44.52915,37.12026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.78667,37.14815,27.32583,37.15415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.32583,37.15415,44.78667,37.14815), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.79443,37.16387,27.54361,37.16444), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.54361,37.16444,44.79443,37.16387), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.26776,37.16749,27.54361,37.16444), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.6011,37.18665,44.63804,37.18748), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.63804,37.18748,42.6011,37.18665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.0636,37.19665,27.56388,37.20471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.56388,37.20471,42.0636,37.19665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.90359,37.22165,44.77332,37.22748), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.77332,37.22748,43.62304,37.22998), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.62304,37.22998,44.77332,37.22748), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.34526,37.23859,44.26166,37.24193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.26166,37.24193,42.34526,37.23859), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.46861,37.24888,44.26166,37.24193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.61361,37.26332,44.82193,37.2711), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.82193,37.2711,27.56388,37.2736), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.56388,37.2736,44.82193,37.2711), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.23109,37.27637,42.24971,37.27887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.24971,37.27887,44.23109,37.27637), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.41722,37.30082,43.30637,37.30998), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.30637,37.30998,44.00388,37.31499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.00388,37.31499,44.11638,37.31638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.11638,37.31638,44.00388,37.31499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.80165,37.32166,42.20787,37.32218), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.20787,37.32218,42.95054,37.32249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.95054,37.32249,42.20787,37.32218), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.49638,37.32499,42.95054,37.32249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.34582,37.33193,27.49638,37.32499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.38555,37.33943,43.34582,37.33193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.20194,37.34888,42.72665,37.35555), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.72665,37.35555,27.20194,37.34888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.32471,37.36943,43.14443,37.37804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.14443,37.37804,42.7861,37.38443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.7861,37.38443,27.43444,37.38999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.43444,37.38999,42.7861,37.38443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.41444,37.41055,27.43444,37.38999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.59276,37.43693,27.41444,37.41055), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.20222,37.4736,44.59276,37.43693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.19472,37.60165,44.55804,37.64804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.55804,37.64804,27.00583,37.65527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.00583,37.65527,44.55804,37.64804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.04305,37.6836,27.2136,37.70805), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.2136,37.70805,44.61887,37.71609), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.61887,37.71609,27.2136,37.70805), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.2486,37.73999,44.61887,37.71609), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.53804,37.78054,44.41915,37.81776), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.41915,37.81776,44.53804,37.78054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.34499,37.88026,44.24554,37.88387), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.24554,37.88387,44.34499,37.88026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.22221,37.90637,44.24554,37.88387), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.26944,37.94749,27.21167,37.98499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.21167,37.98499,27.07805,38.01054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.07805,38.01054,26.87111,38.03082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.87111,38.03082,27.07805,38.01054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.98527,38.06527,26.87111,38.03082), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.61083,38.10332,26.98527,38.06527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.51416,38.15804,26.64333,38.20277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.64333,38.20277,26.77222,38.20888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.77222,38.20888,26.64333,38.20277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.30194,38.24888,26.23333,38.26721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.23333,38.26721,26.30194,38.24888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.36944,38.3061,26.68222,38.30749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.68222,38.30749,26.36944,38.3061), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.47776,38.32304,26.64444,38.33749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.64444,38.33749,26.31972,38.34221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.31972,38.34221,26.64444,38.33749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.78916,38.35499,26.47389,38.36582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.47389,38.36582,26.78916,38.35499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.32526,38.37804,26.47389,38.36582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.44082,38.39332,26.67999,38.40749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.67999,38.40749,26.73861,38.41666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.73861,38.41666,26.67999,38.40749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.51083,38.42805,44.3036,38.43054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.3036,38.43054,26.51083,38.42805), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.16194,38.44388,44.3036,38.43054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.9086,38.46082,26.605,38.46582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.605,38.46582,26.42916,38.47027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.42916,38.47027,26.91777,38.47221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.91777,38.47221,26.42916,38.47027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.63416,38.50555,26.91777,38.47221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.36916,38.54749,26.83416,38.55138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.83416,38.55138,26.36916,38.54749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.84694,38.59527,44.31721,38.61304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.31721,38.61304,26.34722,38.62999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.34722,38.62999,26.52499,38.64416), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.52499,38.64416,26.72333,38.64888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.72333,38.64888,26.52499,38.64416), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.40777,38.67332,26.72333,38.64888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.26082,38.72165,26.73222,38.73026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.73222,38.73026,44.26082,38.72165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.93194,38.75388,26.73222,38.73026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.89888,38.81416,44.30499,38.81554), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.30499,38.81554,26.89888,38.81416), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.2911,38.85971,27.05972,38.86888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.05972,38.86888,44.2911,38.85971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.21082,38.8911,27.05972,38.86888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.05583,38.92666,26.80277,38.95443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.80277,38.95443,27.05583,38.92666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.15942,39.00221,26.79749,39.02415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.79749,39.02415,44.15942,39.00221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.88583,39.06944,26.79749,39.02415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.21665,39.12915,26.82055,39.1511), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.82055,39.1511,44.21665,39.12915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.73138,39.2036,26.72611,39.24999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.72611,39.24999,26.61305,39.26804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.61305,39.26804,26.72611,39.24999), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.6361,39.29887,44.08082,39.30693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.08082,39.30693,26.68944,39.30943), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.68944,39.30943,44.08082,39.30693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.03416,39.37971,44.30776,39.38693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.30776,39.38693,44.03416,39.37971), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.07027,39.41165,44.41609,39.42526), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.41609,39.42526,44.07027,39.41165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.13388,39.45304,26.07305,39.47027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.07305,39.47027,26.8586,39.47054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.8586,39.47054,26.07305,39.47027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.93666,39.48249,26.8586,39.47054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.48972,39.52388,26.95166,39.55276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.95166,39.55276,26.92861,39.5811), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.92861,39.5811,26.10138,39.58166), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.10138,39.58166,26.92861,39.5811), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.15499,39.63221,44.81049,39.64273), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.81049,39.64273,44.79465,39.65065), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.79465,39.65065,44.81049,39.64273), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.78999,39.69859,44.47109,39.69887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.47109,39.69887,44.78999,39.69859), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.77858,39.70665,44.47109,39.69887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.60582,39.78054,44.62526,39.81499), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.62526,39.81499,44.60582,39.78054), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.54027,39.91998,26.16972,39.96944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.16972,39.96944,26.20028,40.00582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.20028,40.00582,44.01693,40.0086), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.01693,40.0086,26.20028,40.00582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.31694,40.01638,44.01693,40.0086), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((44.28027,40.04665,26.31694,40.01638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.65776,40.1086,43.65276,40.13416), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.65276,40.13416,26.40389,40.15443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.40389,40.15443,43.72109,40.16721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.72109,40.16721,26.40389,40.15443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.40861,40.19332,26.51083,40.21416), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.51083,40.21416,26.40861,40.19332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.50888,40.30582,27.77749,40.31388), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.77749,40.31388,27.50888,40.30582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.59443,40.33749,27.77749,40.31388), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.04028,40.36388,27.9175,40.36694), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.9175,40.36694,29.04028,40.36388), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.88,40.37332,27.9175,40.36694), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.30527,40.38638,28.74722,40.3886), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.74722,40.3886,27.30527,40.38638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.02666,40.3911,28.38805,40.39277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.38805,40.39277,27.02666,40.3911), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.61804,40.39554,27.89833,40.39582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.89833,40.39582,43.61804,40.39554), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((26.78971,40.39888,27.89833,40.39582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.32694,40.41221,26.78971,40.39888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.14666,40.42944,43.59276,40.44026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.59276,40.44026,27.08472,40.44693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.08472,40.44693,43.59276,40.44026), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.03083,40.46749,27.27722,40.47249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.27722,40.47249,29.08444,40.47638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.08444,40.47638,27.27722,40.47249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.68889,40.48221,29.08444,40.47638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.015,40.49165,27.68889,40.48221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.79472,40.51665,27.72083,40.52165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.72083,40.52165,28.79472,40.51665), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((27.77833,40.52749,27.72083,40.52165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.79527,40.55138,27.77833,40.52749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((28.98638,40.64193,43.75353,40.67593), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.75353,40.67593,29.55111,40.6861), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.55111,40.6861,29.41222,40.68694), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.41222,40.68694,29.55111,40.6861), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.51055,40.73277,29.35611,40.75888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.35611,40.75888,29.92305,40.76138), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.92305,40.76138,29.35611,40.75888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.46694,40.77415,43.73582,40.78249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.73582,40.78249,29.46694,40.77415), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.33055,40.8136,43.73582,40.78249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.11054,40.91666,38.48943,40.91832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.48943,40.91832,40.11054,40.91666), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.18748,40.93193,38.48943,40.91832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.85443,40.9575,29.07222,40.96249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.07222,40.96249,38.07193,40.96526), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.07193,40.96526,29.07222,40.96249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.99635,40.97431,38.07193,40.96526), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.81666,40.98471,43.60638,40.98832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.60638,40.98832,39.81666,40.98471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.55415,40.99804,29.01777,41.00721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.01777,41.00721,38.86582,41.01388), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((38.86582,41.01388,29.01777,41.00721), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.39832,41.02055,38.86582,41.01388), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.5311,41.02888,40.54332,41.02915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.54332,41.02915,37.5311,41.02888), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.80415,41.03915,43.47359,41.04193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.47359,41.04193,37.80415,41.03915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.26305,41.04916,43.47359,41.04193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.47249,41.06721,30.87971,41.07693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.87971,41.07693,39.16305,41.08249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.16305,41.08249,37.6386,41.08527), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.6386,41.08527,39.16305,41.08249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.73083,41.09915,43.47324,41.10621), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.47324,41.10621,31.27694,41.10832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.27694,41.10832,39.4211,41.10944), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((39.4211,41.10944,31.27694,41.10832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.68304,41.1361,37.23221,41.14165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.23221,41.14165,43.47304,41.14304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.47304,41.14304,37.23221,41.14165), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.19805,41.1536,29.72027,41.15887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.72027,41.15887,30.19805,41.1536), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.77915,41.16832,29.72027,41.15887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.04082,41.17805,43.23304,41.17832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.23304,41.17832,37.04082,41.17805), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((40.9311,41.18748,43.23304,41.17832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.40277,41.19666,43.37526,41.20221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.37526,41.20221,29.12416,41.2036), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.12416,41.2036,43.37526,41.20221), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((30.28055,41.21193,29.12416,41.2036), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((29.24583,41.23582,36.42915,41.24277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.42915,41.24277,36.48527,41.24443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.48527,41.24443,36.42915,41.24277), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.1236,41.25526,36.48527,41.24443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.19942,41.25526,36.48527,41.24443), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((37.02332,41.26749,41.10138,41.27471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.10138,41.27471,37.02332,41.26749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.39583,41.3061,43.20776,41.30693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.20776,41.30693,31.39583,41.3061), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((43.16081,41.31276,43.20776,41.30693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.91888,41.3211,43.16081,41.31276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.2736,41.33887,36.61249,41.34749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.61249,41.34749,36.2736,41.33887), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.78138,41.35693,36.61249,41.34749), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.39193,41.37638,36.78138,41.35693), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.83332,41.42832,42.50665,41.44193), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.50665,41.44193,41.83332,41.42832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((31.77999,41.4561,42.36693,41.46027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.36693,41.46027,31.77999,41.4561), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.84721,41.47304,42.36693,41.46027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.11694,41.48943,42.84721,41.47304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.8886,41.50804,42.78638,41.51027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.78638,41.51027,42.8886,41.50804), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.95499,41.51638,42.78638,41.51027), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.5312,41.52348,41.95499,41.51638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((41.5312,41.52348,41.95499,41.51638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.58804,41.57638,42.83193,41.58332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.83193,41.58332,42.58804,41.57638), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((42.72637,41.59109,42.83193,41.58332), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((36.13082,41.59915,32.14943,41.60471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.14943,41.60471,36.13082,41.59915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.57665,41.62721,32.14943,41.60471), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.94444,41.71027,32.28221,41.72276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.28221,41.72276,35.26527,41.72582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.26527,41.72582,32.28221,41.72276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.97305,41.73221,35.26527,41.72582), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((32.6561,41.83249,35.09332,41.92304), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.09332,41.92304,33.07054,41.9386), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.07054,41.9386,34.71555,41.94249), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.71555,41.94249,33.07054,41.9386), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.47471,41.96221,34.83249,41.96832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.83249,41.96832,35.10165,41.97276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.10165,41.97276,34.83249,41.96832), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((34.05638,41.97749,35.10165,41.97276), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((33.51777,42.00694,35.21138,42.02055), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.21138,42.02055,35.06638,42.02915), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.06638,42.02915,35.21138,42.02055), mapfile, tile_dir, 0, 11, "tr-turkey")
	render_tiles((35.02287,42.08819,35.06638,42.02915), mapfile, tile_dir, 0, 11, "tr-turkey")