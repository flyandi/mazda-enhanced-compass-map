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
    # Region: Illinois
    # Region Name: IL

	render_tiles((-89.13292,36.98206,-89.19504,36.98977), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.19504,36.98977,-89.13292,36.98206), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.25761,37.0155,-89.1289,37.01791), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.1289,37.01791,-89.25761,37.0155), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.30744,37.02876,-89.1289,37.01791), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.35946,37.04261,-89.30744,37.02876), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.53158,37.06719,-88.4904,37.06796), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.4904,37.06796,-88.4838,37.06808), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.4838,37.06808,-88.4904,37.06796), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.47613,37.06822,-88.4838,37.06808), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.16662,37.07211,-89.16809,37.07422), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.16809,37.07422,-89.16662,37.07211), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.56104,37.084,-89.16809,37.07422), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.44461,37.0986,-89.38418,37.10327), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.38418,37.10327,-88.44461,37.0986), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.61144,37.11275,-89.38418,37.10327), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.09905,37.14097,-88.69398,37.14116), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.69398,37.14116,-89.09905,37.14097), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.42478,37.1499,-88.75307,37.1547), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.75307,37.1547,-88.42478,37.1499), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.45611,37.18812,-89.05804,37.18877), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.05804,37.18877,-89.45611,37.18812), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.83505,37.19649,-89.05804,37.18877), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.47175,37.22016,-89.00097,37.2244), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.00097,37.2244,-88.928,37.22639), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.928,37.22639,-88.93352,37.22751), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.93352,37.22751,-88.93175,37.22759), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.93175,37.22759,-88.93352,37.22751), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.47053,37.25336,-89.48289,37.26095), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.48289,37.26095,-89.47053,37.25336), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.51703,37.28192,-88.51466,37.29095), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.51466,37.29095,-89.51703,37.28192), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.49516,37.3248,-89.47368,37.33485), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.47368,37.33485,-88.48695,37.3396), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.48695,37.3396,-89.47368,37.33485), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.42819,37.35616,-88.48695,37.3396), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.46586,37.40055,-88.35844,37.40486), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.35844,37.40486,-89.42594,37.40747), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.42594,37.40747,-88.35844,37.40486), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.4159,37.42122,-88.41859,37.42199), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.41859,37.42199,-88.4159,37.42122), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.28167,37.4526,-89.4712,37.46647), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.4712,37.46647,-88.15706,37.46694), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.15706,37.46694,-89.4712,37.46647), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.06229,37.48784,-88.06625,37.50414), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.06625,37.50414,-88.06229,37.48784), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.07224,37.52883,-89.5124,37.52981), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.5124,37.52981,-88.07224,37.52883), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.50179,37.5589,-89.49775,37.56999), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.49775,37.56999,-88.13162,37.57297), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.13162,37.57297,-88.13216,37.57452), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.13216,37.57452,-88.13162,37.57297), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.49405,37.58012,-88.13216,37.57452), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.50656,37.62505,-88.16006,37.65433), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.16006,37.65433,-89.50656,37.62505), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.52195,37.69648,-88.13234,37.69714), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.13234,37.69714,-89.52195,37.69648), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.59129,37.7236,-88.05959,37.74261), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.05959,37.74261,-89.66799,37.75948), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.66799,37.75948,-88.05959,37.74261), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.68722,37.79641,-88.02803,37.79922), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.02803,37.79922,-89.68722,37.79641), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.69656,37.81434,-88.02803,37.79922), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.78204,37.85509,-88.06736,37.85605), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.06736,37.85605,-89.78204,37.85509), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.05947,37.86669,-89.92319,37.87067), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.92319,37.87067,-88.05947,37.86669), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.9331,37.8801,-89.92319,37.87067), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.04086,37.89177,-89.9331,37.8801), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.85105,37.90398,-88.04086,37.89177), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.97422,37.91922,-89.85105,37.90398), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.01631,37.96157,-89.95491,37.96665), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.95491,37.96665,-90.00835,37.97018), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.00835,37.97018,-89.95491,37.96665), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.08096,38.01543,-88.03088,38.03071), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.03088,38.03071,-90.08096,38.01543), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.12601,38.05057,-87.98877,38.05559), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.98877,38.05559,-90.12601,38.05057), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.20573,38.08823,-90.21871,38.09437), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.21871,38.09437,-87.96221,38.10005), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.96221,38.10005,-90.21871,38.09437), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.25248,38.12757,-90.25275,38.12777), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.25275,38.12777,-90.25248,38.12757), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.92747,38.15195,-90.25275,38.12777), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.32235,38.18159,-87.97582,38.19783), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.97582,38.19783,-90.32235,38.18159), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.35116,38.21954,-87.9702,38.23027), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.9702,38.23027,-90.36393,38.23636), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.36393,38.23636,-87.96897,38.23739), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.96897,38.23739,-90.36393,38.23636), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.90854,38.26858,-87.96897,38.23739), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.83197,38.30724,-90.37252,38.32335), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.37252,38.32335,-87.83197,38.30724), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.78,38.37084,-90.34974,38.37761), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.34974,38.37761,-87.78,38.37084), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.34292,38.38443,-90.34024,38.38709), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.34024,38.38709,-90.34292,38.38443), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.75111,38.41885,-87.74104,38.43558), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.74104,38.43558,-90.28882,38.43845), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.28882,38.43845,-87.74104,38.43558), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.71405,38.47988,-90.27131,38.49605), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.27131,38.49605,-87.65417,38.51191), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.65417,38.51191,-90.26098,38.51853), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.26098,38.51853,-87.65417,38.51191), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.25529,38.53088,-87.66073,38.54109), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.66073,38.54109,-90.24891,38.54475), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.24891,38.54475,-87.66073,38.54109), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.64836,38.56663,-87.63775,38.58851), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.63775,38.58851,-87.64836,38.56663), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.18451,38.61155,-87.63775,38.58851), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.62012,38.63949,-90.18111,38.65955), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.18111,38.65955,-90.18152,38.66037), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.18152,38.66037,-90.18111,38.65955), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.54554,38.67761,-90.19521,38.68755), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.19521,38.68755,-87.54554,38.67761), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.20991,38.72605,-87.49895,38.75777), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.49895,38.75777,-90.16659,38.77245), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.16659,38.77245,-90.16641,38.77265), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.16641,38.77265,-90.16659,38.77245), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.11771,38.80575,-87.52168,38.82658), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52168,38.82658,-90.11771,38.80575), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.11333,38.84931,-87.53526,38.85249), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53526,38.85249,-90.11333,38.84931), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.55569,38.87079,-90.59535,38.87505), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.59535,38.87505,-87.54737,38.87561), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.54737,38.87561,-90.59535,38.87505), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.20728,38.89873,-87.52872,38.90594), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52872,38.90594,-87.52765,38.90769), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52765,38.90769,-87.52872,38.90594), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.50012,38.91041,-90.23034,38.91086), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.23034,38.91086,-90.50012,38.91041), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.27658,38.91934,-90.65725,38.92027), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.65725,38.92027,-90.27658,38.91934), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.29871,38.9234,-90.65725,38.92027), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.66158,38.9347,-90.29871,38.9234), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.39582,38.96004,-90.45097,38.9614), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.45097,38.9614,-90.46778,38.96181), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.46778,38.96181,-90.45097,38.9614), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.5295,38.97193,-90.46778,38.96181), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.6764,38.9841,-87.5295,38.97193), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.57912,39.00161,-90.6764,38.9841), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.71363,39.05398,-87.57259,39.05729), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.57259,39.05729,-90.71363,39.05398), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.68109,39.10059,-87.62538,39.10181), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.62538,39.10181,-90.68109,39.10059), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.7079,39.15086,-87.63829,39.15749), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.63829,39.15749,-90.7079,39.15086), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.64044,39.16673,-87.63829,39.15749), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.57703,39.21112,-90.72328,39.2241), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.72328,39.2241,-87.57703,39.21112), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.59349,39.24745,-90.72996,39.25589), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.72996,39.25589,-87.59475,39.25938), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.59475,39.25938,-90.72996,39.25589), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.6004,39.3129,-87.57833,39.34034), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.57833,39.34034,-90.84011,39.34044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.84011,39.34044,-87.57833,39.34034), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53165,39.34789,-90.84011,39.34044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.93535,39.39952,-90.93742,39.4008), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.93742,39.4008,-90.93535,39.39952), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.03827,39.44844,-87.53162,39.46938), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53162,39.46938,-87.53167,39.47711), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53167,39.47711,-87.53162,39.46938), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.06431,39.49464,-87.53167,39.47711), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.10031,39.5387,-91.14828,39.5458), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.14828,39.5458,-91.10031,39.5387), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.17423,39.59198,-91.18288,39.59823), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.18288,39.59823,-91.17423,39.59198), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53239,39.6073,-91.18288,39.59823), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.5327,39.66487,-91.27614,39.66576), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.27614,39.66576,-87.5327,39.66487), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.30576,39.68622,-91.27614,39.66576), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.36775,39.72903,-91.36462,39.75872), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.36462,39.75872,-91.36157,39.78755), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.36157,39.78755,-91.36462,39.75872), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.39785,39.82112,-91.43605,39.84551), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.43605,39.84551,-91.39785,39.82112), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53245,39.883,-91.42896,39.90773), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.42896,39.90773,-87.53245,39.883), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.43684,39.94524,-91.43709,39.94642), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.43709,39.94642,-91.43684,39.94524), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53231,40.01159,-91.48406,40.01933), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.48406,40.01933,-87.53231,40.01159), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.49766,40.07826,-91.48406,40.01933), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53102,40.14804,-91.51196,40.17044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.51196,40.17044,-87.53102,40.14804), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.50617,40.20064,-91.51196,40.17044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.50617,40.20064,-91.51196,40.17044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.49696,40.2487,-87.53005,40.25067), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53005,40.25067,-91.49696,40.2487), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.49289,40.26992,-87.53005,40.25067), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.46966,40.32241,-91.49289,40.26992), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.41942,40.37826,-91.37292,40.39911), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.37292,40.39911,-91.41942,40.37826), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.37991,40.45211,-87.52707,40.47688), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52707,40.47688,-87.52688,40.49122), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52688,40.49122,-87.52707,40.47688), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.36788,40.51048,-87.52688,40.49122), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.39448,40.53454,-87.52629,40.53541), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52629,40.53541,-91.39448,40.53454), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.37425,40.58259,-91.33972,40.61349), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.33972,40.61349,-91.18698,40.6373), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.18698,40.6373,-91.18546,40.63811), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.18546,40.63811,-91.24785,40.63839), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.24785,40.63839,-91.18546,40.63811), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.12082,40.67278,-91.11822,40.69953), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.11822,40.69953,-91.11574,40.72517), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.11574,40.72517,-87.52614,40.73689), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52614,40.73689,-91.11574,40.72517), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.0917,40.77971,-91.09299,40.82108), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.09299,40.82108,-91.0917,40.77971), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.04465,40.86836,-87.52601,40.89558), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52601,40.89558,-90.98546,40.91214), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.98546,40.91214,-87.52601,40.89558), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.95223,40.95405,-90.98546,40.91214), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52646,41.01035,-90.94532,41.01928), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.94532,41.01928,-87.52652,41.02484), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52652,41.02484,-90.94532,41.01928), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.95189,41.06987,-90.95227,41.07273), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.95227,41.07273,-90.95189,41.06987), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.95725,41.11109,-90.95227,41.07273), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.99791,41.16256,-87.52665,41.16609), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52665,41.16609,-91.04154,41.16614), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.04154,41.16614,-87.52665,41.16609), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.08145,41.21443,-91.11419,41.25003), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.11419,41.25003,-91.08145,41.21443), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52677,41.29805,-87.52677,41.29818), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52677,41.29818,-87.52677,41.29805), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.07442,41.33363,-91.07409,41.33432), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.07409,41.33432,-91.07442,41.33363), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.07155,41.33965,-91.07409,41.33432), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.06506,41.3691,-91.07155,41.33965), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.92434,41.42286,-91.02779,41.4236), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-91.02779,41.4236,-90.92434,41.42286), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.96666,41.43005,-91.02779,41.4236), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.86728,41.44822,-90.78628,41.45289), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.78628,41.45289,-90.70116,41.45474), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.70116,41.45474,-90.78628,41.45289), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52541,41.47028,-90.61854,41.48503), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.61854,41.48503,-87.52541,41.47028), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.57114,41.51633,-90.51313,41.51953), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.51313,41.51953,-90.57114,41.51633), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.46143,41.52353,-90.51313,41.51953), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52494,41.52974,-90.46143,41.52353), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.41583,41.56293,-90.36413,41.57963), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.36413,41.57963,-90.41583,41.56293), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.33953,41.59863,-90.36413,41.57963), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.33673,41.66453,-90.31469,41.69483), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.31469,41.69483,-87.52404,41.70834), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52404,41.70834,-90.31469,41.69483), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.52414,41.72399,-90.31186,41.72853), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.31186,41.72853,-87.52414,41.72399), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.31071,41.74221,-87.53075,41.74824), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.53075,41.74824,-90.31071,41.74221), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.56065,41.76603,-90.24863,41.77981), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.24863,41.77981,-90.24237,41.78277), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.24237,41.78277,-90.24863,41.77981), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.5816,41.80004,-87.58838,41.81103), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.58838,41.81103,-90.18064,41.81198), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.18064,41.81198,-87.58838,41.81103), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.1814,41.84465,-87.60945,41.84523), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.60945,41.84523,-90.1814,41.84465), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.61629,41.87093,-90.16507,41.88378), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.16507,41.88378,-87.61356,41.88448), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.61356,41.88448,-90.16507,41.88378), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.61229,41.89334,-87.61356,41.88448), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.62405,41.90423,-87.62498,41.90682), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.62498,41.90682,-87.62405,41.90423), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.15815,41.92984,-87.63437,41.93291), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.63437,41.93291,-90.15815,41.92984), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.1569,41.93818,-87.63437,41.93291), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.14061,41.996,-87.66898,42.02914), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.66898,42.02914,-90.15968,42.03309), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.15968,42.03309,-87.66898,42.02914), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.16345,42.04041,-90.15968,42.03309), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.68236,42.07573,-87.68928,42.08185), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.68928,42.08185,-87.68236,42.07573), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.16116,42.10637,-87.74136,42.12796), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.74136,42.12796,-87.74166,42.12823), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.74166,42.12823,-87.74169,42.12827), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.74169,42.12827,-87.74166,42.12823), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.20742,42.14911,-87.75933,42.15236), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.75933,42.15236,-90.20742,42.14911), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.26908,42.1745,-90.3157,42.19395), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.3157,42.19395,-90.33817,42.20332), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.33817,42.20332,-87.79859,42.20601), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.79859,42.20601,-87.80007,42.20802), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.80007,42.20802,-87.79859,42.20601), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.40065,42.23929,-87.80007,42.20802), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.43088,42.27823,-87.83338,42.29777), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.83338,42.29777,-87.83477,42.30152), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.83477,42.30152,-87.83338,42.29777), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.41713,42.31994,-87.83477,42.30152), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.44632,42.35704,-87.82086,42.36158), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.82086,42.36158,-90.44632,42.35704), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.48435,42.3816,-87.82086,42.36158), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.51752,42.40302,-87.80337,42.42062), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.80337,42.42062,-90.51752,42.40302), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.56525,42.43874,-90.59042,42.44749), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.59042,42.44749,-90.56525,42.43874), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.64673,42.4719,-87.80048,42.49192), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.80048,42.49192,-87.8977,42.49285), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-87.8977,42.49285,-88.70738,42.49359), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.70738,42.49359,-88.7765,42.49414), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.70738,42.49359,-88.7765,42.49414), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.7765,42.49414,-88.70738,42.49359), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.50691,42.49488,-88.94038,42.49544), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.94038,42.49544,-88.30469,42.49561), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.30469,42.49561,-88.19953,42.49576), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.19953,42.49576,-88.99256,42.49585), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.99256,42.49585,-88.2169,42.49592), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-88.2169,42.49592,-88.99256,42.49585), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.0429,42.49626,-88.2169,42.49592), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.3658,42.50003,-89.40142,42.50044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.40142,42.50044,-89.3658,42.50003), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.49322,42.50151,-89.40142,42.50044), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.83759,42.50491,-89.92648,42.50579), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.92648,42.50579,-89.83759,42.50491), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-89.92701,42.50579,-89.83759,42.50491), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.43701,42.50715,-90.42638,42.50718), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.42638,42.50718,-90.43701,42.50715), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.22319,42.50777,-90.42638,42.50718), mapfile, tile_dir, 0, 11, "illinois-il")
	render_tiles((-90.64284,42.50848,-90.22319,42.50777), mapfile, tile_dir, 0, 11, "illinois-il")