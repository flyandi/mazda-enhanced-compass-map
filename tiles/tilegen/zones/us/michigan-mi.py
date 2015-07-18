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
    # Region: Michigan
    # Region Name: MI

	render_tiles((-84.39404,45.72762,-84.48413,45.73071), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.48413,45.73071,-84.39404,45.72762), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.35602,45.7719,-84.4197,45.79982), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.4197,45.79982,-84.58757,45.8067), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.58757,45.8067,-84.4197,45.79982), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.5179,45.82854,-84.58757,45.8067), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.65078,45.85921,-84.5179,45.82854), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.61622,45.89447,-84.65078,45.85921), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.61622,45.89447,-84.65078,45.85921), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.56163,45.57221,-85.62274,45.58603), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.62274,45.58603,-85.50928,45.59648), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.50928,45.59648,-85.62274,45.58603), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.69687,45.69725,-85.70181,45.73613), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.70181,45.73613,-85.65187,45.74314), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.70181,45.73613,-85.65187,45.74314), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.65187,45.74314,-85.70181,45.73613), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.37713,45.76901,-85.65187,45.74314), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.36095,45.81755,-85.52445,45.82979), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.52445,45.82979,-85.36095,45.81755), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.08149,44.9901,-86.15482,45.00239), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.15482,45.00239,-86.08149,44.9901), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.1381,45.04304,-85.97688,45.06266), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.1381,45.04304,-85.97688,45.06266), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.97688,45.06266,-86.1381,45.04304), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.95402,45.11928,-85.98941,45.15107), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.98941,45.15107,-86.04443,45.15958), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.04443,45.15958,-85.98941,45.15107), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.80608,41.69609,-84.43807,41.7049), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.43807,41.7049,-84.39955,41.70592), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.39955,41.70592,-84.43807,41.7049), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.36042,41.70696,-84.39955,41.70592), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.13442,41.71293,-84.36042,41.70696), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.88039,41.72019,-83.76315,41.72355), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.76315,41.72355,-83.88039,41.72019), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.76304,41.72355,-83.88039,41.72019), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.58554,41.72877,-83.45383,41.73265), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.45383,41.73265,-83.58554,41.72877), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.42408,41.74074,-83.45383,41.73265), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.79133,41.75905,-85.65975,41.75924), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.79136,41.75905,-85.65975,41.75924), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.65975,41.75924,-85.79133,41.75905), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.50177,41.75955,-86.52422,41.75957), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.52422,41.75957,-86.50177,41.75955), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.06256,41.75965,-86.64004,41.75967), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.64004,41.75967,-86.06256,41.75965), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.64132,41.75967,-86.06256,41.75965), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.29218,41.75976,-85.23284,41.75984), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.23284,41.75984,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.19677,41.75987,-85.23284,41.75984), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.22607,41.76002,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.22609,41.76002,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.82513,41.7602,-84.80588,41.76022), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.80588,41.76022,-84.82513,41.7602), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.82483,41.76024,-84.80588,41.76022), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.44167,41.80865,-86.69327,41.8354), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.69327,41.8354,-83.39622,41.85297), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.69327,41.8354,-83.39622,41.85297), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.39622,41.85297,-86.69327,41.8354), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.34156,41.87996,-83.39622,41.85297), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.5979,41.91829,-83.32602,41.92496), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.32602,41.92496,-86.5979,41.91829), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.26952,41.93904,-83.32602,41.92496), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.2169,41.98856,-83.19491,42.0332), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.19491,42.0332,-83.18553,42.05224), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.18553,42.05224,-83.19491,42.0332), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.50132,42.08454,-83.13351,42.08814), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.13351,42.08814,-86.50132,42.08454), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.46626,42.13441,-83.13392,42.17474), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.13392,42.17474,-86.46626,42.13441), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.11568,42.23102,-86.36638,42.24311), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.36638,42.24311,-86.35622,42.25417), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.35622,42.25417,-86.36638,42.24311), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.09786,42.28601,-83.09652,42.29014), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.09652,42.29014,-83.09786,42.28601), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.98862,42.33244,-82.94967,42.34426), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.94967,42.34426,-82.92397,42.35207), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.92397,42.35207,-82.94967,42.34426), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.28445,42.39456,-86.27699,42.41931), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.27699,42.41931,-86.28445,42.39456), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.87035,42.45089,-82.8703,42.45124), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.8703,42.45124,-82.87035,42.45089), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.67906,42.52221,-86.24064,42.54), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.24064,42.54,-82.85945,42.54085), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.85945,42.54085,-86.24064,42.54), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.85932,42.54194,-82.85945,42.54085), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.584,42.55404,-82.78241,42.56483), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.78241,42.56483,-82.584,42.55404), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.72502,42.58021,-82.70252,42.58624), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.70252,42.58624,-82.72502,42.58021), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.22869,42.62951,-82.50994,42.63729), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.50994,42.63729,-86.22664,42.64492), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.22664,42.64492,-82.50994,42.63729), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.46748,42.76191,-86.20831,42.76279), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.20831,42.76279,-82.46748,42.76191), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.20854,42.76754,-86.20831,42.76279), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.21414,42.88356,-82.46991,42.88746), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.46991,42.88746,-86.21414,42.88356), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.4286,42.952,-86.22631,42.98828), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.22631,42.98828,-82.41594,43.00556), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.41594,43.00556,-86.22631,42.98828), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.25465,43.08341,-82.48604,43.10249), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.48604,43.10249,-86.27393,43.11837), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.27393,43.11837,-82.48604,43.10249), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.50604,43.16883,-86.31626,43.19511), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.31626,43.19511,-82.50604,43.16883), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.52309,43.22536,-86.31626,43.19511), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.40783,43.33844,-82.53993,43.42238), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.53993,43.42238,-86.44874,43.43201), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.44874,43.43201,-82.53993,43.42238), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.46352,43.47233,-86.44874,43.43201), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.47928,43.51534,-86.46352,43.47233), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.59379,43.58147,-83.68335,43.59058), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.68335,43.59058,-86.52951,43.59346), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.52951,43.59346,-83.68335,43.59058), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.69942,43.60164,-86.52951,43.59346), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.73101,43.62337,-86.54079,43.64459), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.54079,43.64459,-83.73101,43.62337), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.90948,43.67262,-83.81789,43.67379), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.81789,43.67379,-83.90948,43.67262), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.60648,43.69044,-86.51032,43.69863), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.51032,43.69863,-82.60648,43.69044), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.52964,43.71924,-83.51234,43.73373), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.51234,43.73373,-83.94774,43.73517), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.94774,43.73517,-83.51234,43.73373), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.61222,43.73977,-83.94774,43.73517), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.50609,43.74516,-82.61222,43.73977), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.44512,43.77156,-83.92938,43.77709), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.92938,43.77709,-86.44512,43.77156), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.47957,43.79363,-83.92938,43.77709), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.43549,43.81943,-82.63364,43.83122), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.63364,43.83122,-86.4312,43.84072), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.4312,43.84072,-82.63364,43.83122), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.43261,43.88527,-83.91061,43.89322), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.91061,43.89322,-83.43261,43.88527), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.90133,43.90843,-86.44792,43.91809), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.44792,43.91809,-83.40715,43.91981), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.40715,43.91981,-86.44792,43.91809), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.28231,43.93803,-82.70984,43.94823), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.70984,43.94823,-83.28231,43.93803), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.86941,43.96072,-86.46314,43.97098), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.46314,43.97098,-86.4632,43.97106), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.4632,43.97106,-86.46314,43.97098), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.26153,43.97353,-86.4632,43.97106), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.78786,43.98528,-83.69321,43.98877), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.69321,43.98877,-83.78786,43.98528), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.13488,43.99315,-83.69321,43.98877), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.04658,44.01571,-86.50174,44.02191), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.50174,44.02191,-82.79321,44.02325), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.79321,44.02325,-86.50174,44.02191), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.67965,44.03637,-83.0246,44.04517), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.0246,44.04517,-83.67965,44.03637), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.58409,44.05675,-86.5147,44.05812), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.5147,44.05812,-83.58409,44.05675), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-82.92888,44.06939,-86.5147,44.05812), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.45807,44.09929,-86.42987,44.11978), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.42987,44.11978,-86.45807,44.09929), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.56774,44.1559,-83.56465,44.16352), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.56465,44.16352,-83.56774,44.1559), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.3914,44.1737,-86.38784,44.17869), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.38784,44.17869,-86.3914,44.1737), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.35164,44.22943,-83.52482,44.26156), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.52482,44.26156,-83.44273,44.26536), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.44273,44.26536,-83.52482,44.26156), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.40182,44.30183,-83.33699,44.33292), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.33699,44.33292,-86.26871,44.34532), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.26871,44.34532,-83.33699,44.33292), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.25193,44.40098,-86.26871,44.34532), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.24891,44.483,-83.31761,44.48606), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.31761,44.48606,-86.24891,44.483), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.31696,44.51173,-86.23702,44.5183), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.23702,44.5183,-83.31696,44.51173), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.2207,44.56674,-83.31452,44.60873), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.31452,44.60873,-86.25395,44.64808), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.25395,44.64808,-83.31452,44.60873), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.27684,44.68935,-86.24847,44.69905), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.24847,44.69905,-83.27684,44.68935), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.16027,44.72819,-86.08919,44.7415), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.08919,44.7415,-86.16027,44.72819), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.29697,44.7585,-86.08919,44.7415), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.07847,44.77842,-83.29697,44.7585), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.06597,44.82152,-83.31627,44.85859), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.31627,44.85859,-83.3205,44.88057), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.3205,44.88057,-83.35282,44.88616), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.35282,44.88616,-83.3205,44.88057), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.98022,44.90614,-86.05886,44.91101), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.05886,44.91101,-85.98022,44.90614), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.8543,44.93815,-83.43886,44.94084), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.43886,44.94084,-85.8543,44.93815), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.9316,44.96879,-85.52003,44.974), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.52003,44.974,-85.78044,44.97793), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.78044,44.97793,-85.52003,44.974), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.4752,44.99105,-83.43582,45.00001), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.43582,45.00001,-85.4752,44.99105), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.43142,45.01665,-83.2659,45.02684), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.2659,45.02684,-85.55514,45.02703), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.55514,45.02703,-83.2659,45.02684), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.34026,45.04155,-85.56613,45.04363), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.56613,45.04363,-83.34026,45.04155), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.38066,45.04632,-85.56613,45.04363), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.44205,45.05106,-85.74644,45.05123), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.74644,45.05123,-83.44205,45.05106), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.39926,45.07036,-85.74644,45.05123), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.6811,45.09269,-85.36675,45.10159), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.36675,45.10159,-85.6811,45.09269), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.31592,45.13999,-85.63312,45.1709), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.63312,45.1709,-85.53146,45.17725), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.53146,45.17725,-85.38046,45.18088), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.38046,45.18088,-85.53146,45.17725), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.38521,45.2071,-85.37783,45.20759), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.37783,45.20759,-83.38521,45.2071), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.55107,45.21074,-85.37783,45.20759), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.40591,45.22716,-85.55107,45.21074), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.37159,45.27083,-83.3851,45.2742), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.3851,45.2742,-85.37159,45.27083), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.29485,45.31641,-83.59927,45.35256), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.59927,45.35256,-83.48883,45.35587), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.48883,45.35587,-83.59927,45.35256), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.1967,45.36064,-85.09606,45.36309), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.09606,45.36309,-85.05481,45.36409), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.05481,45.36409,-85.09606,45.36309), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.95912,45.37597,-85.05481,45.36409), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.69732,45.39624,-84.91296,45.40978), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.91296,45.40978,-83.69732,45.39624), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.98095,45.42938,-83.84154,45.43529), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.84154,45.43529,-85.04094,45.4367), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.04094,45.4367,-83.84154,45.43529), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.90947,45.48578,-83.99835,45.49116), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.99835,45.49116,-83.90947,45.48578), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.09591,45.4973,-83.99835,45.49116), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.10925,45.52163,-84.09591,45.4973), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.12653,45.55662,-85.11974,45.56903), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.11974,45.56903,-84.12653,45.55662), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.19604,45.62146,-84.21089,45.62623), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.21089,45.62623,-84.19604,45.62146), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.06149,45.63951,-84.46168,45.6524), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.46168,45.6524,-84.32954,45.66438), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.32954,45.66438,-84.41364,45.66943), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.41364,45.66943,-84.32954,45.66438), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.97095,45.68633,-84.55331,45.69857), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.55331,45.69857,-84.97095,45.68633), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.86698,45.75207,-85.01451,45.76033), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.01451,45.76033,-84.86698,45.75207), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.7189,45.7776,-84.73224,45.7805), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.73224,45.7805,-84.7189,45.7776), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.77277,45.7893,-84.73224,45.7805), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.15774,47.82402,-89.20181,47.85024), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.20181,47.85024,-89.04446,47.85575), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.04446,47.85575,-89.20181,47.85024), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.2552,47.8761,-89.04446,47.85575), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.89899,47.90069,-89.22133,47.90807), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.22133,47.90807,-88.89899,47.90069), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.22133,47.90807,-88.89899,47.90069), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.17915,47.93503,-89.22133,47.90807), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.85292,47.96532,-89.0183,47.99253), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.0183,47.99253,-88.71856,47.99513), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.71856,47.99513,-89.0183,47.99253), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.94089,48.01959,-88.8937,48.03477), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.8937,48.03477,-88.57917,48.04076), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.57917,48.04076,-88.8937,48.03477), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.81608,48.05701,-88.57917,48.04076), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.7282,48.10191,-88.55044,48.10211), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.55044,48.10211,-88.7282,48.10191), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.63191,48.14831,-88.42737,48.16676), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.42737,48.16676,-88.54703,48.17489), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.54703,48.17489,-88.42737,48.16676), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.42516,48.21065,-88.54703,48.17489), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.59021,45.09526,-87.64819,45.10637), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.64819,45.10637,-87.59021,45.09526), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.69506,45.15052,-87.54896,45.19159), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.54896,45.19159,-87.74181,45.19705), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.74181,45.19705,-87.54896,45.19159), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.71148,45.24522,-87.4652,45.27335), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.4652,45.27335,-87.71148,45.24522), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.66742,45.31636,-87.86349,45.35302), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.86349,45.35302,-87.80046,45.35361), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.80046,45.35361,-87.86349,45.35302), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.75093,45.35504,-87.80046,45.35361), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.65735,45.36875,-87.75093,45.35504), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.70677,45.38383,-87.85683,45.39311), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.85683,45.39311,-87.70677,45.38383), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.35085,45.40774,-87.85683,45.39311), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.84743,45.44418,-87.80577,45.47314), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.80577,45.47314,-87.28873,45.50161), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.28873,45.50161,-87.8042,45.52468), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.8042,45.52468,-86.6369,45.54205), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.6369,45.54205,-87.25345,45.55012), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.25345,45.55012,-86.6369,45.54205), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.78729,45.57491,-87.25345,45.55012), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.77767,45.6092,-86.71233,45.61094), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.71233,45.61094,-87.77767,45.6092), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.61697,45.62058,-86.71233,45.61094), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.82468,45.65321,-87.17224,45.66179), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.17224,45.66179,-87.82468,45.65321), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.96428,45.67276,-87.78101,45.67393), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.78101,45.67393,-86.96428,45.67276), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.70518,45.6909,-87.80508,45.70356), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.80508,45.70356,-86.54143,45.70811), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.54143,45.70811,-87.80508,45.70356), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.07044,45.71878,-86.83875,45.72231), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.83875,45.72231,-87.83305,45.72275), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.83305,45.72275,-86.83875,45.72231), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.64732,45.73262,-87.83305,45.72275), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.45988,45.75023,-87.87981,45.75484), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.87981,45.75484,-86.45988,45.75023), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.43966,45.76067,-87.96697,45.76402), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.96697,45.76402,-86.43966,45.76067), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.04851,45.78255,-88.05701,45.78498), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.05701,45.78498,-88.04851,45.78255), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.99588,45.79544,-88.10552,45.79884), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.10552,45.79884,-87.99588,45.79544), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.77328,45.81139,-88.13507,45.82169), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.13507,45.82169,-86.77328,45.81139), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.34913,45.83416,-88.13507,45.82169), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.70638,45.84866,-84.79276,45.85869), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.79276,45.85869,-84.70638,45.84866), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.07394,45.87559,-84.79276,45.85869), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.734,45.90703,-83.58305,45.91592), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.58305,45.91592,-83.52635,45.91864), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.52635,45.91864,-85.91377,45.91944), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.91377,45.91944,-83.52635,45.91864), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.11535,45.92221,-88.11686,45.92281), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.11686,45.92281,-88.11535,45.92221), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.91748,45.93067,-84.37643,45.93196), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.37643,45.93196,-84.91748,45.93067), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.80104,45.93758,-86.27801,45.94206), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.27801,45.94206,-83.65766,45.94546), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.65766,45.94546,-88.17801,45.94711), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.17801,45.94711,-85.86584,45.94757), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.86584,45.94757,-84.56749,45.9477), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.56749,45.9477,-85.86584,45.94757), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.63286,45.95101,-84.56749,45.9477), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.25495,45.95607,-88.30952,45.95937), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.30952,45.95937,-85.6972,45.96016), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.6972,45.96016,-88.30952,45.95937), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.24631,45.96298,-86.07207,45.96531), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.07207,45.96531,-83.91084,45.96561), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.91084,45.96561,-86.07207,45.96531), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.11461,45.96791,-83.91084,45.96561), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.08007,45.97082,-84.11461,45.96791), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.48044,45.97776,-88.40986,45.97969), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.40986,45.97969,-85.81044,45.98009), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.81044,45.98009,-88.40986,45.97969), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.64858,45.9837,-85.81044,45.98009), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.65776,45.98929,-88.61306,45.99063), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.61306,45.99063,-88.38018,45.99165), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.38018,45.99165,-88.61306,45.99063), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.48064,45.99616,-88.38018,45.99165), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.0036,46.00613,-88.67913,46.01354), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.67913,46.01354,-88.68323,46.01447), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.68323,46.01447,-88.59386,46.01513), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.59386,46.01513,-88.68323,46.01447), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.52667,46.02082,-88.81195,46.02161), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.81195,46.02161,-88.52667,46.02082), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.73999,46.02731,-88.81195,46.02161), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.8823,46.04207,-85.15203,46.05073), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.15203,46.05073,-83.8823,46.04207), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.26639,46.06578,-88.93277,46.07211), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.93277,46.07211,-85.26639,46.06578), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.54086,46.07958,-83.97401,46.08155), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.97401,46.08155,-85.38139,46.08204), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.38139,46.08204,-83.97401,46.08155), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.59861,46.09009,-88.99122,46.09654), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.99122,46.09654,-83.71979,46.10103), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.71979,46.10103,-88.99122,46.09654), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-83.81583,46.10853,-83.71979,46.10103), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.02654,46.13165,-89.09163,46.13851), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.09163,46.13851,-84.02654,46.13165), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.11494,46.17411,-89.09163,46.13851), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.10809,46.24124,-89.63842,46.2438), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.63842,46.2438,-84.10809,46.24124), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.09777,46.25651,-89.63842,46.2438), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.92913,46.29992,-90.12049,46.33685), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.12049,46.33685,-84.13891,46.37222), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.13891,46.37222,-90.12049,46.33685), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.15824,46.42049,-84.4934,46.44031), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.4934,46.44031,-86.81097,46.44966), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.81097,46.44966,-84.60795,46.45675), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.60795,46.45675,-84.84977,46.46025), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.84977,46.46025,-84.60795,46.45675), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.90374,46.46614,-84.46183,46.46657), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.46183,46.46657,-86.90374,46.46614), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.12503,46.47014,-84.46183,46.46657), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.96946,46.47629,-86.75016,46.47911), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.75016,46.47911,-84.96946,46.47629), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.55773,46.48743,-84.67842,46.48769), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.67842,46.48769,-86.55773,46.48743), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.29302,46.4928,-87.17507,46.49755), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.17507,46.49755,-90.21487,46.49995), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.21487,46.49995,-84.42027,46.50108), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.42027,46.50108,-90.21487,46.49995), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.11636,46.50615,-87.36677,46.5073), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.36677,46.5073,-87.11636,46.50615), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.11793,46.51762,-90.28571,46.51885), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.28571,46.51885,-84.11793,46.51762), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.97696,46.52658,-90.38723,46.53366), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.38723,46.53366,-86.62738,46.53371), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.62738,46.53371,-90.38723,46.53366), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.19373,46.53992,-86.62738,46.53371), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.45993,46.55193,-90.33189,46.55328), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.33189,46.55328,-85.02737,46.55376), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.02737,46.55376,-90.33189,46.55328), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.69565,46.55503,-85.02737,46.55376), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.41814,46.56609,-86.69565,46.55503), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.41814,46.56609,-86.69565,46.55503), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.38165,46.58006,-90.41814,46.56609), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.32763,46.60774,-90.23761,46.62449), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.23761,46.62449,-90.32763,46.60774), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.50303,46.6475,-86.18802,46.65401), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.18802,46.65401,-87.50303,46.6475), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-90.04542,46.66827,-86.1383,46.67294), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-86.1383,46.67294,-85.99504,46.67368), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.99504,46.67368,-86.1383,46.67294), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.02829,46.67513,-85.99504,46.67368), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.4821,46.68043,-85.02829,46.67513), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.86461,46.68657,-85.84106,46.6889), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.84106,46.6889,-85.86461,46.68657), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.5732,46.72047,-89.91847,46.74032), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.91847,46.74032,-85.25686,46.75338), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.25686,46.75338,-85.23787,46.7557), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.23787,46.7557,-85.25686,46.75338), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-85.17304,46.76363,-89.88387,46.76581), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.88387,46.76581,-85.17304,46.76363), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-84.96465,46.77285,-89.88387,46.76581), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.59531,46.78295,-84.96465,46.77285), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.83196,46.80405,-87.59531,46.78295), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.64226,46.82534,-89.72028,46.83041), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.72028,46.83041,-89.56981,46.83186), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.56981,46.83186,-89.72028,46.83041), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.49908,46.84162,-87.68716,46.84174), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.68716,46.84174,-89.49908,46.84162), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.41515,46.84398,-87.68716,46.84174), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.47794,46.85056,-89.41515,46.84398), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.37268,46.87228,-87.77693,46.87673), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.77693,46.87673,-88.37268,46.87228), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.90034,46.90969,-89.22791,46.91295), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.22791,46.91295,-87.90034,46.90969), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.04452,46.91745,-88.06519,46.91856), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.06519,46.91856,-88.04452,46.91745), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.4554,46.92332,-88.06519,46.91856), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.24444,46.92961,-88.4554,46.92332), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.43936,46.94198,-88.24444,46.92961), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.14369,46.96667,-89.1426,46.98486), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.1426,46.98486,-89.02893,47.00114), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-89.02893,47.00114,-88.38561,47.00452), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.38561,47.00452,-89.02893,47.00114), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.95941,47.0085,-88.38561,47.00452), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.93337,47.0336,-88.92449,47.04216), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.92449,47.04216,-88.93337,47.0336), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.34005,47.08049,-88.88914,47.10058), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.88914,47.10058,-88.34005,47.08049), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.2399,47.13944,-88.81483,47.1414), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.81483,47.1414,-88.2399,47.13944), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.20045,47.19972,-88.69966,47.20483), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.69966,47.20483,-88.19422,47.20924), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.19422,47.20924,-88.69966,47.20483), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.58491,47.24236,-88.09685,47.26135), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.09685,47.26135,-88.58491,47.24236), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.51295,47.28611,-88.50078,47.2935), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.50078,47.2935,-88.51295,47.28611), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.01648,47.30628,-88.50078,47.2935), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.94336,47.3359,-88.01648,47.30628), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.41867,47.37119,-87.6047,47.38863), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.6047,47.38863,-87.94161,47.39007), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.94161,47.39007,-87.6047,47.38863), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.80029,47.39215,-87.94161,47.39007), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.2852,47.42239,-87.5915,47.42411), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.5915,47.42411,-88.2852,47.42239), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.21782,47.44874,-87.68007,47.45569), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.68007,47.45569,-88.21782,47.44874), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-88.08525,47.46896,-87.80118,47.4733), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.80118,47.4733,-88.08525,47.46896), mapfile, tile_dir, 0, 11, "michigan-mi")
	render_tiles((-87.92927,47.47874,-87.80118,47.4733), mapfile, tile_dir, 0, 11, "michigan-mi")