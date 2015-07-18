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
    # Region: BG
    # Region Name: Bulgaria

	render_tiles((25.23943,41.25443,25.38805,41.26305), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.38805,41.26305,25.23943,41.25443), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.20999,41.29388,25.65305,41.31776), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.65305,41.31776,23.17582,41.32276), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.17582,41.32276,25.65305,41.31776), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.80583,41.33387,22.93595,41.3433), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.93595,41.3433,25.80583,41.33387), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.13527,41.35387,24.79888,41.35471), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.79888,41.35471,26.13527,41.35387), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.96499,41.39443,23.28111,41.40331), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.28111,41.40331,23.66805,41.40582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.66805,41.40582,23.28111,41.40331), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.89471,41.41054,23.66805,41.40582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.72527,41.41805,24.6586,41.4211), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.6586,41.4211,24.72527,41.41805), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.19805,41.43942,23.9561,41.44609), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.9561,41.44609,26.19805,41.43942), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.8911,41.45277,23.9561,41.44609), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.07499,41.4672,23.8911,41.45277), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.18472,41.51665,24.36388,41.5236), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.36388,41.5236,24.18472,41.51665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.07861,41.53721,24.15722,41.5411), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.15722,41.5411,24.07861,41.53721), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.30471,41.5486,24.15722,41.5411), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.24554,41.56776,24.5261,41.57221), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.5261,41.57221,24.24554,41.56776), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.09972,41.63665,22.98555,41.66137), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.98555,41.66137,26.06749,41.67887), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.06749,41.67887,22.98555,41.66137), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.36188,41.70155,26.07444,41.71193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.07444,41.71193,23.03139,41.72054), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.03139,41.72054,26.07444,41.71193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.22277,41.74443,26.33277,41.75304), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.33277,41.75304,26.22277,41.74443), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.01277,41.76527,26.33277,41.75304), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.94749,41.80248,26.37749,41.82193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.37749,41.82193,26.54499,41.83082), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.54499,41.83082,26.37749,41.82193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.56666,41.90776,27.57972,41.93748), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.57972,41.93748,27.82166,41.96665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.82166,41.96665,26.84333,41.97221), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.84333,41.97221,27.82166,41.96665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.01305,41.9822,26.62527,41.98332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.62527,41.98332,28.01305,41.9822), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.83472,42.00193,26.62527,41.98332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.0036,42.02388,27.83472,42.00193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.80333,42.04777,27.20166,42.06054), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.20166,42.06054,22.80333,42.04777), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.07027,42.08998,22.60944,42.10332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.60944,42.10332,27.2436,42.10721), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.2436,42.10721,22.60944,42.10332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.74805,42.25971,22.40777,42.27943), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.40777,42.27943,27.74805,42.25971), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.78583,42.31138,22.36361,42.31998), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.36361,42.31998,22.36942,42.32293), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.36942,42.32293,22.36361,42.31998), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.73,42.33471,22.36942,42.32293), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.71694,42.38082,22.51944,42.39915), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.51944,42.39915,27.65333,42.41415), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.65333,42.41415,22.51944,42.39915), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.50194,42.43332,27.63721,42.45193), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.63721,42.45193,27.50194,42.43332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.55805,42.47915,27.45055,42.48082), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.45055,42.48082,22.55805,42.47915), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.48499,42.55582,27.65222,42.55666), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.65222,42.55666,22.48499,42.55582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.53083,42.56194,27.65222,42.55666), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.4386,42.57471,27.53083,42.56194), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.6325,42.63165,27.71388,42.67666), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.71388,42.67666,27.89833,42.70721), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.89833,42.70721,27.73277,42.71138), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.73277,42.71138,27.89833,42.70721), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.46194,42.79388,22.44083,42.8186), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.44083,42.8186,22.46194,42.79388), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.73333,42.88971,22.58583,42.89276), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.58583,42.89276,22.73333,42.88971), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.89527,42.92138,22.58583,42.89276), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.99333,43.14526,27.945,43.16721), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.945,43.16721,22.99333,43.14526), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.90694,43.19582,23.00694,43.19999), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.00694,43.19999,27.90694,43.19582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.01583,43.22582,23.00694,43.19999), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.84972,43.2822,28.01583,43.22582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.08805,43.3636,28.47138,43.36638), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.47138,43.36638,28.08805,43.3636), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.76971,43.3836,28.47138,43.36638), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.15722,43.40554,28.38194,43.41332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.38194,43.41332,28.15722,43.40554), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.56138,43.45832,22.54639,43.47026), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.54639,43.47026,28.56138,43.45832), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.60638,43.54388,28.57277,43.60082), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.57277,43.60082,22.4886,43.62331), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.4886,43.62331,25.36305,43.6247), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.36305,43.6247,22.4886,43.62331), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.57293,43.64828,25.36305,43.6247), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.22471,43.68748,25.08055,43.69109), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.08055,43.69109,24.33722,43.69443), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.33722,43.69443,25.08055,43.69109), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.00138,43.72359,28.58291,43.74483), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.58291,43.74483,28.58384,43.74776), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.58384,43.74776,28.58291,43.74483), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.89055,43.75638,24.50166,43.76138), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((24.50166,43.76138,25.86277,43.76638), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((25.86277,43.76638,24.50166,43.76138), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((28.22499,43.77304,25.86277,43.76638), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.06361,43.80221,22.35944,43.81693), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.35944,43.81693,23.06361,43.80221), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.85749,43.85332,23.41305,43.85582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.41305,43.85582,22.85749,43.85332), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.99721,43.85915,23.41305,43.85582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.84333,43.90498,27.99721,43.85915), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.72166,43.96471,27.82027,43.96582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.82027,43.96582,27.72166,43.96471), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.11166,43.96832,27.82027,43.96582), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.95166,43.97887,26.11166,43.96832), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.90694,43.99887,27.91888,44.00555), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.91888,44.00555,22.41693,44.00694), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.41693,44.00694,27.91888,44.00555), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.99582,44.01415,22.41693,44.00694), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.39999,44.02193,22.99582,44.01415), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.6561,44.04249,26.4786,44.04943), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.4786,44.04943,27.6561,44.04249), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.04277,44.05665,26.4786,44.04943), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.6225,44.07082,23.03722,44.08443), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((23.03722,44.08443,27.29055,44.08804), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.29055,44.08804,23.03722,44.08443), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.88277,44.12887,27.27555,44.13304), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.27555,44.13304,26.92332,44.13665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((27.27555,44.13304,26.92332,44.13665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((26.92332,44.13665,27.27555,44.13304), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.61666,44.17276,26.92332,44.13665), mapfile, tile_dir, 0, 11, "bg-bulgaria")
	render_tiles((22.69193,44.24306,22.61666,44.17276), mapfile, tile_dir, 0, 11, "bg-bulgaria")