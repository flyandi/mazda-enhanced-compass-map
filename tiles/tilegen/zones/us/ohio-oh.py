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
    # Region: Ohio
    # Region Name: OH

	render_tiles((-82.8421,41.62832,-82.78888,41.64305), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.78888,41.64305,-82.8421,41.62832), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.86334,41.69369,-82.78272,41.694), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.86334,41.69369,-82.78272,41.694), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.78272,41.694,-82.86334,41.69369), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.82572,41.72281,-82.78272,41.694), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.56066,38.40434,-82.50897,38.41464), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.50897,38.41464,-82.59367,38.42181), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.59367,38.42181,-82.44708,38.42698), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.44708,38.42698,-82.59367,38.42181), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.38177,38.43478,-82.44708,38.42698), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.324,38.44927,-82.38177,38.43478), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.61847,38.47709,-82.30422,38.49631), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.30422,38.49631,-82.66412,38.50772), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.66412,38.50772,-82.67572,38.5155), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.67572,38.5155,-82.66412,38.50772), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.72485,38.5576,-82.29327,38.56028), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.29327,38.56028,-82.72485,38.5576), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.80011,38.56318,-82.29327,38.56028), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.81154,38.57237,-82.28213,38.57986), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.28213,38.57986,-82.81154,38.57237), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.21897,38.59168,-82.27427,38.59368), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.27427,38.59368,-82.21897,38.59168), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.28651,38.59924,-82.85131,38.60433), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.85131,38.60433,-82.17517,38.60848), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.17517,38.60848,-82.85131,38.60433), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.2643,38.61311,-82.17517,38.60848), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.17265,38.62025,-83.32053,38.62271), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.32053,38.62271,-83.17265,38.62025), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.23952,38.62859,-83.67948,38.63004), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.67948,38.63004,-83.23952,38.62859), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.70586,38.63804,-83.12897,38.64023), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.12897,38.64023,-83.64691,38.64185), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.64691,38.64185,-83.64299,38.64327), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.64299,38.64327,-83.64691,38.64185), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.77216,38.65815,-82.18557,38.65958), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.18557,38.65958,-83.77216,38.65815), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.3763,38.66147,-82.18557,38.65958), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.4404,38.66936,-83.11237,38.67169), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.11237,38.67169,-83.4404,38.66936), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.86959,38.67818,-83.62692,38.67939), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.62692,38.67939,-82.86959,38.67818), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.78362,38.69564,-83.53334,38.70211), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.53334,38.70211,-83.04234,38.70832), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.04234,38.70832,-83.53334,38.70211), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.83402,38.71601,-83.03033,38.71687), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.03033,38.71687,-83.83402,38.71601), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.87119,38.71838,-83.03033,38.71687), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.01182,38.73006,-82.88229,38.74162), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.88229,38.74162,-82.94315,38.74328), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.94315,38.74328,-82.88229,38.74162), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.85209,38.75143,-82.88919,38.75608), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.88919,38.75608,-82.20154,38.76037), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.20154,38.76037,-82.88919,38.75608), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.90438,38.76728,-84.05164,38.7714), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.05164,38.7714,-84.05265,38.77161), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.05265,38.77161,-84.05164,38.7714), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.92845,38.77458,-84.05265,38.77161), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.97881,38.7871,-84.13509,38.78949), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.13509,38.78949,-83.97881,38.7871), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.20929,38.80267,-84.2129,38.80571), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.2129,38.80571,-82.20929,38.80267), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.16157,38.82463,-84.22616,38.82978), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.22616,38.82978,-82.16157,38.82463), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.23327,38.84267,-84.22616,38.82978), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.23231,38.87471,-84.23213,38.88048), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.23213,38.88048,-84.23231,38.87471), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.13477,38.90558,-81.89847,38.9296), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.89847,38.9296,-81.82735,38.9459), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.82735,38.9459,-84.28816,38.95579), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.28816,38.95579,-82.09887,38.96088), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.09887,38.96088,-84.28816,38.95579), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.08907,38.97598,-81.77573,38.98074), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.77573,38.98074,-82.08907,38.97598), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.29726,38.98969,-81.94183,38.9933), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.94183,38.9933,-84.29726,38.98969), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.04156,39.01788,-84.32121,39.02059), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.32121,39.02059,-82.04156,39.01788), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.32654,39.02746,-82.00706,39.02958), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.00706,39.02958,-84.32654,39.02746), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.7933,39.04035,-84.40094,39.04636), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.40094,39.04636,-81.7933,39.04035), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.60793,39.07324,-84.62228,39.07842), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.62228,39.07842,-84.60793,39.07324), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.43294,39.08396,-81.80786,39.08398), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.80786,39.08398,-84.43294,39.08396), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.67725,39.09826,-84.55084,39.09936), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.55084,39.09936,-84.67725,39.09826), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.50652,39.10177,-84.49919,39.10216), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.49919,39.10216,-84.49374,39.10246), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.49374,39.10246,-84.49919,39.10216), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.75027,39.10403,-84.82016,39.10548), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.82016,39.10548,-81.74295,39.10658), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.74295,39.10658,-84.82016,39.10548), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.44524,39.11446,-84.48094,39.11676), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.48094,39.11676,-84.44524,39.11446), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.46204,39.12176,-84.48094,39.11676), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.71405,39.13266,-84.46204,39.12176), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.75075,39.14736,-84.71405,39.13266), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.7523,39.18103,-81.75275,39.18468), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.75275,39.18468,-81.7523,39.18103), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.72147,39.21096,-81.71163,39.21923), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.71163,39.21923,-84.82016,39.22723), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.82016,39.22723,-81.71163,39.21923), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.82016,39.22723,-81.71163,39.21923), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.67833,39.27376,-81.6139,39.27534), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.6139,39.27534,-81.56525,39.27618), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.56525,39.27618,-81.6139,39.27534), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81888,39.30514,-84.81888,39.30517), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81888,39.30517,-84.81888,39.30514), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.55965,39.33077,-81.34757,39.34577), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.34757,39.34577,-81.37039,39.3487), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.37039,39.3487,-81.34757,39.34577), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.39379,39.35171,-81.37039,39.3487), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.50319,39.37324,-81.24909,39.38999), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.24909,39.38999,-84.81745,39.39175), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81745,39.39175,-81.24909,39.38999), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.41271,39.39462,-84.81745,39.39175), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.45614,39.40927,-81.41271,39.39462), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.18595,39.43073,-81.12853,39.44938), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.12853,39.44938,-81.12127,39.4577), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.12127,39.4577,-81.12853,39.44938), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.07595,39.50966,-84.81616,39.52197), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81616,39.52197,-81.07595,39.50966), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.03737,39.53806,-84.81616,39.52197), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81571,39.56772,-81.03737,39.53806), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.94378,39.60693,-84.81571,39.56772), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.86558,39.66275,-80.82976,39.71184), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.82976,39.71184,-80.83552,39.71925), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.83552,39.71925,-84.81413,39.72656), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81413,39.72656,-84.81413,39.72662), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81413,39.72662,-84.81413,39.72656), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.86993,39.76356,-84.81413,39.72662), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.82497,39.80109,-80.86993,39.76356), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.82428,39.84716,-80.82344,39.85003), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.82344,39.85003,-80.82428,39.84716), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81142,39.91691,-80.80339,39.91876), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.80339,39.91876,-84.81142,39.91691), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.76448,39.95025,-80.74013,39.97079), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.74013,39.97079,-80.76448,39.95025), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.81016,40.00507,-80.73822,40.03354), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.73822,40.03354,-84.81016,40.00507), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.7368,40.08007,-84.80871,40.10722), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80871,40.10722,-80.7368,40.08007), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.70599,40.15159,-80.70267,40.15699), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.70267,40.15699,-80.70599,40.15159), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.68417,40.18702,-80.70267,40.15699), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.6446,40.25127,-80.6066,40.30387), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.6066,40.30387,-84.80492,40.3101), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80492,40.3101,-80.6066,40.30387), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80412,40.35276,-84.80412,40.35284), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80412,40.35284,-84.80412,40.35276), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.6316,40.38547,-80.62736,40.39517), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.62736,40.39517,-80.6316,40.38547), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.6049,40.44667,-84.80293,40.46539), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80293,40.46539,-80.6049,40.44667), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80255,40.50181,-80.6222,40.5205), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.6222,40.5205,-84.80255,40.50181), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80241,40.57221,-80.66796,40.5825), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.66796,40.5825,-84.80241,40.57221), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.58363,40.61552,-80.62717,40.61994), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.62717,40.61994,-80.58363,40.61552), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51899,40.6388,-80.62717,40.61994), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80212,40.72815,-84.80212,40.72816), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80212,40.72816,-84.80212,40.72815), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51971,40.85134,-80.51987,40.90032), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51987,40.90032,-80.51988,40.90245), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51988,40.90245,-80.51987,40.90032), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51989,40.90666,-80.51988,40.90245), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80267,40.92257,-80.51989,40.90666), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51964,40.98739,-84.80286,40.98937), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80286,40.98937,-80.51964,40.98739), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80323,41.12141,-80.51922,41.12509), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51922,41.12509,-84.80323,41.12141), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.5192,41.13339,-80.51922,41.12509), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51889,41.23256,-84.80364,41.25256), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80364,41.25256,-84.8037,41.27126), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.8037,41.27126,-84.80364,41.25256), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.519,41.3335,-82.4606,41.38632), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.4606,41.38632,-82.53321,41.39116), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.53321,41.39116,-82.4606,41.38632), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80413,41.40829,-82.53321,41.39116), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80425,41.42605,-82.36178,41.42664), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.36178,41.42664,-84.80425,41.42605), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.34802,41.42726,-82.36178,41.42664), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.61695,41.42843,-82.34802,41.42726), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.26848,41.43084,-82.61695,41.42843), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.1816,41.47163,-81.73876,41.48855), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.73876,41.48855,-80.51917,41.48901), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51917,41.48901,-81.73876,41.48855), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.93786,41.49144,-81.76858,41.49149), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.76858,41.49149,-81.93786,41.49144), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.68792,41.49232,-81.76858,41.49149), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.8225,41.49526,-81.81076,41.49565), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.81076,41.49565,-81.8225,41.49526), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.69057,41.49671,-82.07115,41.49691), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.07115,41.49691,-82.69057,41.49671), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51918,41.49992,-81.96032,41.50055), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.96032,41.50055,-80.51918,41.49992), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.96848,41.50386,-81.96032,41.50055), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.93437,41.51435,-81.99457,41.51444), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.99457,41.51444,-82.93437,41.51435), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80496,41.53014,-81.63365,41.54046), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.63365,41.54046,-82.71788,41.54193), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.71788,41.54193,-81.63365,41.54046), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.02807,41.55566,-82.71788,41.54193), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.85953,41.57637,-82.8341,41.58759), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-82.8341,41.58759,-83.06659,41.59534), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.06659,41.59534,-82.8341,41.58759), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.16382,41.62413,-81.48868,41.63446), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.48868,41.63446,-83.23166,41.64422), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.23166,41.64422,-81.46604,41.64915), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.46604,41.64915,-83.23166,41.64422), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51936,41.66977,-81.46604,41.64915), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.40953,41.69125,-84.80608,41.69609), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.80608,41.69609,-83.40953,41.69125), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.32683,41.70156,-84.43807,41.7049), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.43807,41.7049,-84.39955,41.70592), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.39955,41.70592,-84.43807,41.7049), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.36042,41.70696,-81.38863,41.70714), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.38863,41.70714,-84.36042,41.70696), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-84.13442,41.71293,-81.38863,41.70714), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.88039,41.72019,-83.76304,41.72355), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.76304,41.72355,-83.88039,41.72019), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.76315,41.72355,-83.88039,41.72019), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.58554,41.72877,-83.45383,41.73265), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-83.45383,41.73265,-83.58554,41.72877), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.28693,41.76024,-81.18437,41.78667), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.18437,41.78667,-81.28693,41.76024), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.05192,41.83956,-81.00227,41.84917), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-81.00227,41.84917,-80.5194,41.84956), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.5194,41.84956,-81.00227,41.84917), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.90034,41.86891,-80.5194,41.84956), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.80079,41.90964,-80.90034,41.86891), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.58188,41.95761,-80.51943,41.97752), mapfile, tile_dir, 0, 11, "ohio-oh")
	render_tiles((-80.51943,41.97752,-80.58188,41.95761), mapfile, tile_dir, 0, 11, "ohio-oh")