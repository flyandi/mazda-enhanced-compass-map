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
    # Zone: us
    # Region: Nebraska
    # Region Name: NE

	render_tiles((-95.30829,40,-95.3399,40.00003), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.3399,40.00003,-95.30829,40), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.78458,40.00046,-95.78811,40.00047), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.78811,40.00047,-95.78458,40.00046), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.15437,40.0005,-95.78811,40.00047), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.23921,40.00069,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.23917,40.00069,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.01068,40.0007,-96.23921,40.00069), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.02409,40.00072,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.46371,40.00096,-96.46995,40.00097), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.46995,40.00097,-96.46371,40.00096), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.80577,40.00137,-99.8134,40.0014), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.8134,40.0014,-96.80577,40.00137), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.87381,40.00145,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.91641,40.00145,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.00917,40.00146,-96.87381,40.00145), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.1936,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.19359,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.1778,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.47702,40.00175,-99.62825,40.00177), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.62825,40.00177,-99.62533,40.00178), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.62533,40.00178,-99.62825,40.00177), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.3692,40.00194,-97.41583,40.002), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.41583,40.002,-99.50179,40.00203), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.50179,40.00203,-97.41583,40.002), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.17913,40.00211,-99.0856,40.00213), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.0856,40.00213,-99.06702,40.00214), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.06702,40.00214,-99.0856,40.00213), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.77716,40.00217,-97.8215,40.00219), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.8215,40.00219,-97.77716,40.00217), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.93182,40.00224,-100.73882,40.00226), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.73882,40.00226,-97.93182,40.00224), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.75883,40.0023,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.07603,40.0023,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.06032,40.00231,-100.75883,40.0023), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.72637,40.00234,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.27402,40.00234,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.50445,40.00238,-98.61376,40.0024), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.61376,40.0024,-98.50445,40.00238), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.29399,40.00256,-101.32551,40.00257), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.32551,40.00257,-101.29399,40.00256), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.41103,40.00258,-101.32551,40.00257), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.54227,40.00261,-101.41103,40.00258), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.83216,40.00293,-102.05174,40.00308), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05174,40.00308,-101.83216,40.00293), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.38296,40.02711,-95.34878,40.0293), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.34878,40.0293,-95.38296,40.02711), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.41473,40.06982,-95.39422,40.10826), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.39422,40.10826,-95.43217,40.14103), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.43217,40.14103,-95.39422,40.10826), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.48102,40.18852,-95.43217,40.14103), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.47255,40.23608,-95.54716,40.25907), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.54716,40.25907,-95.54787,40.26278), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.54787,40.26278,-95.54818,40.26441), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.54818,40.26441,-95.54787,40.26278), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.55329,40.29116,-95.59866,40.30981), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.59866,40.30981,-95.65373,40.32258), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.65373,40.32258,-95.59866,40.30981), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05131,40.33838,-102.05131,40.34922), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05131,40.34922,-102.05131,40.33838), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.64103,40.3664,-102.05131,40.34922), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.64942,40.39615,-95.64103,40.3664), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.0513,40.44001,-95.68436,40.46337), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.68436,40.46337,-102.0513,40.44001), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.69473,40.4936,-95.71228,40.52375), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.71228,40.52375,-95.75711,40.52599), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.75711,40.52599,-95.71429,40.52721), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.71429,40.52721,-95.75711,40.52599), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.76565,40.58521,-95.74863,40.60336), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.74863,40.60336,-95.76565,40.58521), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.78191,40.65327,-95.84603,40.68261), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.84603,40.68261,-102.05129,40.69755), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05129,40.69755,-95.84603,40.68261), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.8887,40.73629,-102.05129,40.74959), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05129,40.74959,-95.8887,40.73629), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05129,40.74959,-95.8887,40.73629), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.83416,40.78302,-95.83424,40.78378), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.83424,40.78378,-95.83416,40.78302), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.84131,40.8456,-95.81071,40.88668), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.81071,40.88668,-95.81873,40.89795), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.81873,40.89795,-95.81071,40.88668), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.83777,40.92471,-95.81873,40.89795), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.82833,40.97238,-104.05325,41.00141), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05325,41.00141,-103.57452,41.00172), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.57452,41.00172,-103.38249,41.00193), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.57377,41.00172,-103.38249,41.00193), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.38249,41.00193,-103.57452,41.00172), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.55679,41.00222,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.62103,41.00222,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.65346,41.00223,-102.55679,41.00222), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.07654,41.00225,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05161,41.00238,-103.07654,41.00225), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.05172,41.00238,-103.07654,41.00225), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.86588,41.0174,-102.05161,41.00238), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.86478,41.05285,-95.86384,41.08351), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.86384,41.08351,-95.86478,41.05285), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05314,41.11446,-95.86869,41.1247), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05314,41.11446,-95.86869,41.1247), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.86869,41.1247,-104.05314,41.11446), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.8619,41.1603,-95.90969,41.1844), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.90969,41.1844,-95.85679,41.1871), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.85679,41.1871,-95.90969,41.1844), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.90991,41.19128,-95.85679,41.1871), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.91139,41.238,-104.05245,41.2782), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05245,41.2782,-95.89015,41.27831), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.89015,41.27831,-104.05245,41.2782), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.92569,41.3222,-95.89015,41.27831), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.92879,41.3701,-95.92734,41.38999), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.92734,41.38999,-104.05229,41.39321), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05229,41.39321,-104.05229,41.39331), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05229,41.39331,-104.05229,41.39321), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.92253,41.45577,-95.98296,41.46978), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.98296,41.46978,-95.92253,41.45577), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-95.99402,41.50689,-96.08049,41.5282), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.08049,41.5282,-96.00508,41.544), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.00508,41.544,-96.08049,41.5282), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.09182,41.56109,-104.05263,41.56428), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05263,41.56428,-96.09182,41.56109), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.11811,41.6135,-104.05274,41.61368), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05274,41.61368,-96.11811,41.6135), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.11148,41.66855,-96.10794,41.67651), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.10794,41.67651,-96.11148,41.66855), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05283,41.69795,-96.10794,41.67651), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.0876,41.72218,-104.05283,41.69795), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.06454,41.793,-96.10791,41.84034), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.10791,41.84034,-96.12682,41.8661), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.12682,41.8661,-104.05303,41.88546), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05303,41.88546,-96.12682,41.8661), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.1591,41.91006,-104.05303,41.88546), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.13254,41.97463,-104.05276,42.00172), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05276,42.00172,-104.05273,42.01632), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05273,42.01632,-96.22361,42.02265), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.22361,42.02265,-104.05273,42.01632), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.27288,42.04724,-96.22361,42.02265), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.2689,42.11359,-96.34775,42.16681), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.34775,42.16681,-96.33722,42.21485), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.33722,42.21485,-96.33632,42.21892), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.33632,42.21892,-96.33722,42.21485), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05279,42.24996,-96.336,42.26481), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.336,42.26481,-104.05279,42.24996), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.35196,42.28089,-96.336,42.26481), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.408,42.33741,-96.35196,42.28089), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.41181,42.41089,-96.38131,42.46169), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.38131,42.46169,-96.50132,42.48275), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.50132,42.48275,-96.44551,42.49063), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.44551,42.49063,-96.50132,42.48275), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05311,42.49996,-96.61149,42.50609), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.61149,42.50609,-96.52514,42.51023), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.52514,42.51023,-96.61149,42.50609), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.62795,42.5271,-96.52514,42.51023), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.65875,42.56643,-96.7093,42.60375), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.7093,42.60375,-104.05266,42.61177), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05266,42.61177,-96.7093,42.60375), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05259,42.63092,-104.05266,42.61177), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.69764,42.65914,-96.77818,42.66299), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.77818,42.66299,-96.69764,42.65914), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.80165,42.69877,-96.80737,42.70068), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.80737,42.70068,-96.80165,42.69877), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.96568,42.72453,-96.9068,42.7338), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-96.9068,42.7338,-96.96568,42.72453), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.01563,42.75653,-97.02485,42.76243), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.02485,42.76243,-98.03503,42.76421), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.03503,42.76421,-97.02485,42.76243), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.95015,42.76962,-97.13133,42.77193), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.13133,42.77193,-97.95015,42.76962), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.16507,42.79162,-97.905,42.79887), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.905,42.79887,-97.16507,42.79162), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.1047,42.80848,-97.905,42.79887), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.21396,42.82014,-98.1047,42.80848), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.14806,42.84001,-98.15259,42.84115), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.15259,42.84115,-98.14806,42.84001), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.70103,42.8438,-97.45218,42.84605), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.45218,42.84605,-97.70103,42.8438), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.48492,42.85,-97.63544,42.85181), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.63544,42.85181,-97.87689,42.85266), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.87689,42.85266,-97.23787,42.85314), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.23787,42.85314,-97.87689,42.85266), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.51595,42.85375,-97.23787,42.85314), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.34118,42.85588,-97.59926,42.85623), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.59926,42.85623,-97.34118,42.85588), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.80134,42.858,-97.59926,42.85623), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.23192,42.86114,-97.80134,42.858), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.85796,42.86509,-97.30208,42.86566), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.30208,42.86566,-97.41707,42.86592), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-97.41707,42.86592,-97.30208,42.86566), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.28001,42.875,-97.41707,42.86592), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.30819,42.88649,-98.28001,42.875), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.38645,42.91841,-98.4345,42.92923), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.4345,42.92923,-98.38645,42.91841), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.47892,42.96354,-101.00043,42.99753), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.00043,42.99753,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-101.22811,42.99787,-100.19841,42.99798), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.19841,42.99798,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-100.19841,42.99798,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.85004,42.99817,-99.53406,42.9982), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.53406,42.9982,-99.25446,42.99822), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-99.25446,42.99822,-99.53406,42.9982), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.84799,42.99826,-99.25446,42.99822), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-98.49855,42.99856,-98.84799,42.99826), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.08249,42.99914,-102.40864,42.99963), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.08255,42.99914,-102.40864,42.99963), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.40864,42.99963,-102.79211,43.00004), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-102.79211,43.00004,-103.0009,43.00026), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.0009,43.00026,-102.79211,43.00004), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-104.05313,43.00059,-103.5051,43.00076), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.5051,43.00076,-103.47613,43.00077), mapfile, tile_dir, 0, 11, "nebraska-ne")
	render_tiles((-103.47613,43.00077,-103.5051,43.00076), mapfile, tile_dir, 0, 11, "nebraska-ne")