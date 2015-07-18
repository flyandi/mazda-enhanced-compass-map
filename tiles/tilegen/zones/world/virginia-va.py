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
    # Region: Virginia
    # Region Name: VA

	render_tiles((-75.9424,37.08961,-75.97961,37.10045), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.97961,37.10045,-75.9424,37.08961), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.8973,37.11804,-75.97961,37.10045), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.99865,37.18874,-75.81739,37.19344), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.81739,37.19344,-75.99865,37.18874), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.79516,37.2471,-76.02348,37.28907), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.02348,37.28907,-75.77882,37.29718), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.02348,37.28907,-75.77882,37.29718), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.77882,37.29718,-76.02348,37.28907), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.73583,37.33543,-75.98712,37.36855), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.98712,37.36855,-75.72074,37.37313), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.72074,37.37313,-75.98712,37.36855), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.97649,37.44488,-75.65838,37.45182), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.65838,37.45182,-75.97649,37.44488), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.66542,37.46729,-75.65838,37.45182), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.67288,37.4837,-75.66542,37.46729), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.94557,37.54904,-75.60782,37.56071), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.60782,37.56071,-75.94118,37.56384), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.94118,37.56384,-75.60782,37.56071), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.61453,37.6093,-75.89867,37.63543), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.89867,37.63543,-75.61453,37.6093), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.59195,37.66318,-75.89867,37.63543), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.85926,37.70311,-75.59195,37.66318), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.5519,37.74812,-75.81216,37.7495), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.81216,37.7495,-75.5519,37.74812), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.81813,37.7917,-75.73588,37.81656), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.73588,37.81656,-75.48927,37.83246), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.48927,37.83246,-75.73588,37.81656), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.70291,37.84966,-75.38064,37.8517), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.38064,37.8517,-75.70291,37.84966), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.439,37.86934,-75.38064,37.8517), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.33862,37.89499,-75.75769,37.90391), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.75769,37.90391,-75.33862,37.89499), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.66971,37.9508,-75.62434,37.99421), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.62434,37.99421,-75.24227,38.02721), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.24227,38.02721,-75.62434,37.99421), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.51096,36.54074,-79.47015,36.54084), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.51065,36.54074,-79.47015,36.54084), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.47015,36.54084,-79.51096,36.54074), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.50997,36.54107,-79.34269,36.54114), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.34269,36.54114,-78.50997,36.54107), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.71486,36.54143,-79.21864,36.54144), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.21864,36.54144,-78.45743,36.54145), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.45743,36.54145,-79.21864,36.54144), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.73412,36.54161,-79.13794,36.54164), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.13794,36.54164,-78.73412,36.54161), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.7963,36.54176,-79.13794,36.54164), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.89157,36.54203,-78.94201,36.54211), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.94201,36.54211,-79.89157,36.54203), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.32364,36.54242,-80.02727,36.5425), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.02727,36.5425,-78.32364,36.54242), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.05345,36.54264,-80.02727,36.5425), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.13291,36.54381,-80.29524,36.54397), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.29524,36.54397,-78.13291,36.54381), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.0462,36.5442,-80.29524,36.54397), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.89977,36.54485,-77.7671,36.54544), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.7671,36.54544,-77.74971,36.54552), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.74971,36.54552,-77.7671,36.54544), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.29877,36.54604,-76.91732,36.54605), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.91732,36.54605,-77.29877,36.54604), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.91606,36.54608,-76.91573,36.54609), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.91573,36.54609,-76.91606,36.54608), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.16435,36.54615,-77.19018,36.54616), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.19018,36.54616,-77.16435,36.54615), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.43161,36.55022,-76.31322,36.55055), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.31322,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.12235,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.3132,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.02675,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.4401,36.5506,-76.31322,36.55055), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.49148,36.55073,-75.86704,36.55075), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.86704,36.55075,-76.49148,36.55073), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.5416,36.55078,-75.86704,36.55075), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.73833,36.55099,-76.5416,36.55078), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.61219,36.55822,-80.90184,36.56175), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.90184,36.56175,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.90173,36.56175,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.84021,36.56193,-80.90184,36.56175), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.70483,36.56232,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.06187,36.56702,-80.70483,36.56232), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.17671,36.57193,-81.35313,36.57624), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.35313,36.57624,-81.49983,36.57982), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.49983,36.57982,-81.35313,36.57624), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.67754,36.58812,-82.83043,36.59376), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.83043,36.59376,-81.93414,36.59421), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.93414,36.59421,-82.14607,36.59456), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.14607,36.59456,-82.17398,36.59461), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.17398,36.59461,-82.14607,36.59456), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.24339,36.59488,-82.29414,36.59507), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.29414,36.59507,-82.60918,36.59509), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.60918,36.59509,-82.29414,36.59507), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.98445,36.59529,-82.60918,36.59509), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.48724,36.59582,-82.98445,36.59529), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.2763,36.59819,-83.47209,36.59948), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.47209,36.59948,-83.2763,36.59819), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.67541,36.60081,-83.47209,36.59948), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.6469,36.61192,-81.82673,36.61472), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.82673,36.61472,-81.92264,36.61621), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.92264,36.61621,-81.82673,36.61472), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.89095,36.63075,-83.61451,36.63398), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.61451,36.63398,-75.89095,36.63075), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.61451,36.63398,-75.89095,36.63075), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.52711,36.66599,-83.46095,36.66613), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.46095,36.66613,-83.43651,36.66619), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.43651,36.66619,-83.46095,36.66613), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.3861,36.68659,-75.92175,36.69205), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.92175,36.69205,-83.3861,36.68659), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.2364,36.72689,-83.1364,36.74309), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.1364,36.74309,-83.2364,36.72689), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.11469,36.79609,-75.96159,36.8), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.96159,36.8,-83.11469,36.79609), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.01259,36.84729,-83.07559,36.85059), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-83.07559,36.85059,-83.01259,36.84729), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.89545,36.88215,-82.88361,36.89731), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.88361,36.89731,-76.08796,36.90865), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.08796,36.90865,-82.88361,36.89731), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.86519,36.92092,-75.99625,36.92205), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-75.99625,36.92205,-82.86519,36.92092), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.04305,36.92755,-76.17695,36.92854), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.17695,36.92854,-76.04305,36.92755), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.18996,36.93145,-76.17695,36.92854), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.26796,36.96455,-82.86918,36.97418), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.86918,36.97418,-76.28097,36.97774), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.28097,36.97774,-82.86918,36.97418), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.29679,36.99379,-76.30427,37.00138), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.30427,37.00138,-82.81575,37.0072), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.81575,37.0072,-76.30427,37.00138), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.75072,37.02411,-82.81575,37.0072), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.28389,37.05273,-82.72225,37.05795), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.72225,37.05795,-76.28389,37.05273), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.27126,37.08454,-82.72225,37.05795), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.72629,37.11185,-76.29313,37.11416), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.29313,37.11416,-82.72629,37.11185), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.31109,37.1385,-76.29313,37.11416), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.3431,37.18655,-76.34719,37.18964), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.34719,37.18964,-76.3431,37.18655), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.56528,37.1959,-82.55818,37.19961), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.55818,37.19961,-82.55363,37.20145), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.55363,37.20145,-81.6786,37.20247), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.6786,37.20247,-82.55363,37.20145), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.56063,37.20666,-81.6786,37.20247), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.53307,37.22341,-76.39413,37.22515), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.39413,37.22515,-81.53307,37.22341), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.2251,37.23487,-81.73906,37.2395), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.73906,37.2395,-81.744,37.24253), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.744,37.24253,-82.44916,37.24391), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.44916,37.24391,-81.744,37.24253), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.37694,37.24949,-81.48356,37.2506), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.48356,37.2506,-76.37694,37.24949), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.35534,37.26522,-76.36229,37.27023), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.36229,37.27023,-81.42795,37.27102), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.42795,37.27102,-76.36229,37.27023), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.77475,37.27485,-81.1126,37.2785), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.1126,37.2785,-81.77475,37.27485), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.84995,37.28523,-81.1126,37.2785), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.31478,37.29599,-80.99601,37.29955), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.99601,37.29955,-82.30942,37.30007), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.30942,37.30007,-80.99601,37.29955), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.98085,37.30085,-82.30942,37.30007), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.91926,37.30616,-76.38777,37.30767), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.38777,37.30767,-80.91926,37.30616), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.27555,37.30996,-76.38777,37.30767), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.896,37.33197,-80.83548,37.33482), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.83548,37.33482,-81.896,37.33197), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.36216,37.33769,-80.83548,37.33482), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.77008,37.37236,-76.36675,37.3745), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.36675,37.3745,-82.20175,37.37511), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-82.20175,37.37511,-76.24846,37.37514), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.24846,37.37514,-82.20175,37.37511), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.43753,37.37975,-80.88325,37.38393), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.88325,37.38393,-76.43753,37.37975), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.9336,37.38922,-76.40295,37.3926), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.40295,37.3926,-76.39396,37.39594), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.39396,37.39594,-76.40295,37.3926), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.66497,37.41422,-81.93695,37.41992), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.93695,37.41992,-80.86515,37.41993), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.86515,37.41993,-81.93695,37.41992), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.85815,37.42101,-80.85736,37.42113), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.85736,37.42113,-80.85815,37.42101), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.25045,37.42189,-80.85736,37.42113), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.83645,37.42436,-80.46482,37.42614), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.46482,37.42614,-80.83645,37.42436), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.47128,37.43007,-80.46482,37.42614), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.98489,37.45432,-80.39988,37.46231), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.39988,37.46231,-81.98489,37.45432), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.54484,37.4747,-80.39988,37.46231), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.27349,37.49532,-81.93228,37.51196), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.93228,37.51196,-76.27349,37.49532), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.28949,37.53608,-80.29164,37.53651), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.29164,37.53651,-76.28949,37.53608), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-81.9683,37.5378,-80.29164,37.53651), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.29796,37.55764,-81.9683,37.5378), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.28244,37.58548,-76.28888,37.58736), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.28888,37.58736,-80.28244,37.58548), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.27945,37.61823,-80.22339,37.62319), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.22339,37.62319,-80.2243,37.62399), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.2243,37.62399,-80.22339,37.62319), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.32912,37.67098,-76.3253,37.68257), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.3253,37.68257,-80.29226,37.68373), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.29226,37.68373,-76.3253,37.68257), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.29003,37.68614,-80.29226,37.68373), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.31286,37.72034,-80.25814,37.72061), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.25814,37.72061,-76.31286,37.72034), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.21862,37.78329,-76.31031,37.79485), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.31031,37.79485,-80.21862,37.78329), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.19963,37.82751,-76.25136,37.83307), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.25136,37.83307,-80.19963,37.82751), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.23673,37.88917,-80.13193,37.8895), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.13193,37.8895,-76.23673,37.88917), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.31695,37.93493,-80.05581,37.95188), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.05581,37.95188,-80.03624,37.96792), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-80.03624,37.96792,-76.42749,37.97704), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.42749,37.97704,-80.03624,37.96792), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.492,38.01722,-76.51069,38.03949), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.51069,38.03949,-79.97123,38.04433), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.97123,38.04433,-76.51069,38.03949), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.96198,38.06361,-76.53592,38.06953), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.53592,38.06953,-79.96198,38.06361), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.60094,38.11008,-79.93895,38.11162), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.93895,38.11162,-76.60094,38.11008), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.61394,38.14859,-76.68489,38.1565), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.68489,38.1565,-76.74969,38.16211), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.74969,38.16211,-76.8388,38.16348), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.8388,38.16348,-76.74969,38.16211), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.91617,38.18439,-76.91083,38.19707), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.91083,38.19707,-79.91617,38.18439), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.96231,38.21408,-76.91083,38.19707), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.85032,38.23333,-76.9578,38.24318), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.9578,38.24318,-79.85032,38.23333), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.79701,38.26727,-79.78754,38.2733), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.78754,38.2733,-76.99026,38.27394), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.99026,38.27394,-79.78754,38.2733), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-76.99679,38.27915,-76.99026,38.27394), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.0263,38.30269,-79.80409,38.31392), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.80409,38.31392,-77.0263,38.30269), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.02095,38.32927,-77.2653,38.33317), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2653,38.33317,-77.02095,38.32927), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.16269,38.34599,-77.283,38.35033), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.283,38.35033,-77.09371,38.3528), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.09371,38.3528,-77.283,38.35033), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.7346,38.35673,-77.04814,38.36015), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.04814,38.36015,-79.7346,38.35673), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.31729,38.38358,-77.04814,38.36015), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.29776,38.41644,-79.3113,38.41845), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.3113,38.41845,-79.29776,38.41644), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.3703,38.42724,-79.68968,38.43144), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.68968,38.43144,-79.3703,38.42724), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.47664,38.45723,-79.69109,38.46374), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.69109,38.46374,-77.32262,38.46713), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.32262,38.46713,-79.69109,38.46374), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.23162,38.47404,-79.22826,38.48004), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.22826,38.48004,-79.23162,38.47404), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.3129,38.50089,-79.66913,38.51088), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.66913,38.51088,-77.3129,38.50089), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.20146,38.52782,-79.66913,38.51088), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.29923,38.54838,-79.54257,38.55322), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.54257,38.55322,-77.29923,38.54838), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.29527,38.56213,-79.54257,38.55322), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.64908,38.59152,-79.15436,38.60652), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.15436,38.60652,-79.64908,38.59152), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.1302,38.63502,-77.22415,38.63518), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.22415,38.63518,-77.2467,38.63522), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2467,38.63522,-77.22415,38.63518), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.09296,38.65952,-77.1325,38.67382), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.1325,38.67382,-79.09296,38.65952), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.08806,38.69012,-77.08578,38.70528), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.08578,38.70528,-77.0795,38.70952), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.0795,38.70952,-77.0532,38.70992), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.0532,38.70992,-77.0795,38.70952), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.041,38.73791,-77.04067,38.74669), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.04067,38.74669,-77.041,38.73791), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.05725,38.76141,-78.86928,38.76299), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.86928,38.76299,-79.05725,38.76141), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.03924,38.78534,-77.03901,38.79165), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.03901,38.79165,-77.03924,38.78534), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-79.02305,38.79861,-77.03901,38.79165), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.82117,38.83098,-78.99901,38.84007), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.99901,38.84007,-77.03907,38.84127), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.03907,38.84127,-78.99901,38.84007), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.0391,38.86811,-78.77279,38.89374), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.77279,38.89374,-77.0902,38.90421), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.0902,38.90421,-78.77279,38.89374), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.68162,38.92584,-77.11976,38.93434), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.11976,38.93434,-78.68162,38.92584), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.1466,38.96421,-77.2025,38.96791), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2025,38.96791,-77.1466,38.96421), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.62045,38.9826,-77.2498,38.98591), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2498,38.98591,-78.62045,38.9826), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.56171,39.00901,-77.2484,39.02689), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2484,39.02689,-77.2484,39.02691), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.2484,39.02691,-77.2484,39.02689), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.31071,39.05201,-78.53215,39.05294), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.53215,39.05294,-77.31071,39.05201), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.33004,39.05595,-78.53215,39.05294), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.3597,39.062,-77.33004,39.05595), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.46262,39.07625,-78.50813,39.08863), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.50813,39.08863,-77.46262,39.07625), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.48128,39.10566,-77.51993,39.12093), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.51993,39.12093,-77.82816,39.13233), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.82816,39.13233,-77.8283,39.13242), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.8283,39.13242,-77.82816,39.13233), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.41394,39.15842,-77.52122,39.16106), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.52122,39.16106,-78.41394,39.15842), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.80913,39.16857,-77.52122,39.16106), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.48597,39.18567,-78.4287,39.18722), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.4287,39.18722,-77.48597,39.18567), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.45988,39.21868,-77.46007,39.21884), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.46007,39.21884,-77.45988,39.21868), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.77807,39.22931,-78.40498,39.23801), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.40498,39.23801,-77.77807,39.22931), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.49661,39.25105,-78.40498,39.23801), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.03284,39.2644,-78.03318,39.26462), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.03318,39.26462,-78.03284,39.2644), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.03319,39.26462,-78.03284,39.2644), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.40181,39.27675,-77.55311,39.27927), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.55311,39.27927,-78.40181,39.27675), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.58824,39.30196,-77.66613,39.31701), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.66613,39.31701,-77.6777,39.31794), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.6777,39.31794,-77.66613,39.31701), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-77.71952,39.32131,-77.6777,39.31794), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.34048,39.35349,-78.18737,39.36399), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.18737,39.36399,-78.34048,39.35349), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.22913,39.39066,-78.33713,39.40917), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.33713,39.40917,-78.22913,39.39066), mapfile, tile_dir, 0, 11, "virginia-va")
	render_tiles((-78.34709,39.46601,-78.33713,39.40917), mapfile, tile_dir, 0, 11, "virginia-va")