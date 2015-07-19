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
    # Region: AE
    # Region Name: United Arab Emirates

	render_tiles((55.50226,22.05716,55.50226,25.51944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.50226,22.05716,55.50226,25.51944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.50226,22.05716,55.50226,25.51944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.50226,22.05716,55.50226,25.51944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.69401,22.28859,55.50226,25.58916), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.69401,22.28859,55.50226,25.58916), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.69401,22.28859,55.50226,25.58916), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.69401,22.28859,55.50226,25.58916), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.09041,22.52001,56.15887,23.95555), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.09041,22.52001,56.15887,23.95555), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.09041,22.52001,56.15887,23.95555), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.09041,22.52001,56.15887,23.95555), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.49532,23.60439,55.50226,24.57194), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.49532,23.60439,55.50226,24.57194), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.49532,23.60439,55.50226,24.57194), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.49532,23.60439,55.50226,24.57194), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.69401,23.79615,55.50226,25.58916), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.69401,23.79615,55.50226,25.58916), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.69401,23.79615,55.50226,25.58916), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.69401,23.79615,55.50226,25.58916), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.08054,23.95555,56.15887,22.52001), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.08054,23.95555,56.15887,22.52001), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.08054,23.95555,56.15887,22.52001), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.08054,23.95555,56.15887,22.52001), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.25443,23.98,56.15887,22.52001), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.25443,23.98,56.15887,22.52001), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.25443,23.98,56.15887,22.52001), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.25443,23.98,56.15887,22.52001), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.85416,23.98805,56.15887,24.0025), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.85416,23.98805,56.15887,24.0025), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.85416,23.98805,56.15887,24.0025), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.85416,23.98805,56.15887,24.0025), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.95693,23.99361,56.15887,23.98805), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.95693,23.99361,56.15887,23.98805), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.95693,23.99361,56.15887,23.98805), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.95693,23.99361,56.15887,23.98805), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.80777,24.0025,55.50226,24.26667), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.80777,24.0025,55.50226,24.26667), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.80777,24.0025,55.50226,24.26667), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.80777,24.0025,55.50226,24.26667), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.76193,24.05583,55.50226,24.08638), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.76193,24.05583,55.50226,24.08638), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.76193,24.05583,55.50226,24.08638), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.76193,24.05583,55.50226,24.08638), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.49999,24.08638,55.50226,24.10222), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.49999,24.08638,55.50226,24.10222), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.49999,24.08638,55.50226,24.10222), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.49999,24.08638,55.50226,24.10222), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.48916,24.09083,55.50226,24.19444), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.48916,24.09083,55.50226,24.19444), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.48916,24.09083,55.50226,24.19444), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.48916,24.09083,55.50226,24.19444), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.25526,24.09666,55.50226,24.10222), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.25526,24.09666,55.50226,24.10222), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.25526,24.09666,55.50226,24.10222), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.25526,24.09666,55.50226,24.10222), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.34055,24.10222,55.50226,24.09666), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.34055,24.10222,55.50226,24.09666), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.34055,24.10222,55.50226,24.09666), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.34055,24.10222,55.50226,24.09666), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.16193,24.12305,55.50226,24.16639), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.16193,24.12305,55.50226,24.16639), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.16193,24.12305,55.50226,24.16639), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.16193,24.12305,55.50226,24.16639), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.68193,24.13639,55.50226,24.19638), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.68193,24.13639,55.50226,24.19638), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.68193,24.13639,55.50226,24.19638), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.68193,24.13639,55.50226,24.19638), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.7711,24.14055,55.50226,24.26667), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.7711,24.14055,55.50226,24.26667), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.7711,24.14055,55.50226,24.26667), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.7711,24.14055,55.50226,24.26667), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.12888,24.14388,55.50226,24.32861), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.12888,24.14388,55.50226,24.32861), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.12888,24.14388,55.50226,24.32861), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.12888,24.14388,55.50226,24.32861), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.14582,24.16166,55.50226,24.12305), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.14582,24.16166,55.50226,24.12305), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.14582,24.16166,55.50226,24.12305), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.14582,24.16166,55.50226,24.12305), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((53.16527,24.16639,55.50226,24.12305), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((53.16527,24.16639,55.50226,24.12305), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((53.16527,24.16639,55.50226,24.12305), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((53.16527,24.16639,55.50226,24.12305), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.58166,24.19444,55.50226,24.19638), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.58166,24.19444,55.50226,24.19638), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.58166,24.19444,55.50226,24.19638), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.58166,24.19444,55.50226,24.19638), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((52.63055,24.19638,55.50226,24.19444), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((52.63055,24.19638,55.50226,24.19444), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((52.63055,24.19638,55.50226,24.19444), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((52.63055,24.19638,55.50226,24.19444), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.19305,24.20083,55.50226,24.23888), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.19305,24.20083,55.50226,24.23888), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.19305,24.20083,55.50226,24.23888), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.19305,24.20083,55.50226,24.23888), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.02462,24.2061,55.50226,26.06644), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.02462,24.2061,55.50226,26.06644), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.02462,24.2061,55.50226,26.06644), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.02462,24.2061,55.50226,26.06644), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.63554,24.20972,55.50226,24.34472), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.63554,24.20972,55.50226,24.34472), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.63554,24.20972,55.50226,24.34472), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.63554,24.20972,55.50226,24.34472), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.24582,24.21777,55.50226,24.24944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.24582,24.21777,55.50226,24.24944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.24582,24.21777,55.50226,24.24944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.24582,24.21777,55.50226,24.24944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.72083,24.21777,55.50226,24.28639), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.72083,24.21777,55.50226,24.28639), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.72083,24.21777,55.50226,24.28639), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.72083,24.21777,55.50226,24.28639), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.19471,24.23888,55.50226,24.20083), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.19471,24.23888,55.50226,24.20083), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.19471,24.23888,55.50226,24.20083), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.19471,24.23888,55.50226,24.20083), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.29527,24.24944,55.50226,24.21777), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.29527,24.24944,55.50226,24.21777), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.29527,24.24944,55.50226,24.21777), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.29527,24.24944,55.50226,24.21777), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.54332,24.25194,55.50226,24.26777), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.54332,24.25194,55.50226,24.26777), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.54332,24.25194,55.50226,24.26777), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.54332,24.25194,55.50226,24.26777), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.38194,24.25222,55.50226,24.24944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.38194,24.25222,55.50226,24.24944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.38194,24.25222,55.50226,24.24944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.38194,24.25222,55.50226,24.24944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((50.84734,24.25238,55.50226,24.75386), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((50.84734,24.25238,55.50226,24.75386), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((50.84734,24.25238,55.50226,24.75386), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((50.84734,24.25238,55.50226,24.75386), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.78082,24.26667,55.50226,24.14055), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.78082,24.26667,55.50226,24.14055), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.78082,24.26667,55.50226,24.14055), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.78082,24.26667,55.50226,24.14055), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.59026,24.26777,55.50226,24.37833), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.59026,24.26777,55.50226,24.37833), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.59026,24.26777,55.50226,24.37833), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.59026,24.26777,55.50226,24.37833), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.0811,24.2725,55.50226,24.30055), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.0811,24.2725,55.50226,24.30055), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.0811,24.2725,55.50226,24.30055), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.0811,24.2725,55.50226,24.30055), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.74388,24.28639,55.50226,24.21777), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.74388,24.28639,55.50226,24.21777), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.74388,24.28639,55.50226,24.21777), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.74388,24.28639,55.50226,24.21777), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.08665,24.30055,55.50226,24.2725), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.08665,24.30055,55.50226,24.2725), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.08665,24.30055,55.50226,24.2725), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.08665,24.30055,55.50226,24.2725), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.47943,24.30861,55.50226,24.57194), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.47943,24.30861,55.50226,24.57194), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.47943,24.30861,55.50226,24.57194), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.47943,24.30861,55.50226,24.57194), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.15193,24.32861,55.50226,24.14388), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.15193,24.32861,55.50226,24.14388), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.15193,24.32861,55.50226,24.14388), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.15193,24.32861,55.50226,24.14388), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.29137,24.33999,55.50226,24.50999), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.29137,24.33999,55.50226,24.50999), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.29137,24.33999,55.50226,24.50999), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.29137,24.33999,55.50226,24.50999), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.62221,24.34472,55.50226,24.20972), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.62221,24.34472,55.50226,24.20972), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.62221,24.34472,55.50226,24.20972), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.62221,24.34472,55.50226,24.20972), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.59138,24.37833,55.50226,24.26777), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.59138,24.37833,55.50226,24.26777), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.59138,24.37833,55.50226,24.26777), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.59138,24.37833,55.50226,24.26777), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.31416,24.41944,55.50226,24.50999), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.31416,24.41944,55.50226,24.50999), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.31416,24.41944,55.50226,24.50999), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.31416,24.41944,55.50226,24.50999), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.49443,24.4361,55.50226,24.50972), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.49443,24.4361,55.50226,24.50972), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.49443,24.4361,55.50226,24.50972), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.49443,24.4361,55.50226,24.50972), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.57027,24.43805,55.50226,24.46333), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.57027,24.43805,55.50226,24.46333), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.57027,24.43805,55.50226,24.46333), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.57027,24.43805,55.50226,24.46333), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.58388,24.46333,55.50226,24.43805), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.58388,24.46333,55.50226,24.43805), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.58388,24.46333,55.50226,24.43805), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.58388,24.46333,55.50226,24.43805), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.52666,24.50972,55.50226,24.4361), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.52666,24.50972,55.50226,24.4361), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.52666,24.50972,55.50226,24.4361), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.52666,24.50972,55.50226,24.4361), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.30499,24.50999,55.50226,24.41944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.30499,24.50999,55.50226,24.41944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.30499,24.50999,55.50226,24.41944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.30499,24.50999,55.50226,24.41944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.59805,24.52388,55.50226,24.46333), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.59805,24.52388,55.50226,24.46333), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.59805,24.52388,55.50226,24.46333), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.59805,24.52388,55.50226,24.46333), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.34249,24.53749,55.50226,24.41944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.34249,24.53749,55.50226,24.41944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.34249,24.53749,55.50226,24.41944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.34249,24.53749,55.50226,24.41944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.10862,24.55559,55.50226,24.61903), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.10862,24.55559,55.50226,24.61903), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.10862,24.55559,55.50226,24.61903), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.10862,24.55559,55.50226,24.61903), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.49277,24.57194,56.15887,23.60439), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.49277,24.57194,56.15887,23.60439), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.49277,24.57194,56.15887,23.60439), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.49277,24.57194,56.15887,23.60439), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((50.96943,24.57777,55.50226,24.25238), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((50.96943,24.57777,55.50226,24.25238), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((50.96943,24.57777,55.50226,24.25238), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((50.96943,24.57777,55.50226,24.25238), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.27554,24.61638,55.50226,24.33999), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.27554,24.61638,55.50226,24.33999), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.27554,24.61638,55.50226,24.33999), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.27554,24.61638,55.50226,24.33999), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.39639,24.61825,55.50226,24.53749), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.39639,24.61825,55.50226,24.53749), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.39639,24.61825,55.50226,24.53749), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.39639,24.61825,55.50226,24.53749), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((51.21603,24.61903,55.50226,24.61638), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((51.21603,24.61903,55.50226,24.61638), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((51.21603,24.61903,55.50226,24.61638), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((51.21603,24.61903,55.50226,24.61638), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.64582,24.62888,55.50226,24.70888), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.64582,24.62888,55.50226,24.70888), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.64582,24.62888,55.50226,24.70888), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.64582,24.62888,55.50226,24.70888), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.65305,24.70888,55.50226,24.62888), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.65305,24.70888,55.50226,24.62888), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.65305,24.70888,55.50226,24.62888), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.65305,24.70888,55.50226,24.62888), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((50.8261,24.74751,55.50226,24.75386), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((50.8261,24.74751,55.50226,24.75386), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((50.8261,24.74751,55.50226,24.75386), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((50.8261,24.74751,55.50226,24.75386), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((50.82764,24.75386,55.50226,24.74751), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((50.82764,24.75386,55.50226,24.74751), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((50.82764,24.75386,55.50226,24.74751), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((50.82764,24.75386,55.50226,24.74751), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.76749,24.78944,55.50226,24.83194), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.76749,24.78944,55.50226,24.83194), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.76749,24.78944,55.50226,24.83194), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.76749,24.78944,55.50226,24.83194), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.70776,24.80138,55.50226,24.70888), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.70776,24.80138,55.50226,24.70888), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.70776,24.80138,55.50226,24.70888), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.70776,24.80138,55.50226,24.70888), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((54.76499,24.83194,55.50226,24.78944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((54.76499,24.83194,55.50226,24.78944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((54.76499,24.83194,55.50226,24.78944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((54.76499,24.83194,55.50226,24.78944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.37582,24.96444,55.50226,25.51305), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.37582,24.96444,55.50226,25.51305), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.37582,24.96444,55.50226,25.51305), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.37582,24.96444,55.50226,25.51305), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.01693,24.98305,55.50226,24.78944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.01693,24.98305,55.50226,24.78944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.01693,24.98305,55.50226,24.78944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.01693,24.98305,55.50226,24.78944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.02462,25.03922,55.50226,26.06644), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.02462,25.03922,55.50226,26.06644), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.02462,25.03922,55.50226,26.06644), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.02462,25.03922,55.50226,26.06644), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.36471,25.21138,55.50226,25.51305), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.36471,25.21138,55.50226,25.51305), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.36471,25.21138,55.50226,25.51305), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.36471,25.21138,55.50226,25.51305), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.32971,25.24722,55.50226,25.30249), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.32971,25.24722,55.50226,25.30249), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.32971,25.24722,55.50226,25.30249), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.32971,25.24722,55.50226,25.30249), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.27915,25.2575,55.50226,25.2725), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.27915,25.2575,55.50226,25.2725), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.27915,25.2575,55.50226,25.2725), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.27915,25.2575,55.50226,25.2725), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.29665,25.2725,55.50226,25.2575), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.29665,25.2725,55.50226,25.2575), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.29665,25.2725,55.50226,25.2575), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.29665,25.2725,55.50226,25.2575), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.32471,25.30249,55.50226,25.24722), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.32471,25.30249,55.50226,25.24722), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.32471,25.30249,55.50226,25.24722), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.32471,25.30249,55.50226,25.24722), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.36916,25.51305,55.50226,25.21138), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.36916,25.51305,55.50226,25.21138), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.36916,25.51305,55.50226,25.21138), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.36916,25.51305,55.50226,25.21138), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.54999,25.51944,55.50226,25.58166), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.54999,25.51944,55.50226,25.58166), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.54999,25.51944,55.50226,25.58166), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.54999,25.51944,55.50226,25.58166), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.62832,25.53278,55.50226,25.58916), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.62832,25.53278,55.50226,25.58916), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.62832,25.53278,55.50226,25.58916), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.62832,25.53278,55.50226,25.58916), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.55971,25.58166,55.50226,25.51944), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.55971,25.58166,55.50226,25.51944), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.55971,25.58166,55.50226,25.51944), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.55971,25.58166,55.50226,25.51944), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.65249,25.58916,55.50226,25.53278), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.65249,25.58916,55.50226,25.53278), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.65249,25.58916,55.50226,25.53278), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.65249,25.58916,55.50226,25.53278), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.34583,25.59694,55.50226,25.21138), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.34583,25.59694,55.50226,25.21138), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.34583,25.59694,55.50226,25.21138), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.34583,25.59694,55.50226,25.21138), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.21471,25.61527,55.50226,26.01472), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.21471,25.61527,55.50226,26.01472), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.21471,25.61527,55.50226,26.01472), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.21471,25.61527,55.50226,26.01472), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.2719,25.6349,55.50226,25.61527), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.2719,25.6349,55.50226,25.61527), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.2719,25.6349,55.50226,25.61527), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.2719,25.6349,55.50226,25.61527), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.1436,25.6761,55.50226,26.0836), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.1436,25.6761,55.50226,26.0836), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.1436,25.6761,55.50226,26.0836), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.1436,25.6761,55.50226,26.0836), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.80582,25.69611,56.15887,22.28859), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.80582,25.69611,56.15887,22.28859), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.80582,25.69611,56.15887,22.28859), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.80582,25.69611,56.15887,22.28859), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.94777,25.76555,55.50226,25.82305), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.94777,25.76555,55.50226,25.82305), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.94777,25.76555,55.50226,25.82305), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.94777,25.76555,55.50226,25.82305), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((55.95471,25.82305,55.50226,25.76555), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((55.95471,25.82305,55.50226,25.76555), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((55.95471,25.82305,55.50226,25.76555), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((55.95471,25.82305,55.50226,25.76555), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.18554,26.01472,55.50226,26.0836), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.18554,26.01472,55.50226,26.0836), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.18554,26.01472,55.50226,26.0836), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.18554,26.01472,55.50226,26.0836), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.08121,26.06644,55.50226,24.2061), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")
	render_tiles((56.15887,26.0836,55.50226,25.6761), mapfile, tile_dir, 0, 11, "ae-united-arab-emirates")
	render_tiles((56.15887,26.0836,55.50226,25.6761), mapfile, tile_dir, 13, 13, "ae-united-arab-emirates")
	render_tiles((56.15887,26.0836,55.50226,25.6761), mapfile, tile_dir, 15, 15, "ae-united-arab-emirates")
	render_tiles((56.15887,26.0836,55.50226,25.6761), mapfile, tile_dir, 17, 17, "ae-united-arab-emirates")