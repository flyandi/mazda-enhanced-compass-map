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
    # Region: Louisiana
    # Region Name: LA

	render_tiles((-89.40097,28.93381,-89.14287,28.99162), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.14287,28.99162,-89.32201,29.01025), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.32201,29.01025,-89.40353,29.01696), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.40353,29.01696,-89.21867,29.02252), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.21867,29.02252,-89.40353,29.01696), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.14879,29.02967,-89.21867,29.02252), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.74838,29.04006,-90.81255,29.04214), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.81255,29.04214,-90.74838,29.04006), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.44273,29.05605,-90.86785,29.05606), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.86785,29.05606,-90.44273,29.05605), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.65212,29.05772,-89.25935,29.05836), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.25935,29.05836,-90.40947,29.05844), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.40947,29.05844,-89.25935,29.05836), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.48812,29.05876,-90.40947,29.05844), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.33494,29.0638,-90.48812,29.05876), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.3611,29.07185,-89.11653,29.0741), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.11653,29.0741,-89.3611,29.07185), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.22359,29.08508,-89.06662,29.09071), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.06662,29.09071,-90.22359,29.08508), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.87758,29.10489,-89.06662,29.09071), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.39052,29.12358,-90.87758,29.10489), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.12275,29.14429,-89.43293,29.14902), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.43293,29.14902,-90.12275,29.14429), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.94188,29.16237,-90.08984,29.16448), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.08984,29.16448,-90.94188,29.16237), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.01428,29.16691,-90.08984,29.16448), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.0001,29.16948,-89.01428,29.16691), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.05851,29.18369,-91.09402,29.18771), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.09402,29.18771,-90.05851,29.18369), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.48284,29.21505,-89.02597,29.21515), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.02597,29.21515,-89.48284,29.21505), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.15815,29.2181,-89.11665,29.21953), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.11665,29.21953,-91.15815,29.2181), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.56455,29.24253,-91.27879,29.24778), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.27879,29.24778,-89.60665,29.25202), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.60665,29.25202,-89.95646,29.25374), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.95646,29.25374,-89.60665,29.25202), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.13434,29.27934,-89.63966,29.29053), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.63966,29.29053,-89.90271,29.29304), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.90271,29.29304,-89.63966,29.29053), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.33489,29.29878,-89.72616,29.30403), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.72616,29.30403,-89.88346,29.3071), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.88346,29.3071,-89.72616,29.30403), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.84264,29.31882,-91.27665,29.32983), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.27665,29.32983,-89.25785,29.33687), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.25785,29.33687,-91.27665,29.32983), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.20039,29.34442,-89.25785,29.33687), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.26545,29.36098,-91.26632,29.36136), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.26632,29.36136,-91.26545,29.36098), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.31209,29.38804,-91.33405,29.39153), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.33405,29.39153,-89.38,29.39179), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.38,29.39179,-91.33405,29.39153), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.48232,29.40622,-89.38,29.39179), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.36397,29.42066,-89.53215,29.43457), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.53215,29.43457,-91.34751,29.44444), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.34751,29.44444,-89.53215,29.43457), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.46096,29.46996,-91.82158,29.47393), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.82158,29.47393,-91.46096,29.46996), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.76826,29.49036,-89.56961,29.49404), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.56961,29.49404,-91.39431,29.49712), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.39431,29.49712,-91.48559,29.49912), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.48559,29.49912,-91.39431,29.49712), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.91532,29.51851,-92.32347,29.5315), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.32347,29.5315,-91.53102,29.53154), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.53102,29.53154,-92.32347,29.5315), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.25186,29.53935,-89.56462,29.54379), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.56462,29.54379,-92.40986,29.54748), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.40986,29.54748,-89.56462,29.54379), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.47359,29.56108,-91.53745,29.56589), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.53745,29.56589,-91.71108,29.56933), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.71108,29.56933,-92.03019,29.57267), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.03019,29.57267,-91.71108,29.56933), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.56804,29.5774,-92.04289,29.57748), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.04289,29.57748,-92.56804,29.5774), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.15862,29.58162,-92.06451,29.58567), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.06451,29.58567,-92.61723,29.58906), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.61723,29.58906,-92.06451,29.58567), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.54197,29.59435,-91.80373,29.59595), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.80373,29.59595,-91.54197,29.59435), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.68449,29.605,-89.60211,29.6103), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.60211,29.6103,-92.68449,29.605), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.87327,29.62728,-91.64383,29.63063), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.64383,29.63063,-91.60018,29.63116), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.60018,29.63116,-89.50474,29.63151), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.50474,29.63151,-91.60018,29.63116), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.50097,29.63346,-89.50474,29.63151), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.5352,29.64857,-89.46556,29.65174), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.46556,29.65174,-89.5352,29.64857), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.86256,29.6674,-92.87999,29.68029), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.87999,29.68029,-89.40396,29.68181), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.40396,29.68181,-92.87999,29.68029), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.83797,29.69062,-91.62383,29.69924), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.62383,29.69924,-91.85307,29.70294), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.85307,29.70294,-91.62383,29.69924), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.79925,29.71526,-92.99313,29.72385), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.99313,29.72385,-93.8632,29.72406), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.8632,29.72406,-92.99313,29.72385), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.80814,29.7251,-93.8632,29.72406), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.74195,29.73634,-91.66713,29.74582), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.66713,29.74582,-93.08818,29.74913), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.08818,29.74913,-91.73725,29.74937), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.73725,29.74937,-93.08818,29.74913), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.27103,29.75636,-93.89082,29.76167), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.89082,29.76167,-93.53846,29.7633), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.53846,29.7633,-93.89082,29.76167), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.41109,29.76736,-93.17693,29.77049), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.17693,29.77049,-89.39916,29.77059), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.39916,29.77059,-93.17693,29.77049), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.29557,29.77507,-89.39916,29.77059), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.92921,29.80295,-89.29325,29.80305), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.29325,29.80305,-93.92921,29.80295), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.87245,29.85165,-89.64706,29.8636), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.64706,29.8636,-93.85231,29.87209), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.85231,29.87209,-89.70173,29.87409), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.70173,29.87409,-93.85231,29.87209), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.2363,29.87708,-89.70173,29.87409), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.59813,29.88141,-89.2363,29.87708), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.83037,29.89436,-89.59813,29.88141), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.74427,29.91765,-89.23118,29.92548), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.23118,29.92548,-89.74427,29.91765), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.79597,29.934,-89.23118,29.92548), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.85258,29.95272,-93.80782,29.95455), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.80782,29.95455,-89.85258,29.95272), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.57443,29.98374,-89.21568,29.99352), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.21568,29.99352,-89.57443,29.98374), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.8453,30.01638,-89.84507,30.01841), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.84507,30.01841,-89.8453,30.01638), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.74108,30.02157,-89.84507,30.01841), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.49406,30.04097,-89.78253,30.04537), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.78253,30.04537,-89.49406,30.04097), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70634,30.05218,-93.70394,30.05429), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70394,30.05429,-93.70634,30.05218), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.34216,30.05917,-89.44462,30.06096), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.44462,30.06096,-89.34216,30.05917), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.1858,30.06393,-89.44462,30.06096), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.68371,30.07602,-89.1858,30.06393), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.30303,30.09157,-89.68371,30.07602), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70244,30.11272,-89.65699,30.11838), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.65699,30.11838,-93.70244,30.11272), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.23317,30.13496,-89.60509,30.14281), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.60509,30.14281,-89.18326,30.14934), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.18326,30.14934,-89.60509,30.14281), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70376,30.17394,-89.5245,30.18075), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.5245,30.18075,-93.70376,30.17394), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.60766,30.2171,-93.71336,30.22526), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.71336,30.22526,-89.60766,30.2171), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.71106,30.24397,-93.71336,30.22526), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70719,30.27551,-93.71106,30.24397), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.63421,30.30826,-93.76033,30.32992), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.76033,30.32992,-89.63421,30.30826), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.74533,30.39702,-93.73854,30.40226), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.73854,30.40226,-93.74533,30.39702), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.67851,30.41401,-93.73854,30.40226), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.70267,30.42995,-89.67851,30.41401), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.69993,30.45404,-89.71249,30.47751), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.71249,30.47751,-89.69993,30.45404), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.71012,30.5064,-89.71249,30.47751), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.7292,30.54484,-89.79166,30.55152), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.79166,30.55152,-93.7292,30.54484), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.68433,30.59259,-93.68512,30.6252), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.68512,30.6252,-89.82187,30.64402), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.82187,30.64402,-93.68512,30.6252), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.82618,30.66882,-93.6299,30.67994), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.6299,30.67994,-89.82618,30.66882), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.83633,30.7272,-93.61769,30.73848), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.61769,30.73848,-89.83633,30.7272), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.5693,30.80297,-89.79175,30.82039), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.79175,30.82039,-93.5693,30.80297), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.55862,30.86942,-93.55458,30.87747), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.55458,30.87747,-93.55862,30.86942), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.75007,30.91293,-93.53094,30.92453), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.53094,30.92453,-89.75007,30.91293), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.54984,30.96712,-91.22407,30.99918), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.22407,30.99918,-91.17614,30.99922), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.17614,30.99922,-91.22407,30.99918), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.06013,30.99932,-91.17614,30.99922), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.63694,30.99942,-91.06013,30.99932), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.82583,30.99953,-90.75878,30.99958), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.75878,30.99958,-90.82583,30.99953), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.5672,30.99995,-90.54757,30.99998), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.54757,30.99998,-90.5672,30.99995), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.34724,31.00036,-90.25955,31.00066), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.34601,31.00036,-90.25955,31.00066), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.25955,31.00066,-90.34724,31.00036), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.89752,31.00191,-89.83591,31.0021), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.83591,31.0021,-89.89752,31.00191), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.72818,31.00231,-89.72815,31.00243), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-89.72815,31.00243,-89.72818,31.00231), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.62826,31.0051,-89.72815,31.00243), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.53953,31.0085,-91.62826,31.0051), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.56037,31.04951,-93.53122,31.05168), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.53122,31.05168,-91.56037,31.04951), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.59469,31.09144,-93.54028,31.12887), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.54028,31.12887,-91.62167,31.13687), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.62167,31.13687,-93.54028,31.12887), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.60244,31.18254,-93.6006,31.18262), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.6006,31.18262,-93.60244,31.18254), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.5525,31.18482,-93.5351,31.18561), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.5351,31.18561,-93.5525,31.18482), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.59099,31.192,-91.59005,31.19369), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.59005,31.19369,-91.59099,31.192), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.64436,31.23441,-93.61394,31.25938), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.61394,31.25938,-91.56419,31.26163), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.56419,31.26163,-93.61394,31.25938), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.62136,31.26781,-91.56419,31.26163), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.50886,31.29164,-93.67544,31.30104), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.67544,31.30104,-91.50886,31.29164), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.53606,31.33836,-93.66815,31.3751), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.66815,31.3751,-91.53234,31.39028), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.53234,31.39028,-93.66815,31.3751), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.6976,31.42841,-91.51036,31.43893), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.51036,31.43893,-93.6976,31.42841), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.74948,31.46869,-91.51714,31.49839), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.51714,31.49839,-93.72593,31.50409), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.72593,31.50409,-91.51714,31.49839), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.78769,31.52734,-91.48962,31.53427), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.48962,31.53427,-93.78769,31.52734), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.43762,31.54617,-91.48962,31.53427), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.83492,31.58621,-91.45752,31.58757), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.83492,31.58621,-91.45752,31.58757), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.45752,31.58757,-93.83492,31.58621), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.46382,31.62037,-93.81684,31.62251), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.81684,31.62251,-91.46382,31.62037), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.39572,31.64417,-93.81684,31.62251), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.80342,31.70069,-91.38092,31.73246), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.38092,31.73246,-91.38012,31.73263), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.38012,31.73263,-91.38092,31.73246), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.31858,31.74532,-91.32046,31.7478), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.32046,31.7478,-91.31858,31.74532), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.35951,31.79936,-93.85339,31.80547), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.85339,31.80547,-91.35951,31.79936), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.29014,31.83366,-91.34571,31.84286), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.34571,31.84286,-93.87825,31.84428), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.87825,31.84428,-91.34571,31.84286), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.24402,31.86973,-91.2349,31.87686), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.2349,31.87686,-91.24402,31.86973), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.90956,31.89314,-91.2349,31.87686), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.18111,31.92006,-93.97746,31.92642), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.97746,31.92642,-91.18111,31.92006), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.17741,31.97326,-94.02943,31.97969), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.02943,31.97969,-91.17741,31.97326), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.11741,31.98706,-94.04183,31.9924), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04183,31.9924,-91.11741,31.98706), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.08081,32.02346,-91.07911,32.05026), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.07911,32.05026,-91.08081,32.02346), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.03471,32.10105,-91.03947,32.10797), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.03947,32.10797,-91.03471,32.10105), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04268,32.13796,-91.03947,32.10797), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.0427,32.196,-91.10851,32.20815), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.10851,32.20815,-90.99123,32.21466), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.99123,32.21466,-91.10851,32.20815), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.94783,32.28349,-90.92117,32.34207), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.92117,32.34207,-90.98667,32.35176), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.98667,32.35176,-90.92117,32.34207), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04274,32.36356,-90.98667,32.35176), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04279,32.39228,-94.04274,32.36356), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-90.96599,32.42481,-91.05291,32.43844), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.05291,32.43844,-90.96599,32.42481), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.06052,32.51236,-91.01128,32.5166), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.01128,32.5166,-91.06052,32.51236), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04308,32.56426,-91.04876,32.5728), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.04876,32.5728,-91.04931,32.57362), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.04931,32.57362,-91.04876,32.5728), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.05529,32.57898,-91.04931,32.57362), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.07951,32.60068,-91.05529,32.57898), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.09876,32.68529,-94.04305,32.69303), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04305,32.69303,-91.09876,32.68529), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04305,32.69303,-91.09876,32.68529), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.057,32.72558,-91.11365,32.73997), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.11365,32.73997,-91.057,32.72558), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.15761,32.77603,-94.04303,32.79748), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04303,32.79748,-91.16167,32.81247), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.16167,32.81247,-94.04303,32.79748), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.13789,32.84898,-94.043,32.88109), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.043,32.88109,-91.0706,32.88866), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.0706,32.88866,-94.043,32.88109), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.07208,32.93783,-91.13441,32.98053), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.13441,32.98053,-91.16607,33.00411), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.16607,33.00411,-91.26456,33.00474), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.26456,33.00474,-91.16607,33.00411), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.43593,33.00584,-91.46039,33.006), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.46039,33.006,-91.43593,33.00584), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.48918,33.00618,-91.46039,33.006), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-91.87513,33.00773,-92.0691,33.00848), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.0691,33.00848,-92.22283,33.00908), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.06915,33.00848,-92.22283,33.00908), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.22283,33.00908,-92.0691,33.00848), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.50138,33.01216,-92.72355,33.01433), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.72355,33.01433,-92.72474,33.01434), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.72474,33.01434,-92.72355,33.01433), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.97114,33.01719,-92.98871,33.01725), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-92.98871,33.01725,-92.97114,33.01719), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.1974,33.01795,-93.23861,33.01802), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.23861,33.01802,-93.1974,33.01795), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.37713,33.01823,-93.23861,33.01802), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.49051,33.01863,-93.52099,33.01874), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.52099,33.01874,-93.49051,33.01863), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-94.04296,33.01922,-93.81455,33.01939), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.81455,33.01939,-93.80491,33.0194), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.80491,33.0194,-93.81455,33.01939), mapfile, tile_dir, 0, 11, "louisiana-la")
	render_tiles((-93.72327,33.01946,-93.80491,33.0194), mapfile, tile_dir, 0, 11, "louisiana-la")