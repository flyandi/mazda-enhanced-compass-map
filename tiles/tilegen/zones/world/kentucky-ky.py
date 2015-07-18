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
    # Region: Kentucky
    # Region Name: KY

	render_tiles((-89.53923,36.49793,-88.12738,36.49854), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.12738,36.49854,-89.41729,36.49903), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.41729,36.49903,-88.12738,36.49854), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.05335,36.5,-88.05047,36.50005), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.05047,36.50005,-88.05335,36.5), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.48908,36.50128,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.34519,36.50134,-88.48908,36.50128), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.51636,36.50146,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.51192,36.50146,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.81676,36.50195,-88.82718,36.50197), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.82718,36.50197,-88.83459,36.50198), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.83459,36.50198,-88.82718,36.50197), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.96447,36.50219,-88.83459,36.50198), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.21141,36.50563,-88.96447,36.50219), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.57148,36.53809,-88.0338,36.55173), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.0338,36.55173,-89.40791,36.56235), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.40791,36.56235,-89.47935,36.56625), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.47935,36.56625,-89.22732,36.56938), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.22732,36.56938,-89.47935,36.56625), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.54443,36.57451,-89.27894,36.5777), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.54443,36.57451,-89.27894,36.5777), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.27894,36.5777,-89.54443,36.57451), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.69071,36.58258,-83.89442,36.58648), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.89442,36.58648,-83.93076,36.58769), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.93076,36.58769,-83.89442,36.58648), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.98761,36.58959,-83.98784,36.5896), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.98784,36.5896,-83.98761,36.58959), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.22733,36.59218,-84.26132,36.59274), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.22719,36.59218,-84.26132,36.59274), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.26132,36.59274,-84.22733,36.59218), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.49994,36.59668,-84.26132,36.59274), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.67541,36.60081,-84.77846,36.60321), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.77846,36.60321,-84.78534,36.60337), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.78534,36.60337,-84.7854,36.60338), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.7854,36.60338,-84.78534,36.60337), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.94395,36.61257,-84.97487,36.61458), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.97487,36.61458,-85.48835,36.61499), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.48835,36.61499,-84.97487,36.61458), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.4364,36.618,-85.73186,36.62043), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.73186,36.62043,-85.78856,36.62171), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.78856,36.62171,-89.37869,36.62229), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.37869,36.62229,-85.09613,36.62248), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.09613,36.62248,-89.37869,36.62229), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.87386,36.62364,-89.32732,36.62395), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.32732,36.62395,-89.32466,36.62403), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.32466,36.62403,-89.32732,36.62395), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.19914,36.62565,-85.29581,36.62615), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.29581,36.62615,-85.27629,36.62616), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.27629,36.62616,-85.29581,36.62615), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.29063,36.62645,-85.27629,36.62616), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.97571,36.62864,-88.05574,36.63048), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.05574,36.63048,-85.97571,36.62864), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.8532,36.63325,-86.08194,36.63385), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.08194,36.63385,-83.61451,36.63398), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.61451,36.63398,-86.08194,36.63385), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.69419,36.63684,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.55129,36.63799,-87.64115,36.63804), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.64115,36.63804,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.64115,36.63804,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.20557,36.63925,-87.64115,36.63804), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.56203,36.64074,-87.3478,36.64144), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.3478,36.64144,-87.33598,36.64158), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.33598,36.64158,-87.3478,36.64144), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.115,36.64414,-87.06083,36.64477), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.06083,36.64477,-87.115,36.64414), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.81304,36.64765,-86.4115,36.64824), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.4115,36.64824,-86.76329,36.64872), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.76329,36.64872,-86.4115,36.64824), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.17565,36.65132,-86.60639,36.65211), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.60639,36.65211,-86.50777,36.65245), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.50777,36.65245,-86.60639,36.65211), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.16549,36.66243,-87.84957,36.6637), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.84957,36.6637,-89.16549,36.66243), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.52711,36.66599,-83.46095,36.66613), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.46095,36.66613,-83.43651,36.66619), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.43651,36.66619,-83.46095,36.66613), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.01179,36.67703,-88.07053,36.67812), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.07053,36.67812,-88.01179,36.67703), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.3861,36.68659,-88.07053,36.67812), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.20251,36.71662,-83.2364,36.72689), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.2364,36.72689,-89.20251,36.71662), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.1364,36.74309,-89.15699,36.75597), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.15699,36.75597,-83.1364,36.74309), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.15598,36.78629,-89.15589,36.78913), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.15589,36.78913,-89.15598,36.78629), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.11469,36.79609,-89.15589,36.78913), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.14767,36.84715,-83.01259,36.84729), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.01259,36.84729,-89.14767,36.84715), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.07559,36.85059,-83.01259,36.84729), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.89545,36.88215,-89.12047,36.8919), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.12047,36.8919,-82.88361,36.89731), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.88361,36.89731,-89.12047,36.8919), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.86519,36.92092,-82.88361,36.89731), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.10314,36.94476,-89.09884,36.95785), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.09884,36.95785,-89.10314,36.94476), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.86918,36.97418,-89.13292,36.98206), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.13292,36.98206,-82.86918,36.97418), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.81575,37.0072,-89.1289,37.01791), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.1289,37.01791,-82.75072,37.02411), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.75072,37.02411,-89.1289,37.01791), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.72225,37.05795,-88.53158,37.06719), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.53158,37.06719,-88.4904,37.06796), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.4904,37.06796,-88.4838,37.06808), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.4838,37.06808,-88.4904,37.06796), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.47613,37.06822,-88.4838,37.06808), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.16662,37.07211,-89.16809,37.07422), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.16809,37.07422,-89.16662,37.07211), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.56104,37.084,-89.16809,37.07422), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.44461,37.0986,-82.72629,37.11185), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.72629,37.11185,-88.61144,37.11275), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.61144,37.11275,-82.72629,37.11185), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.09905,37.14097,-88.69398,37.14116), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.69398,37.14116,-89.09905,37.14097), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.42478,37.1499,-88.75307,37.1547), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.75307,37.1547,-88.42478,37.1499), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.05804,37.18877,-82.56528,37.1959), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.56528,37.1959,-88.83505,37.19649), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.83505,37.19649,-82.56528,37.1959), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.55818,37.19961,-82.55363,37.20145), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.55363,37.20145,-82.55818,37.19961), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.47175,37.22016,-89.00097,37.2244), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-89.00097,37.2244,-88.928,37.22639), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.928,37.22639,-88.93352,37.22751), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.93352,37.22751,-88.93175,37.22759), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.93175,37.22759,-88.93352,37.22751), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.44916,37.24391,-88.93175,37.22759), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.35534,37.26522,-82.44916,37.24391), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.51466,37.29095,-82.31478,37.29599), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.31478,37.29599,-82.30942,37.30007), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.30942,37.30007,-82.31478,37.29599), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.48695,37.3396,-82.20175,37.37511), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.20175,37.37511,-88.46586,37.40055), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.46586,37.40055,-88.35844,37.40486), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.35844,37.40486,-88.46586,37.40055), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.4159,37.42122,-88.41859,37.42199), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.41859,37.42199,-88.4159,37.42122), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.28167,37.4526,-88.15706,37.46694), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.15706,37.46694,-88.28167,37.4526), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.06229,37.48784,-88.06625,37.50414), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.06625,37.50414,-88.06229,37.48784), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.07224,37.52883,-81.9683,37.5378), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-81.9683,37.5378,-82.06442,37.54452), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.06442,37.54452,-81.9683,37.5378), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.13162,37.57297,-88.13216,37.57452), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.13216,37.57452,-88.13162,37.57297), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.14156,37.59517,-88.13216,37.57452), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.22611,37.65309,-88.16006,37.65433), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.16006,37.65433,-82.22611,37.65309), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.29612,37.68617,-88.13234,37.69714), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.13234,37.69714,-82.29612,37.68617), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.05959,37.74261,-82.32067,37.74597), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.32067,37.74597,-88.05959,37.74261), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.32736,37.76223,-87.10561,37.76763), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.10561,37.76763,-82.32736,37.76223), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.97026,37.78186,-87.93586,37.7897), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.93586,37.7897,-87.97026,37.78186), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-88.02803,37.79922,-82.36997,37.80175), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.36997,37.80175,-88.02803,37.79922), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.1375,37.80726,-82.36997,37.80175), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.9038,37.81776,-87.05784,37.82746), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.05784,37.82746,-87.9038,37.81776), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.18006,37.84138,-82.39846,37.84305), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.39846,37.84305,-87.18006,37.84138), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.62585,37.85192,-86.61522,37.85286), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.61522,37.85286,-87.62585,37.85192), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.68163,37.85592,-86.61522,37.85286), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.6467,37.86491,-87.25525,37.86733), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.25525,37.86733,-86.65837,37.86938), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.65837,37.86938,-87.93813,37.87065), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.93813,37.87065,-86.65837,37.86938), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.41869,37.87238,-87.93813,37.87065), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.04305,37.87505,-87.80801,37.87519), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.80801,37.87519,-87.04305,37.87505), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.27039,37.87542,-87.80801,37.87519), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.71321,37.88309,-87.27039,37.87542), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.72364,37.89206,-86.72225,37.89265), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.72225,37.89265,-87.72364,37.89206), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.30406,37.89343,-86.72225,37.89265), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.60848,37.89879,-87.92539,37.89959), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.92539,37.89959,-87.60848,37.89879), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.50937,37.90289,-87.92539,37.89959), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.59985,37.90675,-87.92174,37.90789), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.92174,37.90789,-87.33177,37.90825), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.33177,37.90825,-87.92174,37.90789), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.48756,37.91698,-87.01032,37.91967), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.01032,37.91967,-87.48635,37.92022), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.48635,37.92022,-87.01032,37.91967), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.87254,37.921,-87.48635,37.92022), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.55128,37.92542,-86.97774,37.9257), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.97774,37.9257,-87.55128,37.92542), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.44864,37.93388,-86.92775,37.93496), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.92775,37.93496,-87.44864,37.93388), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.47942,37.93856,-86.92775,37.93496), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-87.41859,37.94476,-82.47942,37.93856), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.77999,37.95652,-86.52517,37.96823), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.52517,37.96823,-86.03339,37.97038), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.03339,37.97038,-86.87587,37.97077), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.87587,37.97077,-86.03339,37.97038), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.46499,37.97686,-86.87587,37.97077), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.99735,37.99123,-86.81366,37.99603), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.81366,37.99603,-86.81091,37.99715), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.81091,37.99715,-86.81366,37.99603), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.97603,38.00356,-86.09577,38.00893), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.09577,38.00893,-85.97603,38.00356), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.95173,38.01494,-86.09577,38.00893), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.20644,38.02188,-85.9224,38.02868), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.9224,38.02868,-86.20644,38.02188), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.52183,38.03833,-86.48805,38.04367), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.48805,38.04367,-86.4719,38.04622), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.4719,38.04622,-86.48805,38.04367), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.54941,38.06306,-86.4719,38.04622), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.43405,38.08676,-86.43357,38.08714), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.43357,38.08714,-86.43405,38.08676), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.27866,38.09851,-86.43357,38.08714), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.90516,38.11107,-86.27866,38.09851), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.38722,38.12463,-82.62618,38.13484), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.62618,38.13484,-86.35641,38.13528), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.35641,38.13528,-82.62618,38.13484), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-86.32127,38.14742,-86.35641,38.13528), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.89591,38.17993,-85.89476,38.18847), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.89476,38.18847,-85.89591,38.17993), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.59886,38.20101,-85.89476,38.18847), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.83966,38.23977,-82.58469,38.24051), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.58469,38.24051,-85.83966,38.23977), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.5818,38.24859,-82.58469,38.24051), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.75096,38.26787,-85.79451,38.27795), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.79451,38.27795,-85.81616,38.28297), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.81616,38.28297,-85.79451,38.27795), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.68356,38.29547,-85.81616,38.28297), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.57188,38.31578,-85.68356,38.29547), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.6462,38.34292,-82.59798,38.34491), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.59798,38.34491,-85.6462,38.34292), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.63444,38.3784,-82.59596,38.38089), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.59596,38.38089,-85.63444,38.3784), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.62163,38.41709,-82.59367,38.42181), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.59367,38.42181,-85.62163,38.41709), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.58776,38.4505,-85.49887,38.46824), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.49887,38.46824,-82.61847,38.47709), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.61847,38.47709,-85.49887,38.46824), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.47435,38.50407,-82.66412,38.50772), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.66412,38.50772,-85.47435,38.50407), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.67572,38.5155,-82.66412,38.50772), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.43314,38.52391,-85.43297,38.52412), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.43297,38.52412,-85.43314,38.52391), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.4156,38.54634,-82.72485,38.5576), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.72485,38.5576,-82.80011,38.56318), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.80011,38.56318,-82.72485,38.5576), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.81154,38.57237,-82.80011,38.56318), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.43142,38.58629,-85.43617,38.59829), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.43617,38.59829,-83.28651,38.59924), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.28651,38.59924,-85.43617,38.59829), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.85131,38.60433,-83.28651,38.59924), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.2643,38.61311,-83.17265,38.62025), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.17265,38.62025,-83.32053,38.62271), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.32053,38.62271,-83.17265,38.62025), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.23952,38.62859,-83.67948,38.63004), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.67948,38.63004,-83.23952,38.62859), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.70586,38.63804,-83.12897,38.64023), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.12897,38.64023,-83.64691,38.64185), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.64691,38.64185,-83.64299,38.64327), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.64299,38.64327,-83.64691,38.64185), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.77216,38.65815,-85.43874,38.65932), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.43874,38.65932,-83.77216,38.65815), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.3763,38.66147,-85.43874,38.65932), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.4404,38.66936,-83.11237,38.67169), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.11237,38.67169,-83.4404,38.66936), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.86959,38.67818,-83.62692,38.67939), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.62692,38.67939,-82.86959,38.67818), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.18728,38.68761,-85.14686,38.69543), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.14686,38.69543,-83.78362,38.69564), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.78362,38.69564,-85.14686,38.69543), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.20176,38.69744,-83.78362,38.69564), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.53334,38.70211,-85.20176,38.69744), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.04234,38.70832,-85.44886,38.71337), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.44886,38.71337,-83.83402,38.71601), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.83402,38.71601,-83.03033,38.71687), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.03033,38.71687,-83.83402,38.71601), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.87119,38.71838,-83.03033,38.71687), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.23867,38.72249,-82.87119,38.71838), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.01182,38.73006,-85.34095,38.73389), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.34095,38.73389,-85.33264,38.73482), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.33264,38.73482,-85.34095,38.73389), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.40048,38.73598,-85.33264,38.73482), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.27545,38.74117,-85.07193,38.74157), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.07193,38.74157,-82.88229,38.74162), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.88229,38.74162,-85.07193,38.74157), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.94315,38.74328,-82.88229,38.74162), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.85209,38.75143,-82.88919,38.75608), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-82.88919,38.75608,-85.02105,38.75853), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-85.02105,38.75853,-82.88919,38.75608), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.90438,38.76728,-84.05164,38.7714), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.05164,38.7714,-84.05265,38.77161), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.05265,38.77161,-84.05164,38.7714), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.92845,38.77458,-84.05265,38.77161), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.96254,38.77804,-83.92845,38.77458), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.81288,38.78609,-83.97881,38.7871), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-83.97881,38.7871,-84.81288,38.78609), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.13509,38.78949,-84.8569,38.79022), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.8569,38.79022,-84.13509,38.78949), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.2129,38.80571,-84.8569,38.79022), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.22616,38.82978,-84.23327,38.84267), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.23327,38.84267,-84.80325,38.85072), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.80325,38.85072,-84.23327,38.84267), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.7987,38.85923,-84.80325,38.85072), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.23231,38.87471,-84.23213,38.88048), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.23213,38.88048,-84.78641,38.88222), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.78641,38.88222,-84.23213,38.88048), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.83047,38.89726,-84.78641,38.88222), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.86443,38.91384,-84.87776,38.92036), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.87776,38.92036,-84.86443,38.91384), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.28816,38.95579,-84.83262,38.96146), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.83262,38.96146,-84.28816,38.95579), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.29726,38.98969,-84.84945,39.00092), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.84945,39.00092,-84.29726,38.98969), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.32121,39.02059,-84.32654,39.02746), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.32654,39.02746,-84.87757,39.03126), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.87757,39.03126,-84.32654,39.02746), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.40094,39.04636,-84.89717,39.05241), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.89717,39.05241,-84.40094,39.04636), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.60793,39.07324,-84.86069,39.07814), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.86069,39.07814,-84.62228,39.07842), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.62228,39.07842,-84.86069,39.07814), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.43294,39.08396,-84.62228,39.07842), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.67725,39.09826,-84.55084,39.09936), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.55084,39.09936,-84.67725,39.09826), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.50652,39.10177,-84.49919,39.10216), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.49919,39.10216,-84.49374,39.10246), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.49374,39.10246,-84.49919,39.10216), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.82016,39.10548,-84.49374,39.10246), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.44524,39.11446,-84.48094,39.11676), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.48094,39.11676,-84.44524,39.11446), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.46204,39.12176,-84.48094,39.11676), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.71405,39.13266,-84.46204,39.12176), mapfile, tile_dir, 0, 11, "kentucky-ky")
	render_tiles((-84.75075,39.14736,-84.71405,39.13266), mapfile, tile_dir, 0, 11, "kentucky-ky")