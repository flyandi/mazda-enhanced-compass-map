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
    # Region: Kansas
    # Region Name: KS

	render_tiles((-102.04224,36.99308,-102.0282,36.99315), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.0282,36.99315,-102.04224,36.99308), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.90244,36.9937,-102.0282,36.99315), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.55526,36.99529,-101.48533,36.99561), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.48533,36.99561,-101.55526,36.99529), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.21149,36.99712,-101.06645,36.99774), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.06645,36.99774,-98.35407,36.99796), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.35407,36.99796,-98.34715,36.99797), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.34715,36.99797,-98.35407,36.99796), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.11199,36.99825,-98.04534,36.99833), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.94547,36.99825,-98.04534,36.99833), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.04534,36.99833,-98.11199,36.99825), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.54466,36.99852,-100.85563,36.99863), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.85563,36.99863,-96.50029,36.99864), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.50029,36.99864,-100.85563,36.99863), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.52558,36.99868,-97.80231,36.9987), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.80231,36.9987,-96.52558,36.99868), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.7687,36.99875,-94.71277,36.99879), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.71277,36.99879,-97.46235,36.99882), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.46235,36.99882,-97.38493,36.99884), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.38493,36.99884,-97.46235,36.99882), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61796,36.99891,-97.14772,36.99897), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.14772,36.99897,-96.74984,36.99899), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.74984,36.99899,-97.10065,36.999), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.10065,36.999,-96.74984,36.99899), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.21757,36.99907,-97.10065,36.999), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.00081,36.9992,-95.96427,36.99922), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.96427,36.99922,-96.00081,36.9992), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.92812,36.99925,-98.79194,36.99926), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.79194,36.99926,-95.92812,36.99925), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.78676,36.99927,-98.79194,36.99926), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.5736,36.99931,-95.52241,36.99932), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.52241,36.99932,-95.5736,36.99931), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.40762,36.99934,-95.52241,36.99932), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.32257,36.99936,-95.40762,36.99934), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.0003,36.99936,-95.40762,36.99934), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.12945,36.99942,-95.32257,36.99936), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.0735,36.99949,-95.00762,36.99952), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.00762,36.99952,-94.99529,36.99953), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.99529,36.99953,-95.00762,36.99952), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.40702,36.99958,-94.99529,36.99953), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.4562,36.9997,-99.40702,36.99958), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.54111,36.99991,-99.4562,36.9997), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.63332,37.00017,-99.65766,37.0002), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.65766,37.0002,-100.63332,37.00017), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.55268,37.00074,-99.65766,37.0002), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.08948,37.00148,-100.00257,37.00162), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.00257,37.00162,-99.9952,37.00163), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.9952,37.00163,-100.00257,37.00162), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04192,37.03508,-94.6181,37.0568), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.6181,37.0568,-102.04192,37.03508), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04198,37.10655,-94.6181,37.0568), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61835,37.16021,-102.04198,37.10655), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04196,37.25816,-94.61775,37.33842), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61775,37.33842,-94.61767,37.36417), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61767,37.36417,-102.04194,37.38919), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04194,37.38919,-94.61751,37.41091), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61751,37.41091,-102.04194,37.38919), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04189,37.64428,-94.61785,37.65358), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61785,37.65358,-102.04189,37.64428), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61787,37.67311,-94.61789,37.68221), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61789,37.68221,-94.61787,37.67311), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04188,37.72388,-102.04197,37.73854), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04197,37.73854,-102.04188,37.72388), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61447,37.9878,-94.6141,38.03706), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.6141,38.03706,-94.61393,38.06005), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61393,38.06005,-94.6141,38.03706), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04426,38.11301,-94.61393,38.06005), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61261,38.23777,-102.04463,38.26241), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04463,38.26241,-102.04465,38.26875), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04465,38.26875,-102.04463,38.26241), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04494,38.38442,-94.61277,38.38872), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61277,38.38872,-102.04494,38.38442), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61287,38.47757,-94.61287,38.4776), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61287,38.4776,-94.61287,38.47757), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.61196,38.54763,-102.04551,38.61516), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04551,38.61516,-94.61196,38.54763), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04571,38.69757,-94.60949,38.7381), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60949,38.7381,-94.60946,38.7407), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60946,38.7407,-94.60949,38.7381), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60896,38.84721,-94.60946,38.7407), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60833,38.98181,-94.60787,39.04409), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60787,39.04409,-102.04657,39.04704), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04657,39.04704,-94.60787,39.04409), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60735,39.11344,-102.0472,39.13315), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.0472,39.13315,-94.60735,39.11344), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.59193,39.155,-94.60194,39.1555), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.60194,39.1555,-94.59193,39.155), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.62393,39.1566,-94.60194,39.1555), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.74194,39.1702,-94.62393,39.1566), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.68034,39.1843,-94.74194,39.1702), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.79199,39.20126,-94.79966,39.20602), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.79966,39.20602,-94.79199,39.20126), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.82566,39.24173,-94.85707,39.27383), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.85707,39.27383,-94.82566,39.24173), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.90807,39.32366,-94.85707,39.27383), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04896,39.37371,-94.88897,39.39243), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.88897,39.39243,-94.94666,39.39972), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.94666,39.39972,-94.88897,39.39243), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.96575,39.42168,-94.98214,39.44055), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.98214,39.44055,-94.96575,39.42168), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.04985,39.49442,-95.09142,39.53326), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.09142,39.53326,-95.11356,39.55394), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.11356,39.55394,-102.04996,39.56818), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04996,39.56818,-102.04999,39.57406), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.04999,39.57406,-95.07669,39.57676), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.07669,39.57676,-102.04999,39.57406), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.04717,39.59512,-95.07669,39.57676), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.04405,39.61367,-95.04717,39.59512), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.03746,39.65291,-94.97132,39.68641), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.97132,39.68641,-95.03746,39.65291), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.97108,39.72315,-94.89932,39.72404), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.89932,39.72404,-94.97108,39.72315), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.86037,39.74953,-94.87114,39.77299), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.87114,39.77299,-94.86037,39.74953), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.05125,39.81899,-94.87782,39.82041), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.87782,39.82041,-102.05125,39.81899), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.87868,39.82652,-94.87782,39.82041), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.08153,39.86172,-94.92847,39.87634), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.92847,39.87634,-95.08153,39.86172), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.14245,39.89542,-95.01874,39.89737), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.01874,39.89737,-94.99337,39.89857), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.99337,39.89857,-95.01874,39.89737), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-94.95154,39.90053,-94.99337,39.89857), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.23111,39.94378,-94.95154,39.90053), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.30829,40,-95.3399,40.00003), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.3399,40.00003,-95.30829,40), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.78458,40.00046,-95.78811,40.00047), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-95.78811,40.00047,-95.78458,40.00046), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.15437,40.0005,-95.78811,40.00047), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.23917,40.00069,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.23921,40.00069,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.01068,40.0007,-96.23917,40.00069), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.02409,40.00072,-96.01068,40.0007), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.46371,40.00096,-96.46995,40.00097), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.46995,40.00097,-96.46371,40.00096), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.80577,40.00137,-99.8134,40.0014), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.8134,40.0014,-96.80577,40.00137), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.87381,40.00145,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-96.91641,40.00145,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.00917,40.00146,-96.87381,40.00145), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.1936,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.19359,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.1778,40.00157,-97.00917,40.00146), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.47702,40.00175,-99.62825,40.00177), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.62825,40.00177,-99.62533,40.00178), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.62533,40.00178,-99.62825,40.00177), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.3692,40.00194,-97.41583,40.002), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.41583,40.002,-99.50179,40.00203), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.50179,40.00203,-97.41583,40.002), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.17913,40.00211,-99.0856,40.00213), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.0856,40.00213,-99.06702,40.00214), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-99.06702,40.00214,-99.0856,40.00213), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.77716,40.00217,-97.8215,40.00219), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.8215,40.00219,-97.77716,40.00217), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-97.93182,40.00224,-100.73882,40.00226), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.73882,40.00226,-97.93182,40.00224), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.07603,40.0023,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-100.75883,40.0023,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.06032,40.00231,-98.07603,40.0023), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.27402,40.00234,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.72637,40.00234,-101.06032,40.00231), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.50445,40.00238,-98.61376,40.0024), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-98.61376,40.0024,-98.50445,40.00238), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.29399,40.00256,-101.32551,40.00257), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.32551,40.00257,-101.29399,40.00256), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.41103,40.00258,-101.32551,40.00257), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.54227,40.00261,-101.41103,40.00258), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-101.83216,40.00293,-102.05174,40.00308), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.05174,40.00308,-101.83216,40.00293), mapfile, tile_dir, 0, 11, "kansas-ks")
	render_tiles((-102.05174,40.00308,-101.83216,40.00293), mapfile, tile_dir, 0, 11, "kansas-ks")