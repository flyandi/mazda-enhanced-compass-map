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
    # Region: Florida
    # Region Name: FL

	render_tiles((-81.81254,24.54547,-81.68524,24.55868), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.68524,24.55868,-81.81169,24.56875), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.81169,24.56875,-81.68524,24.55868), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.81169,24.56875,-81.68524,24.55868), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.59533,24.59311,-81.81169,24.56875), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.5174,24.62124,-81.40189,24.62354), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.40189,24.62354,-81.5174,24.62124), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.34219,24.63777,-81.44392,24.64268), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.44392,24.64268,-81.34219,24.63777), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.75127,24.65352,-81.44392,24.64268), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.24323,24.674,-81.75127,24.65352), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.67234,24.69951,-81.24323,24.674), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.5846,24.7367,-81.30505,24.75519), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.30505,24.75519,-81.57115,24.75635), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.57115,24.75635,-81.30505,24.75519), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.44351,24.81336,-81.57115,24.75635), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.91886,24.49813,-82.02809,24.49872), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.02809,24.49872,-81.91886,24.49813), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.01491,24.54307,-81.98391,24.58068), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.01491,24.54307,-81.98391,24.58068), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.98391,24.58068,-81.86871,24.58412), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.86871,24.58412,-81.98391,24.58068), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.17945,24.52947,-82.10076,24.53329), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.10076,24.53329,-82.17945,24.52947), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.18803,24.5747,-82.08664,24.59007), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.18803,24.5747,-82.08664,24.59007), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.08664,24.59007,-82.18803,24.5747), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.1441,24.62248,-82.08664,24.59007), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.10337,24.66946,-80.96625,24.70785), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.96625,24.70785,-81.14872,24.71048), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.14872,24.71048,-80.96625,24.70785), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.03891,24.7726,-81.14872,24.71048), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.84747,24.85175,-80.65119,24.86613), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.65119,24.86613,-80.84747,24.85175), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.49676,24.99932,-80.61087,25.007), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.61087,25.007,-80.49676,24.99932), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.51657,25.09546,-81.07986,25.1188), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.07986,25.1188,-81.0096,25.1254), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.0096,25.1254,-81.07986,25.1188), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.91592,25.1413,-80.74775,25.14744), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.74775,25.14744,-80.71061,25.15253), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.71061,25.15253,-80.35818,25.15323), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.35818,25.15323,-80.71061,25.15253), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.50051,25.15667,-80.35818,25.15323), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.87546,25.17432,-80.85817,25.17752), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.85817,25.17752,-80.87546,25.17432), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.14228,25.183,-80.81213,25.18604), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.81213,25.18604,-81.14228,25.183), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.65053,25.1891,-80.81213,25.18604), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.49539,25.19981,-80.54239,25.20638), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.54239,25.20638,-80.49539,25.19981), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.17091,25.24586,-80.54239,25.20638), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.23856,25.32682,-81.1481,25.33279), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.1481,25.33279,-80.23239,25.33707), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.23239,25.33707,-81.1481,25.33279), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.31036,25.38971,-81.14677,25.40758), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.14677,25.40758,-80.23485,25.42196), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.23485,25.42196,-81.14677,25.40758), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.16316,25.45218,-80.33705,25.46562), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.33705,25.46562,-80.16316,25.45218), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.2082,25.50494,-80.17602,25.52115), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.17602,25.52115,-81.2082,25.50494), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.31392,25.53916,-80.17602,25.52115), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.24052,25.59904,-80.30146,25.6133), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.30146,25.6133,-81.24052,25.59904), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.26588,25.65837,-80.15497,25.66549), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.15497,25.66549,-80.26588,25.65837), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.2899,25.67355,-80.15497,25.66549), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.17692,25.68506,-81.2899,25.67355), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.35599,25.70353,-80.17692,25.68506), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.22911,25.73251,-80.12913,25.74616), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.12913,25.74616,-80.12569,25.75687), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.12569,25.75687,-80.12381,25.76277), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.12381,25.76277,-80.12569,25.75687), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.38381,25.77675,-80.12381,25.76277), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.44186,25.80313,-81.47224,25.81693), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.47224,25.81693,-80.10995,25.81826), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.10995,25.81826,-81.47224,25.81693), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.67263,25.85665,-81.64024,25.87754), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.64024,25.87754,-81.61474,25.89398), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.61474,25.89398,-81.72709,25.90721), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.72709,25.90721,-80.1179,25.91577), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.1179,25.91577,-81.72709,25.90721), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.1152,25.97133,-80.11502,25.9751), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.11502,25.9751,-80.1152,25.97133), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.75746,26.00037,-80.11502,25.9751), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.10957,26.08717,-80.10871,26.09296), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.10871,26.09296,-80.10957,26.08717), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.80883,26.15225,-80.10871,26.09296), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.08557,26.24926,-80.07587,26.32092), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.07587,26.32092,-81.84456,26.32771), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.84456,26.32771,-81.84649,26.33037), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.84649,26.33037,-81.84456,26.32771), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.07502,26.42206,-82.12667,26.43628), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.12667,26.43628,-81.92361,26.43666), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.92361,26.43666,-82.12667,26.43628), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.01391,26.45206,-81.95661,26.45236), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.95661,26.45236,-82.01391,26.45206), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.18072,26.47626,-81.95661,26.45236), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.05036,26.50955,-82.18072,26.47626), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03886,26.56935,-82.2454,26.60109), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.2454,26.60109,-80.03536,26.61235), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03536,26.61235,-82.2454,26.60109), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03558,26.64666,-80.03576,26.67604), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03576,26.67604,-82.26435,26.6985), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.26435,26.6985,-80.03576,26.67604), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.26468,26.75684,-80.03212,26.77153), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03212,26.77153,-80.03236,26.77303), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.03236,26.77303,-80.03212,26.77153), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.28054,26.78931,-80.03236,26.77303), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.31428,26.85838,-80.04626,26.85924), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.04626,26.85924,-82.31428,26.85838), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.3692,26.94608,-80.08308,26.97053), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.08308,26.97053,-82.3692,26.94608), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.11677,27.0724,-82.45267,27.07936), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.45267,27.07936,-80.11677,27.0724), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.13861,27.11152,-82.45267,27.07936), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.15338,27.16931,-80.13861,27.11152), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.53972,27.25433,-80.19802,27.26301), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.19802,27.26301,-82.53972,27.25433), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.61058,27.34882,-80.25367,27.37979), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.25367,27.37979,-82.64817,27.38972), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.64817,27.38972,-80.25367,27.37979), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.69182,27.43722,-82.64817,27.38972), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.65072,27.52312,-82.71985,27.52893), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.71985,27.52893,-82.74302,27.53109), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.74302,27.53109,-82.71985,27.52893), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.31669,27.55734,-82.74302,27.53109), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.58463,27.59602,-80.33096,27.59754), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.33096,27.59754,-82.58463,27.59602), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.73308,27.61297,-82.70502,27.62531), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.70502,27.62531,-82.73308,27.61297), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.55289,27.64545,-82.67736,27.66483), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.67736,27.66483,-82.73847,27.6785), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.73847,27.6785,-82.67736,27.66483), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.65252,27.70031,-82.51427,27.70559), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.51427,27.70559,-82.65252,27.70031), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.47764,27.723,-82.74622,27.73131), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.74622,27.73131,-82.62502,27.73271), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.62502,27.73271,-82.74622,27.73131), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.3837,27.74005,-82.62502,27.73271), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.43198,27.76809,-82.62272,27.77987), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.62272,27.77987,-82.61483,27.7879), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.61483,27.7879,-82.79022,27.7916), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.79022,27.7916,-82.61483,27.7879), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.44879,27.81004,-82.58652,27.8167), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.58652,27.8167,-82.48985,27.82261), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.48985,27.82261,-82.58652,27.8167), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.56638,27.83634,-82.55395,27.84846), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.55395,27.84846,-82.84653,27.8543), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.84653,27.8543,-82.55395,27.84846), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.44768,27.86051,-82.84653,27.8543), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.84088,27.93716,-80.44768,27.86051), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.82816,28.02013,-80.54768,28.0488), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.54768,28.0488,-82.82816,28.02013), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.84839,28.09344,-82.85088,28.10245), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.85088,28.10245,-82.84839,28.09344), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.85938,28.17218,-82.85962,28.17414), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.85962,28.17414,-82.85938,28.17218), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.58998,28.17799,-82.85962,28.17414), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.7641,28.24435,-80.60421,28.25773), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.60421,28.25773,-82.7641,28.24435), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.73146,28.32508,-80.60687,28.33648), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.60687,28.33648,-82.73146,28.32508), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.58781,28.41086,-82.69743,28.42017), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.69743,28.42017,-80.58781,28.41086), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.6908,28.43334,-82.69743,28.42017), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.52509,28.45945,-82.66506,28.48443), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.66506,28.48443,-80.52509,28.45945), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.65669,28.54481,-80.58388,28.59771), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.58388,28.59771,-82.66815,28.62241), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.66815,28.62241,-80.58388,28.59771), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.64729,28.67788,-82.66871,28.6943), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.66871,28.6943,-82.66872,28.69566), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.66872,28.69566,-82.66871,28.6943), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.71237,28.72092,-82.66872,28.69566), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.72751,28.79119,-82.71312,28.80028), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.71312,28.80028,-80.72751,28.79119), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.73025,28.85016,-80.78702,28.87527), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.78702,28.87527,-82.73025,28.85016), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.68886,28.90561,-80.78702,28.87527), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.72386,28.95351,-82.75557,29.00093), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.75557,29.00093,-82.75938,29.00662), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.75938,29.00662,-82.75557,29.00093), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.7597,29.05419,-80.90728,29.06426), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.90728,29.06426,-82.7597,29.05419), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.82366,29.0989,-82.79888,29.1145), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.79888,29.1145,-83.01625,29.12537), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.01625,29.12537,-83.05321,29.13084), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.05321,29.13084,-83.01625,29.12537), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-80.96618,29.14796,-82.82707,29.15843), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.82707,29.15843,-80.96618,29.14796), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.92711,29.16891,-82.99614,29.17807), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.99614,29.17807,-82.92711,29.16891), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.07899,29.19694,-82.99614,29.17807), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.07473,29.24798,-83.10748,29.26889), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.10748,29.26889,-83.16592,29.28909), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.16592,29.28909,-83.16958,29.29036), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.16958,29.29036,-83.16592,29.28909), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.04668,29.30786,-83.16958,29.29036), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.17552,29.34469,-81.04668,29.30786), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.20245,29.39442,-81.10297,29.427), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.10297,29.427,-83.24051,29.43318), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.24051,29.43318,-83.29475,29.43792), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.29475,29.43792,-83.24051,29.43318), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.30783,29.46886,-83.29475,29.43792), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.40155,29.52329,-81.16358,29.55529), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.16358,29.55529,-85.04507,29.58699), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.04507,29.58699,-83.40507,29.59557), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.40507,29.59557,-85.04507,29.58699), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.15731,29.64289,-84.87673,29.65576), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.87673,29.65576,-85.35262,29.65979), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.35262,29.65979,-84.87673,29.65576), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.41413,29.66607,-85.22843,29.66956), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.22843,29.66956,-83.4147,29.67054), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.4147,29.67054,-81.21041,29.67064), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.21041,29.67064,-83.4147,29.67054), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.25972,29.6813,-81.21041,29.67064), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.77695,29.69219,-83.48357,29.69854), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.48357,29.69854,-84.77695,29.69219), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.53765,29.72306,-83.48357,29.69854), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.40283,29.75878,-84.69262,29.76304), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.69262,29.76304,-85.40283,29.75878), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.25671,29.78469,-84.604,29.78602), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.604,29.78602,-83.58305,29.78731), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.58305,29.78731,-84.604,29.78602), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.56498,29.81018,-83.58305,29.78731), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.41655,29.84263,-83.62503,29.85689), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.62503,29.85689,-85.41655,29.84263), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.27044,29.88311,-84.57744,29.88783), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.57744,29.88783,-81.27044,29.88311), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.34907,29.89681,-84.42383,29.903), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.42383,29.903,-84.34907,29.89681), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.53587,29.91009,-81.28896,29.91518), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.28896,29.91518,-83.67922,29.91851), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.67922,29.91851,-85.38473,29.92095), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.38473,29.92095,-83.67922,29.91851), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.33375,29.92372,-85.38924,29.92411), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.38924,29.92411,-84.33375,29.92372), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.47032,29.92452,-85.38924,29.92411), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.42596,29.94989,-84.34115,29.96076), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.34115,29.96076,-85.48776,29.96123), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.48776,29.96123,-84.34115,29.96076), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.34144,29.96221,-85.48776,29.96123), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.78873,29.97698,-84.34144,29.96221), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.36612,30.00866,-85.57191,30.02644), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.57191,30.02644,-83.93151,30.03907), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.93151,30.03907,-85.57191,30.02644), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.60118,30.05634,-84.28973,30.0572), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.28973,30.0572,-85.60118,30.05634), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.17915,30.07319,-84.20801,30.08478), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.20801,30.08478,-83.99231,30.08927), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.99231,30.08927,-84.12489,30.0906), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.12489,30.0906,-83.99231,30.08927), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.00072,30.09621,-85.69681,30.09689), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.69681,30.09689,-84.00072,30.09621), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.07613,30.09909,-85.69681,30.09689), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.06299,30.10138,-84.07613,30.09909), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.81122,30.17832,-81.37438,30.25293), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.37438,30.25293,-85.9961,30.2689), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.9961,30.2689,-85.99994,30.27078), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.99994,30.27078,-85.9961,30.2689), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.51832,30.28044,-85.99994,30.27078), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.41986,30.29713,-86.08996,30.30357), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.08996,30.30357,-87.41986,30.29713), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.26783,30.31548,-87.31952,30.31781), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.31952,30.31781,-87.22977,30.31963), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.22977,30.31963,-87.31952,30.31781), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.15539,30.32775,-87.22977,30.31963), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.39641,30.34004,-86.22256,30.34359), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.22256,30.34359,-87.45228,30.3441), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.45228,30.3441,-86.22256,30.34359), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.09471,30.36077,-82.14331,30.36338), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.14331,30.36338,-82.09471,30.36077), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.05098,30.36837,-82.18004,30.36861), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.18004,30.36861,-82.05098,30.36837), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.9192,30.36899,-82.18004,30.36861), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.04077,30.37014,-86.9192,30.36899), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.39738,30.3775,-86.41208,30.38035), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.41208,30.38035,-86.85063,30.38097), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.85063,30.38097,-86.41208,30.38035), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.80035,30.38451,-86.85063,30.38097), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.63295,30.3963,-87.43178,30.40319), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.43178,30.40319,-82.04201,30.40325), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.04201,30.40325,-87.43178,30.40319), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.21032,30.42458,-87.3666,30.43664), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.3666,30.43664,-82.02823,30.44739), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.02823,30.44739,-87.41469,30.45729), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.41469,30.45729,-82.02823,30.44739), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.20097,30.47443,-81.41081,30.48204), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.41081,30.48204,-82.20097,30.47443), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.42601,30.49674,-81.42895,30.50618), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.42895,30.50618,-87.44472,30.50748), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.44472,30.50748,-81.42895,30.50618), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.22943,30.52081,-81.43406,30.52257), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.43406,30.52257,-82.22943,30.52081), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.01838,30.53118,-81.43406,30.52257), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.43145,30.55025,-82.21861,30.5644), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.21861,30.5644,-87.43145,30.55025), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.41898,30.58092,-82.45958,30.58426), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.45958,30.58426,-82.45979,30.58428), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.45979,30.58428,-82.45958,30.58426), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.58401,30.59164,-82.68953,30.59789), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.68953,30.59789,-81.4431,30.60094), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.4431,30.60094,-82.01573,30.6017), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.01573,30.6017,-81.4431,30.60094), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.40119,30.60438,-82.01573,30.6017), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.87731,30.60902,-87.40119,30.60438), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.13143,30.62358,-83.13662,30.62389), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.13662,30.62389,-83.13143,30.62358), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.30935,30.63424,-83.35772,30.63714), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.35772,30.63714,-83.30935,30.63424), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.44893,30.64261,-83.49995,30.64566), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.49995,30.64566,-83.44893,30.64261), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.6117,30.65156,-82.04953,30.65554), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.04953,30.65554,-87.40019,30.6572), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.40019,30.6572,-83.74373,30.65853), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.74373,30.65853,-87.40019,30.6572), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-83.82097,30.6626,-83.74373,30.65853), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.00745,30.67207,-84.08375,30.67594), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.08375,30.67594,-84.12499,30.67804), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.12499,30.67804,-84.08375,30.67594), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.28551,30.68481,-84.38075,30.68883), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.38075,30.68883,-82.04183,30.69237), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.04183,30.69237,-87.44229,30.69266), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.44229,30.69266,-84.47452,30.69278), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.47452,30.69278,-87.44229,30.69266), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.42742,30.69802,-84.47452,30.69278), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.813,30.70965,-81.44412,30.70971), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.44412,30.70971,-84.813,30.70965), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.86346,30.7115,-84.86469,30.71154), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.86469,30.71154,-84.86346,30.7115), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.56171,30.7156,-84.86469,30.71154), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.50722,30.72294,-81.52828,30.72336), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.52828,30.72336,-81.50722,30.72294), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.63327,30.7296,-81.52828,30.72336), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.52362,30.73829,-81.66828,30.74464), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.66828,30.74464,-81.73224,30.74964), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.73224,30.74964,-82.03267,30.75067), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-82.03267,30.75067,-81.73224,30.74964), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.54227,30.76748,-84.91815,30.77208), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.91815,30.77208,-81.76338,30.77382), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.76338,30.77382,-84.91815,30.77208), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.99499,30.78607,-81.80854,30.79002), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.80854,30.79002,-81.86862,30.79276), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.86862,30.79276,-81.80854,30.79002), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.90235,30.82082,-81.90598,30.82141), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.90598,30.82141,-81.90235,30.82082), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-81.94319,30.82744,-81.90598,30.82141), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.93428,30.83403,-81.94319,30.82744), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.63494,30.86586,-84.9357,30.8787), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.9357,30.8787,-87.63494,30.86586), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-84.98376,30.93698,-87.59206,30.95146), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.59206,30.95146,-84.98376,30.93698), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.59206,30.95146,-84.98376,30.93698), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.00606,30.97704,-85.89363,30.99346), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.89363,30.99346,-86.03504,30.99375), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.03504,30.99375,-85.89363,30.99346), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.18725,30.99407,-86.03504,30.99375), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.36497,30.99444,-86.38864,30.99453), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.38864,30.99453,-86.36497,30.99444), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.56349,30.9952,-85.74972,30.99528), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.74972,30.99528,-86.56349,30.9952), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.68824,30.9962,-86.78569,30.99698), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.78569,30.99698,-85.5795,30.99703), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.5795,30.99703,-86.78569,30.99698), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.83198,30.99735,-87.59894,30.99742), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.59894,30.99742,-86.83198,30.99735), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.59883,30.99742,-86.83198,30.99735), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.51953,30.99755,-87.59894,30.99742), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-86.92785,30.99768,-87.51953,30.99755), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.498,30.99787,-85.4883,30.99796), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.4883,30.99796,-85.498,30.99787), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.42579,30.99806,-85.4883,30.99796), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.31221,30.9984,-87.42579,30.99806), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.16308,30.99902,-87.16264,30.99903), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-87.16264,30.99903,-87.16308,30.99902), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.33332,30.99956,-87.16264,30.99903), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.03129,31.00065,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.0025,31.00068,-85.14596,31.00069), mapfile, tile_dir, 0, 11, "florida-fl")
	render_tiles((-85.14596,31.00069,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "florida-fl")