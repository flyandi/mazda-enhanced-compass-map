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
    # Region: Iowa
    # Region Name: IA

	render_tiles((-91.41942,40.37826,-91.37292,40.39911), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.37292,40.39911,-91.49809,40.40193), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.49809,40.40193,-91.37292,40.39911), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.51913,40.43282,-91.37991,40.45211), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.37991,40.45211,-91.56384,40.46099), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.56384,40.46099,-91.37991,40.45211), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.60835,40.50004,-91.36788,40.51048), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.36788,40.51048,-91.60835,40.50004), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.39448,40.53454,-91.619,40.53908), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.619,40.53908,-91.39448,40.53454), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.67099,40.55094,-91.619,40.53908), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.53388,40.57074,-94.47121,40.57096), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.47121,40.57096,-94.53388,40.57074), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.31072,40.57152,-94.63203,40.57176), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.63203,40.57176,-94.31072,40.57152), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.23224,40.57201,-94.63203,40.57176), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.09109,40.5729,-94.81998,40.57371), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.81998,40.57371,-94.01549,40.57407), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.01549,40.57407,-94.81998,40.57371), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.9149,40.57492,-94.01549,40.57407), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.84093,40.57679,-95.06892,40.57688), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.06892,40.57688,-93.84093,40.57679), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.77434,40.57753,-95.06892,40.57688), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.20227,40.57838,-91.68538,40.57889), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.68538,40.57889,-95.20227,40.57838), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.59735,40.5795,-93.5569,40.57966), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.5569,40.57966,-93.59735,40.5795), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.33559,40.57987,-93.5569,40.57966), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.37393,40.58033,-93.37439,40.5804), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.37439,40.5804,-95.37393,40.58033), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.34544,40.58051,-93.37439,40.5804), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.53318,40.58225,-91.37425,40.58259), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.37425,40.58259,-93.1358,40.58285), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.1358,40.58285,-91.37425,40.58259), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.09729,40.58382,-93.1358,40.58285), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.76565,40.58521,-93.09729,40.58382), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.9416,40.58774,-92.7146,40.58958), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.7146,40.58958,-92.68669,40.58981), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.68669,40.58981,-92.7146,40.58958), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.6379,40.59096,-92.68669,40.58981), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.45375,40.59529,-92.3508,40.59726), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.3508,40.59726,-92.45375,40.59529), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.17978,40.60053,-95.74863,40.60336), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.74863,40.60336,-91.71665,40.60374), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.71665,40.60374,-95.74863,40.60336), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.94312,40.60606,-91.93929,40.60615), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.93929,40.60615,-91.94312,40.60606), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.33972,40.61349,-91.72912,40.61364), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.72912,40.61364,-91.33972,40.61349), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.18698,40.6373,-91.18546,40.63811), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.18546,40.63811,-91.24785,40.63839), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.24785,40.63839,-91.18546,40.63811), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.78191,40.65327,-91.24785,40.63839), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.12082,40.67278,-95.84603,40.68261), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.84603,40.68261,-91.12082,40.67278), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.11822,40.69953,-95.84603,40.68261), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.11574,40.72517,-95.8887,40.73629), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.8887,40.73629,-91.11574,40.72517), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.0917,40.77971,-95.83416,40.78302), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.83416,40.78302,-95.83424,40.78378), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.83424,40.78378,-95.83416,40.78302), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.09299,40.82108,-95.84131,40.8456), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.84131,40.8456,-91.04465,40.86836), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.04465,40.86836,-95.81071,40.88668), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.81071,40.88668,-95.81873,40.89795), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.81873,40.89795,-95.81071,40.88668), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.98546,40.91214,-95.83777,40.92471), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.83777,40.92471,-90.98546,40.91214), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.95223,40.95405,-95.82833,40.97238), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.82833,40.97238,-90.95223,40.95405), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.86588,41.0174,-90.94532,41.01928), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.94532,41.01928,-95.86588,41.0174), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.86478,41.05285,-90.95189,41.06987), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.95189,41.06987,-90.95227,41.07273), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.95227,41.07273,-90.95189,41.06987), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.86384,41.08351,-90.95227,41.07273), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.95725,41.11109,-95.86869,41.1247), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.86869,41.1247,-90.95725,41.11109), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.8619,41.1603,-90.99791,41.16256), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.99791,41.16256,-95.8619,41.1603), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.04154,41.16614,-90.99791,41.16256), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.90969,41.1844,-95.85679,41.1871), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.85679,41.1871,-95.90969,41.1844), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.90991,41.19128,-95.85679,41.1871), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.08145,41.21443,-95.90991,41.19128), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.91139,41.238,-91.11419,41.25003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.11419,41.25003,-95.91139,41.238), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.89015,41.27831,-91.11419,41.25003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.92569,41.3222,-91.07442,41.33363), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.07442,41.33363,-91.07409,41.33432), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.07409,41.33432,-91.07442,41.33363), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.07155,41.33965,-91.07409,41.33432), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.06506,41.3691,-95.92879,41.3701), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.92879,41.3701,-91.06506,41.3691), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.92734,41.38999,-95.92879,41.3701), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.92434,41.42286,-91.02779,41.4236), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.02779,41.4236,-90.92434,41.42286), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.96666,41.43005,-91.02779,41.4236), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.86728,41.44822,-90.78628,41.45289), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.78628,41.45289,-90.70116,41.45474), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.70116,41.45474,-95.92253,41.45577), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.92253,41.45577,-90.70116,41.45474), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.98296,41.46978,-95.92253,41.45577), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.61854,41.48503,-95.98296,41.46978), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.99402,41.50689,-90.57114,41.51633), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.57114,41.51633,-90.51313,41.51953), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.51313,41.51953,-90.57114,41.51633), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.46143,41.52353,-90.51313,41.51953), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.08049,41.5282,-90.46143,41.52353), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.00508,41.544,-96.08049,41.5282), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.09182,41.56109,-90.41583,41.56293), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.41583,41.56293,-96.09182,41.56109), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.36413,41.57963,-90.41583,41.56293), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.33953,41.59863,-96.11811,41.6135), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.11811,41.6135,-90.33953,41.59863), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.33673,41.66453,-96.11148,41.66855), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.11148,41.66855,-90.33673,41.66453), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.10794,41.67651,-96.11148,41.66855), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.31469,41.69483,-96.10794,41.67651), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.0876,41.72218,-90.31186,41.72853), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.31186,41.72853,-96.0876,41.72218), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.31071,41.74221,-90.31186,41.72853), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.24863,41.77981,-90.24237,41.78277), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.24237,41.78277,-90.24863,41.77981), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.06454,41.793,-90.24237,41.78277), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.18064,41.81198,-96.06454,41.793), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.10791,41.84034,-90.1814,41.84465), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.1814,41.84465,-96.10791,41.84034), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.12682,41.8661,-90.16507,41.88378), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.16507,41.88378,-96.12682,41.8661), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.1591,41.91006,-90.15815,41.92984), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.15815,41.92984,-90.1569,41.93818), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.1569,41.93818,-90.15815,41.92984), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.13254,41.97463,-90.14061,41.996), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.14061,41.996,-96.13254,41.97463), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.22361,42.02265,-90.15968,42.03309), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.15968,42.03309,-90.16345,42.04041), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.16345,42.04041,-96.27288,42.04724), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.27288,42.04724,-90.16345,42.04041), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.16116,42.10637,-96.2689,42.11359), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.2689,42.11359,-90.16116,42.10637), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.20742,42.14911,-96.34775,42.16681), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.34775,42.16681,-90.26908,42.1745), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.26908,42.1745,-96.34775,42.16681), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.3157,42.19395,-90.33817,42.20332), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.33817,42.20332,-90.3157,42.19395), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.33722,42.21485,-96.33632,42.21892), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.33632,42.21892,-96.33722,42.21485), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.40065,42.23929,-96.33632,42.21892), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.336,42.26481,-90.43088,42.27823), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.43088,42.27823,-96.35196,42.28089), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.35196,42.28089,-90.43088,42.27823), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.41713,42.31994,-96.408,42.33741), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.408,42.33741,-90.41713,42.31994), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.44632,42.35704,-96.408,42.33741), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.48435,42.3816,-90.51752,42.40302), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.51752,42.40302,-96.41181,42.41089), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.41181,42.41089,-90.51752,42.40302), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.56525,42.43874,-90.59042,42.44749), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.59042,42.44749,-90.56525,42.43874), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.38131,42.46169,-90.64673,42.4719), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.64673,42.4719,-96.38131,42.46169), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.44551,42.49063,-90.64284,42.50848), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.64284,42.50848,-96.47745,42.50959), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.47745,42.50959,-90.64284,42.50848), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.49297,42.51728,-96.47745,42.50959), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.47695,42.55608,-96.48002,42.56133), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.48002,42.56133,-96.47695,42.55608), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.67273,42.5766,-96.48002,42.56133), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.70086,42.62645,-96.52677,42.64118), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.52677,42.64118,-90.74368,42.64556), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.74368,42.64556,-96.52677,42.64118), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.8525,42.66482,-90.89696,42.67432), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.89696,42.67432,-90.8525,42.66482), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-90.94157,42.68384,-96.5916,42.68808), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.5916,42.68808,-90.94157,42.68384), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.01724,42.71957,-96.6247,42.7255), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.6247,42.7255,-91.01724,42.71957), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.07072,42.7755,-96.62188,42.77926), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.62188,42.77926,-91.07072,42.7755), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.62188,42.77926,-91.07072,42.7755), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.57794,42.82765,-91.09882,42.86442), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.09882,42.86442,-96.53785,42.87848), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.53785,42.87848,-91.09882,42.86442), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.138,42.90377,-96.54047,42.9086), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.54047,42.9086,-91.138,42.90377), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.54169,42.92258,-96.54047,42.9086), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.50031,42.95939,-91.15552,42.97577), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.15552,42.97577,-96.52025,42.97764), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.52025,42.97764,-91.15552,42.97577), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.15908,42.98748,-96.52025,42.97764), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.49269,43.00509,-91.15908,42.98748), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.17469,43.03871,-96.51161,43.03993), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.51161,43.03993,-91.17469,43.03871), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.4582,43.06755,-91.17493,43.08026), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.17493,43.08026,-96.4521,43.08255), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.4521,43.08255,-91.17493,43.08026), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.43934,43.11392,-91.17525,43.13467), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.17525,43.13467,-96.45885,43.14336), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.45885,43.14336,-91.17525,43.13467), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.13417,43.17441,-96.45885,43.14336), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.52208,43.22096,-96.47557,43.22105), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.47557,43.22105,-96.52208,43.22096), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.08746,43.22189,-96.47557,43.22105), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.55296,43.24728,-91.05791,43.25397), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.05791,43.25397,-96.55903,43.25756), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.55903,43.25756,-91.05791,43.25397), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.57882,43.2911,-96.53039,43.30003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.53039,43.30003,-96.57882,43.2911), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.10724,43.31365,-96.53039,43.30003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.15481,43.33483,-96.52429,43.34721), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.52429,43.34721,-91.15481,43.33483), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.20737,43.37366,-96.52157,43.38564), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.52157,43.38564,-91.20737,43.37366), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.19941,43.40303,-91.21066,43.41944), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.21066,43.41944,-96.59425,43.43415), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.59425,43.43415,-91.21066,43.41944), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.23228,43.45095,-96.59425,43.43415), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.5846,43.46961,-91.23228,43.45095), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.57673,43.49952,-93.49735,43.49953), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.49735,43.49953,-93.57673,43.49952), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.64853,43.49954,-92.87028,43.49955), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.87028,43.49955,-93.64853,43.49954), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.02435,43.49956,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.04919,43.49956,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.22886,43.49957,-93.02435,43.49956), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.97076,43.49961,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-93.97076,43.49961,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.83442,43.49997,-95.86095,43.49999), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.86095,43.49999,-95.83442,43.49997), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.24797,43.50018,-96.05316,43.50019), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.05316,43.50019,-94.24797,43.50018), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.4868,43.50025,-92.55316,43.5003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.55316,43.5003,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.55313,43.5003,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.45443,43.50032,-92.55316,43.5003), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.19848,43.50034,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.45326,43.50039,-92.44895,43.50041), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.44895,43.50041,-96.45326,43.50039), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-96.59893,43.50046,-94.3906,43.50047), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.3906,43.50047,-96.59893,43.50046), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.38779,43.50048,-94.3906,43.50047), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.44285,43.50048,-94.3906,43.50047), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.21771,43.50055,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.85456,43.50055,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.87424,43.50056,-91.21771,43.50055), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-94.91461,43.5006,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.82485,43.50068,-91.49104,43.50069), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.49104,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.73022,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-91.61084,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.0798,43.5007,-92.17886,43.50071), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-92.17886,43.50071,-92.0798,43.5007), mapfile, tile_dir, 0, 11, "iowa-ia")
	render_tiles((-95.21494,43.50089,-92.17886,43.50071), mapfile, tile_dir, 0, 11, "iowa-ia")