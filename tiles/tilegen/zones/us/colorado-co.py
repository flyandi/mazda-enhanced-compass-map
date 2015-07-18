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
    # Region: Colorado
    # Region Name: CO

	render_tiles((-106.8698,36.99243,-102.04224,36.99308), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04224,36.99308,-104.73203,36.99345), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.73203,36.99345,-104.33883,36.99354), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.33883,36.99354,-104.73203,36.99345), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.47624,36.99377,-104.33883,36.99354), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.34314,36.99423,-102.35529,36.99451), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.35529,36.99451,-106.34314,36.99423), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.69814,36.99515,-106.00663,36.99539), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.00663,36.99539,-105.99747,36.99542), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.99747,36.99542,-105.1208,36.99543), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.1208,36.99543,-105.99747,36.99542), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.15504,36.99547,-105.1208,36.99543), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.22061,36.99556,-105.2513,36.99561), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.2513,36.99561,-105.22061,36.99556), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.71847,36.99585,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.71647,36.99585,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.41931,36.99586,-105.71847,36.99585), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.53392,36.99588,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.00785,36.99598,-105.53392,36.99588), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.73325,36.99802,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04522,36.99908,-108.62031,36.99929), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-108.62031,36.99929,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-108.37926,36.99956,-102.84199,36.9996), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.84199,36.9996,-108.37926,36.99956), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.0861,36.99986,-107.42092,37), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.42092,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.48174,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-108.00062,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.42091,37.00001,-107.42092,37), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.0022,37.0001,-106.87729,37.00014), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.87729,37.00014,-103.0022,37.0001), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04192,37.03508,-106.87729,37.00014), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04198,37.10655,-102.04192,37.03508), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04196,37.25816,-102.04194,37.38919), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04194,37.38919,-109.04378,37.48468), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04378,37.48468,-102.04194,37.38919), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04189,37.64428,-102.04188,37.72388), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04188,37.72388,-102.04197,37.73854), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04197,37.73854,-102.04188,37.72388), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.0426,37.88117,-102.04197,37.73854), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04426,38.11301,-109.0418,38.15302), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.0418,38.15302,-109.04176,38.16469), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04176,38.16469,-109.0418,38.15302), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04463,38.26241,-102.04465,38.26875), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04465,38.26875,-102.04463,38.26241), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.06006,38.27549,-102.04465,38.26875), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04494,38.38442,-109.06006,38.27549), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05996,38.49999,-102.04551,38.61516), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05996,38.49999,-102.04551,38.61516), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05996,38.49999,-102.04551,38.61516), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04551,38.61516,-102.04571,38.69757), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04571,38.69757,-102.04551,38.61516), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04657,39.04704,-109.05151,39.1261), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05151,39.1261,-102.0472,39.13315), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.0472,39.13315,-109.05151,39.1261), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05122,39.36668,-102.04896,39.37371), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04896,39.37371,-109.05122,39.36668), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05107,39.49774,-102.04996,39.56818), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04996,39.56818,-102.04999,39.57406), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.04999,39.57406,-102.04996,39.56818), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05087,39.66047,-102.04999,39.57406), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05125,39.81899,-109.05062,39.87497), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05062,39.87497,-102.05125,39.81899), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05174,40.00308,-109.05062,39.87497), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05097,40.18085,-109.05073,40.22266), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05073,40.22266,-109.05097,40.18085), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05131,40.33838,-102.05131,40.34922), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05131,40.34922,-102.05131,40.33838), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.0513,40.44001,-102.05131,40.34922), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04825,40.6536,-109.04826,40.6626), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04826,40.6626,-109.04825,40.6536), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05129,40.69755,-109.04826,40.6626), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05129,40.74959,-102.05129,40.69755), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05129,40.74959,-102.05129,40.69755), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.04846,40.82608,-102.05129,40.74959), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.21757,40.99773,-106.19055,40.99775), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.19055,40.99775,-106.21757,40.99773), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.85527,40.99805,-104.94337,40.99807), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.94337,40.99807,-104.85527,40.99805), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.27686,40.99817,-106.32117,40.99822), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-105.27714,40.99817,-106.32117,40.99822), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.32117,40.99822,-105.27686,40.99817), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-108.25065,41.00011,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-109.05008,41.00066,-106.86038,41.00072), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-106.86038,41.00072,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.91842,41.00123,-104.05325,41.00141), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.05325,41.00141,-107.91842,41.00123), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.57377,41.00172,-104.49706,41.00181), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.57452,41.00172,-104.49706,41.00181), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-104.49706,41.00181,-103.57377,41.00172), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.38249,41.00193,-104.49706,41.00181), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.62103,41.00222,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.55679,41.00222,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.65346,41.00223,-102.62103,41.00222), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-103.07654,41.00225,-102.65346,41.00223), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05172,41.00238,-103.07654,41.00225), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-102.05161,41.00238,-103.07654,41.00225), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.3178,41.00284,-107.36744,41.00307), mapfile, tile_dir, 0, 11, "colorado-co")
	render_tiles((-107.36744,41.00307,-107.3178,41.00284), mapfile, tile_dir, 0, 11, "colorado-co")