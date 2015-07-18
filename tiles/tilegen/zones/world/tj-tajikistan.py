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
    # Region: TJ
    # Region Name: Tajikistan

	render_tiles((71.69524,36.67221,71.84274,36.69248), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.84274,36.69248,71.69524,36.67221), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.56831,36.74137,71.84274,36.69248), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.01137,36.8122,71.56831,36.74137), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.03552,36.92471,72.34663,36.98998), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.34663,36.98998,67.91052,37.01443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.91052,37.01443,68.28331,37.01998), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.28331,37.01998,67.91052,37.01443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.66359,37.02609,68.28331,37.01998), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.43219,37.05859,67.88553,37.06137), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.88553,37.06137,71.43219,37.05859), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.27831,37.08693,67.79164,37.08832), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.79164,37.08832,68.27831,37.08693), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.24663,37.09415,67.79164,37.08832), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.41302,37.10443,68.30164,37.1111), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.30164,37.1111,68.41302,37.10443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.33304,37.12498,68.30164,37.1111), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.41219,37.14804,68.52498,37.16193), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.52498,37.16193,68.41219,37.14804), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.77715,37.1858,71.44691,37.20776), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.44691,37.20776,73.79247,37.22942), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.79247,37.22942,69.45137,37.22998), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.45137,37.22998,73.79247,37.22942), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.79886,37.22998,73.79247,37.22942), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.63441,37.23998,74.89883,37.2403), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.89883,37.2403,73.63441,37.23998), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.65469,37.24693,68.82581,37.24776), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.82581,37.24776,68.65469,37.24693), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8381,37.2619,73.61996,37.26276), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.61996,37.26276,67.8381,37.2619), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.90497,37.27221,73.61996,37.26276), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.49025,37.28416,68.92441,37.28471), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.92441,37.28471,71.49025,37.28416), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.64941,37.30443,68.99969,37.30776), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.99969,37.30776,73.64941,37.30443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.89165,37.31749,73.08136,37.32054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.08136,37.32054,75.09663,37.32193), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((75.09663,37.32193,68.8094,37.32249), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8094,37.32249,75.09663,37.32193), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.85025,37.32471,68.8094,37.32249), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8439,37.3306,73.75276,37.33138), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.75276,37.33138,67.8439,37.3306), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.38135,37.34415,73.75276,37.33138), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((75.11108,37.3836,74.69969,37.39193), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.69969,37.39193,74.4008,37.39943), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.4008,37.39943,73.15053,37.40082), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.15053,37.40082,74.4008,37.39943), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((75.18747,37.40665,73.15053,37.40082), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.36386,37.4286,73.77664,37.43443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.77664,37.43443,74.36386,37.4286), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8175,37.4461,69.38274,37.45582), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.38274,37.45582,73.31441,37.46332), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.31441,37.46332,69.38274,37.45582), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.13498,37.52915,67.8564,37.5353), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8564,37.5353,70.13498,37.52915), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.9494,37.56304,69.95859,37.56499), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.95859,37.56499,74.9494,37.56304), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.51581,37.58082,69.95859,37.56499), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.91803,37.61193,67.9172,37.6172), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.9172,37.6172,70.25554,37.62109), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.25554,37.62109,67.9172,37.6172), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.90276,37.64721,70.25554,37.62109), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.27692,37.74137,71.52885,37.76443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.52885,37.76443,68.0731,37.7653), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0731,37.7653,71.52885,37.76443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.99025,37.77859,68.0731,37.7653), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.58858,37.81609,74.90276,37.84721), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.90276,37.84721,70.16553,37.87221), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.16553,37.87221,74.90276,37.84721), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.59192,37.90304,71.37468,37.90582), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.37468,37.90582,71.59192,37.90304), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.26414,37.91805,70.20386,37.92054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.20386,37.92054,71.26414,37.91805), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1506,37.9281,70.20386,37.92054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.92052,37.93887,70.17024,37.94165), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.17024,37.94165,71.54636,37.94248), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.54636,37.94248,70.17024,37.94165), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.2739,37.9544,71.54636,37.94248), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.91359,38.01776,68.2964,38.0181), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.2964,38.0181,74.91359,38.01776), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.2847,38.01998,68.2964,38.0181), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3783,38.05776,74.82053,38.0686), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.82053,38.0686,70.3783,38.05776), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.3686,38.1122,74.82053,38.0686), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.49191,38.16721,71.36386,38.18803), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.36386,38.18803,68.3842,38.1956), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.3842,38.1956,71.36386,38.18803), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.36302,38.24887,68.3314,38.2733), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.3314,38.2733,70.58441,38.27859), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.58441,38.27859,68.3314,38.2733), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.79442,38.30609,68.2156,38.3322), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.2156,38.3322,70.59636,38.33415), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.59636,38.33415,68.2156,38.3322), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.68552,38.37526,74.86275,38.38416), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.86275,38.38416,70.68552,38.37526), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1694,38.395,74.86275,38.38416), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.10191,38.40609,70.6722,38.41443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6722,38.41443,71.10191,38.40609), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1272,38.4244,70.6722,38.41443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.88052,38.45304,74.85663,38.47054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.85663,38.47054,70.88052,38.45304), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1111,38.4983,74.85663,38.47054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.97414,38.5336,68.0719,38.5414), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0719,38.5414,74.08304,38.54749), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.08304,38.54749,68.0719,38.5414), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.8472,38.58804,74.55302,38.60054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.55302,38.60054,68.0806,38.6092), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0806,38.6092,74.07442,38.60999), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.07442,38.60999,68.0806,38.6092), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.79521,38.66122,74.35469,38.67443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((74.35469,38.67443,73.79521,38.66122), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0578,38.6969,74.35469,38.67443), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1008,38.7392,68.0578,38.6969), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0831,38.7944,68.155,38.8086), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.155,38.8086,68.0831,38.7944), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1964,38.8544,73.70525,38.86415), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.70525,38.86415,68.1964,38.8544), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1839,38.9008,73.7233,38.91248), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.7233,38.91248,68.1839,38.9008), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.84413,38.94526,73.85136,38.97609), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.85136,38.97609,67.8633,38.9767), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8633,38.9767,73.85136,38.97609), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0369,38.9842,67.8633,38.9767), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.7094,38.9967,68.1133,38.9975), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.1133,38.9975,67.7094,38.9967), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.9689,39.0089,68.1133,38.9975), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.6872,39.0494,67.9689,39.0089), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.6947,39.1347,67.6539,39.14), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.6539,39.14,67.6947,39.1347), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.5178,39.1678,67.6175,39.1719), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.6175,39.1719,67.5178,39.1678), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.2486,39.1919,67.3819,39.2097), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.3819,39.2097,73.62386,39.22054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.62386,39.22054,67.3675,39.2253), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.3675,39.2253,73.62386,39.22054), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.2297,39.2453,72.3064,39.2572), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.3064,39.2572,72.2297,39.2453), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.7731,39.2781,71.8572,39.2867), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.8572,39.2867,72.1092,39.2881), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.1092,39.2881,71.8572,39.2867), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.3667,39.2942,67.4106,39.2992), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.4106,39.2992,67.3667,39.2942), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.3858,39.3358,72.3422,39.3364), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.3422,39.3364,72.3858,39.3358), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.7314,39.3375,72.3422,39.3364), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.1017,39.3414,71.7314,39.3375), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.4975,39.3517,71.9792,39.3519), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.0283,39.3517,71.9792,39.3519), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.9792,39.3519,72.4975,39.3517), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.1539,39.3533,71.9792,39.3519), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.4158,39.3586,72.8583,39.3625), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.8583,39.3625,72.5931,39.3639), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.5931,39.3639,72.8583,39.3625), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.9489,39.3664,72.5931,39.3639), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.0767,39.3747,72.5386,39.3819), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.5386,39.3819,73.0986,39.3825), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.0986,39.3825,72.5386,39.3819), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8011,39.3889,71.7744,39.3911), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.7744,39.3911,70.8011,39.3889), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.3439,39.3947,71.7744,39.3911), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7478,39.3986,70.9978,39.4011), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9978,39.4011,73.3603,39.4014), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.3603,39.4014,70.9978,39.4011), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8897,39.4019,73.3603,39.4014), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((72.6642,39.4019,73.3603,39.4014), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9525,39.4103,70.8486,39.4111), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8486,39.4111,70.9525,39.4103), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.0328,39.4119,70.8486,39.4111), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7206,39.4192,71.0328,39.4119), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9219,39.4353,73.65469,39.43803), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.65469,39.43803,70.9219,39.4353), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.3697,39.4419,73.65469,39.43803), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.605,39.4486,73.3697,39.4419), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.67163,39.45888,73.67244,39.45895), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.67244,39.45895,73.67163,39.45888), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.5425,39.4611,71.7567,39.4622), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.7567,39.4622,71.5425,39.4611), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((73.4914,39.47,70.7244,39.4767), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7244,39.4767,73.4914,39.47), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.4425,39.4856,70.7244,39.4767), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.5136,39.4956,71.515,39.4989), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.515,39.4989,67.5136,39.4956), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6739,39.5075,71.0972,39.5106), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.0972,39.5106,70.6739,39.5075), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.2753,39.5169,71.0972,39.5106), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3406,39.5169,71.0972,39.5106), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.2347,39.5261,70.2692,39.5292), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.2692,39.5292,69.4458,39.5308), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.4458,39.5308,70.2692,39.5292), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.203,39.5336,70.0025,39.5339), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.0025,39.5339,71.203,39.5336), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3625,39.5344,70.0025,39.5339), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.3533,39.5367,68.4733,39.5375), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.4733,39.5375,68.3533,39.5367), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.8428,39.5375,68.3533,39.5367), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3061,39.5394,68.4733,39.5375), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5436,39.5461,69.3061,39.5394), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.1631,39.5539,68.5403,39.5547), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.5403,39.5547,70.1631,39.5539), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.5097,39.5547,70.1631,39.5539), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.9431,39.5586,69.3844,39.5592), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3844,39.5592,69.9431,39.5586), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.3161,39.5647,67.6047,39.5664), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.6047,39.5664,68.0825,39.5672), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.0825,39.5672,67.6047,39.5664), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.5531,39.5672,67.6047,39.5664), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.7728,39.5764,70.4072,39.5775), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4072,39.5775,69.7728,39.5764), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5897,39.5789,70.0525,39.58), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.0525,39.58,70.6339,39.5808), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6339,39.5808,70.0525,39.58), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5489,39.5822,70.6339,39.5808), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3561,39.5822,70.6339,39.5808), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.5367,39.5872,69.7081,39.5878), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.7081,39.5878,68.5367,39.5872), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4172,39.5919,69.7081,39.5878), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.4444,39.6025,71.4064,39.6064), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.4064,39.6064,70.4919,39.6083), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4919,39.6083,71.4064,39.6064), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.5925,39.6136,70.4919,39.6083), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((71.4797,39.6203,67.8275,39.6214), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.8275,39.6214,71.4797,39.6203), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.2275,39.6247,67.7097,39.6258), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((67.7097,39.6258,70.2275,39.6247), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.63,39.6561,69.3114,39.6817), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3114,39.6817,68.63,39.6561), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2492,39.7544,69.3114,39.6817), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7608,39.8275,68.7794,39.8389), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7794,39.8389,68.6739,39.85), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.6739,39.85,68.6397,39.8558), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.6397,39.8558,69.265,39.8603), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.265,39.8603,68.7744,39.8619), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7744,39.8619,69.265,39.8603), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7222,39.8714,68.8533,39.8742), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8533,39.8742,68.7222,39.8714), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8125,39.8783,68.8533,39.8742), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.9092,39.8914,68.8156,39.9003), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8156,39.9003,69.425,39.9019), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.425,39.9019,68.8156,39.9003), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4981,39.9069,69.425,39.9019), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5261,39.9319,70.4672,39.9364), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4672,39.9364,69.5261,39.9319), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5283,39.9503,69.3981,39.9519), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3981,39.9519,70.5283,39.9503), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6031,39.9583,69.3981,39.9519), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5003,39.9686,68.8058,39.9717), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8058,39.9717,70.4925,39.9736), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4925,39.9736,68.8058,39.9717), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7753,39.9869,70.6436,39.9894), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6436,39.9894,68.7753,39.9869), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8153,39.9939,69.3383,39.9964), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3383,39.9964,68.8153,39.9939), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5522,40.0094,69.3383,39.9964), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5603,40.0264,69.4839,40.0361), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.4839,40.0361,70.6617,40.0369), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6617,40.0369,69.4839,40.0361), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8386,40.0408,68.8083,40.0433), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8083,40.0433,68.8386,40.0408), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5422,40.0461,68.8083,40.0433), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.9975,40.07,68.7853,40.0706), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7853,40.0706,68.9975,40.07), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.9006,40.0775,70.3486,40.0831), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3486,40.0831,68.9006,40.0775), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8086,40.0831,68.9006,40.0775), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.0094,40.1011,70.6611,40.1036), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6611,40.1036,69.0094,40.1011), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5722,40.1072,70.6611,40.1036), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.6058,40.1119,69.5722,40.1072), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7058,40.1169,69.6058,40.1119), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5408,40.1314,70.2861,40.1328), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.2861,40.1328,69.5408,40.1314), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7756,40.135,70.2861,40.1328), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8636,40.1417,70.1728,40.1419), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.1728,40.1419,68.8636,40.1417), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.9872,40.1492,70.1728,40.1419), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8336,40.1597,68.6014,40.1628), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.6014,40.1628,70.8336,40.1597), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9181,40.1689,70.8197,40.1714), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8197,40.1714,70.9181,40.1689), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.6008,40.1783,70.8614,40.1797), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8614,40.1797,68.6008,40.1783), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9744,40.1811,70.8614,40.1797), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.64444,40.18328,70.9744,40.1811), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2353,40.1897,68.8358,40.1947), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8358,40.1947,69.2353,40.1897), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7161,40.2039,68.7683,40.2081), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.7683,40.2081,70.6258,40.2103), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6258,40.2103,69.3214,40.2119), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3214,40.2119,70.6258,40.2103), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.9964,40.2153,70.0242,40.2164), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.0242,40.2164,70.9964,40.2153), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((68.8761,40.2186,70.0242,40.2164), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.0978,40.2244,68.8761,40.2186), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.0322,40.2386,70.8706,40.2428), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.8706,40.2428,70.98216,40.24466), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.98216,40.24466,70.8706,40.2428), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.98216,40.24466,70.8706,40.2428), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5894,40.2553,70.98216,40.24466), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2317,40.2817,69.3075,40.2864), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3075,40.2864,69.2317,40.2817), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.24,40.3117,69.3392,40.3278), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3392,40.3278,70.5581,40.3425), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5581,40.3425,70.4567,40.3511), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4567,40.3511,70.5581,40.3425), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5158,40.3603,70.4567,40.3511), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.375,40.3767,70.5158,40.3603), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3783,40.4022,70.375,40.3767), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3464,40.4297,70.3783,40.4022), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3508,40.4617,70.3464,40.4297), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2597,40.5028,70.485,40.5067), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.485,40.5067,69.2597,40.5028), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.515,40.5497,69.2106,40.5589), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2106,40.5589,70.515,40.5497), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5797,40.5747,69.2514,40.5897), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.2514,40.5897,69.3322,40.6022), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3322,40.6022,69.2514,40.5897), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.7325,40.6386,69.6803,40.6467), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.6803,40.6467,69.7325,40.6386), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.8231,40.7156,70.79546,40.72551), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.79546,40.72551,69.8231,40.7156), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.335,40.7381,70.79546,40.72551), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7767,40.7556,69.3517,40.7689), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.3517,40.7689,70.0503,40.77), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.0503,40.77,69.3517,40.7689), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6922,40.7722,70.0503,40.77), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.5344,40.7822,69.4892,40.7856), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.4892,40.7856,70.6494,40.7861), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6494,40.7861,69.4892,40.7856), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7011,40.7981,69.4003,40.8003), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.4003,40.8003,70.7289,40.8006), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7289,40.8006,69.4003,40.8003), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((69.4592,40.8111,70.7281,40.8139), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.7281,40.8139,69.4592,40.8111), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6458,40.8225,70.1222,40.8267), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.1222,40.8267,70.6672,40.8275), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.6672,40.8275,70.1222,40.8267), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.2481,40.8558,70.6672,40.8275), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3167,40.8942,70.2481,40.8558), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3747,40.9583,70.5442,40.9811), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.5442,40.9811,70.3747,40.9583), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.3817,41.0278,70.4719,41.0372), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4719,41.0372,70.3817,41.0278), mapfile, tile_dir, 0, 11, "tj-tajikistan")
	render_tiles((70.4305,41.0503,70.4719,41.0372), mapfile, tile_dir, 0, 11, "tj-tajikistan")