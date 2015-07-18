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
    # Region: Tennessee
    # Region Name: TN

	render_tiles((-85.38497,34.98299,-85.36392,34.98338), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.36392,34.98338,-85.47434,34.98367), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.47434,34.98367,-85.36392,34.98338), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.60517,34.98468,-85.27756,34.98498), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.27756,34.98498,-85.26506,34.98508), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.26506,34.98508,-85.27756,34.98498), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.04518,34.98688,-85.86395,34.98703), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.86395,34.98703,-85.04518,34.98688), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.97985,34.98721,-84.97697,34.98722), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.97697,34.98722,-84.97985,34.98721), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.86131,34.98779,-84.81048,34.98788), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.81048,34.98788,-84.77584,34.98794), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.77584,34.98794,-84.81048,34.98788), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.72743,34.98802,-84.50905,34.98803), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.50905,34.98803,-84.72743,34.98802), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.62148,34.98833,-84.32187,34.98841), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.32187,34.98841,-84.62148,34.98833), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.4678,34.99069,-86.31876,34.99108), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.31876,34.99108,-86.31127,34.9911), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.31127,34.9911,-86.31876,34.99108), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.78363,34.99192,-86.78365,34.99193), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.78365,34.99193,-86.78363,34.99192), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.83629,34.9928,-86.78365,34.99193), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.43495,34.99375,-89.35268,34.994), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.35268,34.994,-89.64428,34.99407), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.64428,34.99407,-89.35268,34.994), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.72432,34.99419,-89.75961,34.99424), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.75961,34.99424,-89.72432,34.99419), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.79519,34.99429,-89.75961,34.99424), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.19829,34.99445,-89.79519,34.99429), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.02654,34.99496,-89.01713,34.99497), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.01713,34.99497,-89.02654,34.99496), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.82305,34.99521,-88.78661,34.99525), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.78661,34.99525,-88.82305,34.99521), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.25811,34.99546,-88.20006,34.99563), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.20006,34.99563,-90.3093,34.99569), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.3093,34.99569,-88.36353,34.99575), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.36353,34.99575,-88.38049,34.99579), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.38049,34.99579,-88.36353,34.99575), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.46988,34.99603,-88.38049,34.99579), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.21076,34.99905,-87.21668,34.99915), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.21668,34.99915,-87.22405,34.99923), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.22405,34.99923,-87.21668,34.99915), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.6061,35.00352,-87.62503,35.00373), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.62503,35.00373,-87.6061,35.00352), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.85189,35.00566,-87.98492,35.00591), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.98492,35.00591,-88.00003,35.00594), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.00003,35.00594,-87.98492,35.00591), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.20296,35.00803,-88.00003,35.00594), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.3007,35.02879,-90.2653,35.04029), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.3007,35.02879,-90.2653,35.04029), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.2653,35.04029,-90.19715,35.05073), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.19715,35.05073,-90.2653,35.04029), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.18139,35.0914,-90.09061,35.11829), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.09061,35.11829,-90.16006,35.12883), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.16006,35.12883,-90.09061,35.11829), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.09978,35.16447,-90.16006,35.12883), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.09329,35.20328,-84.2866,35.20575), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.2866,35.20575,-90.09329,35.20328), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.28322,35.22658,-84.17852,35.24068), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.17852,35.24068,-84.09751,35.24738), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.09751,35.24738,-90.09795,35.24998), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.09795,35.24998,-84.09751,35.24738), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.22372,35.26908,-90.16659,35.27459), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.16659,35.27459,-84.22372,35.26908), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.02911,35.29212,-84.02351,35.29578), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.02351,35.29578,-84.02911,35.29212), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.12186,35.30454,-84.02351,35.29578), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.03808,35.34836,-90.0879,35.36327), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.0879,35.36327,-84.00759,35.37166), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.00759,35.37166,-90.0879,35.36327), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.02178,35.40742,-90.1125,35.41015), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.1125,35.41015,-84.02178,35.40742), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.04531,35.41544,-90.1125,35.41015), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.07055,35.42329,-90.04531,35.41544), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.97317,35.45258,-90.02206,35.45738), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.02206,35.45738,-83.95888,35.45791), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.95888,35.45791,-90.02206,35.45738), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.95311,35.46007,-83.95888,35.45791), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.9168,35.47361,-83.95311,35.46007), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.04581,35.49653,-83.8485,35.51926), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.8485,35.51926,-89.9585,35.5417), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.9585,35.5417,-90.03762,35.55033), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-90.03762,35.55033,-89.9585,35.5417), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.94475,35.56031,-83.77174,35.56212), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.77174,35.56212,-83.49834,35.56298), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.49834,35.56298,-83.77174,35.56212), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.58783,35.56696,-83.66289,35.5678), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.66289,35.5678,-83.65316,35.56831), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.65316,35.56831,-83.66289,35.5678), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.45243,35.60292,-89.9325,35.60787), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.9325,35.60787,-83.42158,35.61119), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.42158,35.61119,-89.9325,35.60787), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.87655,35.62665,-83.42158,35.61119), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.89892,35.6509,-83.29715,35.65775), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.29715,35.65775,-83.34726,35.66047), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.34726,35.66047,-83.29715,35.65775), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.95659,35.69549,-83.26463,35.70324), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.26463,35.70324,-89.95659,35.69549), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.25619,35.71506,-83.25535,35.71623), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.25535,35.71623,-83.25619,35.71506), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.19827,35.72549,-83.25535,35.71623), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.86387,35.74759,-89.91549,35.75492), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.91549,35.75492,-89.86387,35.74759), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.16154,35.76336,-89.91549,35.75492), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.09719,35.77607,-82.97841,35.78261), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.97841,35.78261,-89.79705,35.78265), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.79705,35.78265,-82.97841,35.78261), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.04853,35.78771,-89.79705,35.78265), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.96655,35.79555,-83.04853,35.78771), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.72343,35.80938,-82.96655,35.79555), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.93744,35.82732,-89.72343,35.80938), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.72952,35.84763,-82.93744,35.82732), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.72263,35.87372,-82.89972,35.8746), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.89972,35.8746,-89.72263,35.87372), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.64727,35.89492,-89.6489,35.90358), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.6489,35.90358,-89.64727,35.89492), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.65228,35.92146,-82.81613,35.92399), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.81613,35.92399,-89.65228,35.92146), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.91061,35.92693,-82.81613,35.92399), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.89387,35.93381,-82.91061,35.92693), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.86072,35.94743,-89.68692,35.94772), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.68692,35.94772,-82.86072,35.94743), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.78747,35.95216,-82.55787,35.9539), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.55787,35.9539,-82.78747,35.95216), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.61089,35.97444,-82.50787,35.98209), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.50787,35.98209,-82.61089,35.97444), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.7794,35.99251,-89.7331,36.00061), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.7331,36.00061,-82.46456,36.00651), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.46456,36.00651,-89.7331,36.00061), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.72507,36.0182,-89.69244,36.02051), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.69244,36.02051,-82.72507,36.0182), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.59553,36.02601,-89.69244,36.02051), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.60573,36.03723,-82.59553,36.02601), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.62837,36.06211,-82.41695,36.07295), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.41695,36.07295,-89.68003,36.08249), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.68003,36.08249,-82.40946,36.08341), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.40946,36.08341,-89.68003,36.08249), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.64302,36.10362,-82.12715,36.10442), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.12715,36.10442,-89.64302,36.10362), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.08052,36.10571,-82.08014,36.10572), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.08014,36.10572,-82.08052,36.10571), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.34686,36.11521,-82.02874,36.12432), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.02874,36.12432,-82.26569,36.12761), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.26569,36.12761,-82.02874,36.12432), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.29766,36.13351,-89.5921,36.13564), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.5921,36.13564,-82.14085,36.13622), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.14085,36.13622,-89.5921,36.13564), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.22027,36.15381,-82.21125,36.15901), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.21125,36.15901,-82.22027,36.15381), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.6238,36.18313,-89.62764,36.18546), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.62764,36.18546,-89.6238,36.18313), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.69263,36.22496,-81.9601,36.22813), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.9601,36.22813,-89.69263,36.22496), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.60237,36.23811,-81.9601,36.22813), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.67805,36.24828,-89.60237,36.23811), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.93437,36.26472,-89.55429,36.27775), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.55429,36.27775,-81.91845,36.28735), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.91845,36.28735,-89.55429,36.27775), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.90814,36.30201,-89.61182,36.30909), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.61182,36.30909,-81.90814,36.30201), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.70597,36.3385,-81.76898,36.34104), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.76898,36.34104,-89.60054,36.34299), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.60054,36.34299,-89.54503,36.34427), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.54503,36.34427,-89.5227,36.34479), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.5227,36.34479,-89.54503,36.34427), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.8332,36.34734,-89.5227,36.34479), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.51038,36.37836,-81.72524,36.38938), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.72524,36.38938,-89.51038,36.37836), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.73431,36.41334,-89.54234,36.4201), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.54234,36.4201,-81.73431,36.41334), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.52102,36.46193,-81.69531,36.46791), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.69531,36.46791,-89.52102,36.46193), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.53923,36.49793,-88.12738,36.49854), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.12738,36.49854,-89.41729,36.49903), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.41729,36.49903,-88.12738,36.49854), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.05335,36.5,-88.05047,36.50005), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.05047,36.50005,-88.05335,36.5), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.48908,36.50128,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.34519,36.50134,-88.48908,36.50128), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.51636,36.50146,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.51192,36.50146,-89.34519,36.50134), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.81676,36.50195,-88.82718,36.50197), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.82718,36.50197,-88.83459,36.50198), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.83459,36.50198,-88.82718,36.50197), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.96447,36.50219,-88.83459,36.50198), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-89.21141,36.50563,-88.96447,36.50219), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.69996,36.53683,-88.0338,36.55173), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.0338,36.55173,-81.69996,36.53683), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.69071,36.58258,-83.89442,36.58648), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.89442,36.58648,-83.93076,36.58769), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.93076,36.58769,-81.67754,36.58812), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.67754,36.58812,-83.93076,36.58769), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.98761,36.58959,-83.98784,36.5896), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.98784,36.5896,-83.98761,36.58959), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.22719,36.59218,-84.26132,36.59274), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.22733,36.59218,-84.26132,36.59274), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.26132,36.59274,-84.22719,36.59218), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.83043,36.59376,-81.93414,36.59421), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.93414,36.59421,-82.14607,36.59456), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.14607,36.59456,-82.17398,36.59461), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.17398,36.59461,-82.14607,36.59456), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.24339,36.59488,-82.29414,36.59507), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.29414,36.59507,-82.60918,36.59509), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.60918,36.59509,-82.29414,36.59507), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.98445,36.59529,-82.60918,36.59509), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-82.48724,36.59582,-82.98445,36.59529), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.49994,36.59668,-82.48724,36.59582), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.2763,36.59819,-83.47209,36.59948), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.47209,36.59948,-83.2763,36.59819), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-83.67541,36.60081,-83.47209,36.59948), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.77846,36.60321,-84.78534,36.60337), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.78534,36.60337,-84.7854,36.60338), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.7854,36.60338,-84.78534,36.60337), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.6469,36.61192,-84.94395,36.61257), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.94395,36.61257,-81.6469,36.61192), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-84.97487,36.61458,-81.82673,36.61472), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.82673,36.61472,-84.97487,36.61458), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.48835,36.61499,-81.82673,36.61472), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-81.92264,36.61621,-85.48835,36.61499), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.4364,36.618,-81.92264,36.61621), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.73186,36.62043,-85.78856,36.62171), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.78856,36.62171,-85.09613,36.62248), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.09613,36.62248,-85.78856,36.62171), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.87386,36.62364,-85.09613,36.62248), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.29581,36.62615,-85.27629,36.62616), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.27629,36.62616,-85.29581,36.62615), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.29063,36.62645,-85.27629,36.62616), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-85.97571,36.62864,-88.05574,36.63048), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.05574,36.63048,-85.97571,36.62864), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.8532,36.63325,-86.08194,36.63385), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.08194,36.63385,-87.8532,36.63325), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.69419,36.63684,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.55129,36.63799,-87.64115,36.63804), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.64115,36.63804,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.64115,36.63804,-86.55129,36.63799), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.20557,36.63925,-87.64115,36.63804), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.56203,36.64074,-87.3478,36.64144), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.3478,36.64144,-87.33598,36.64158), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.33598,36.64158,-87.3478,36.64144), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.115,36.64414,-87.06083,36.64477), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.06083,36.64477,-87.115,36.64414), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.81304,36.64765,-86.4115,36.64824), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.4115,36.64824,-86.76329,36.64872), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.76329,36.64872,-86.4115,36.64824), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.60639,36.65211,-86.50777,36.65245), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-86.50777,36.65245,-86.60639,36.65211), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-87.84957,36.6637,-86.50777,36.65245), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.01179,36.67703,-88.07053,36.67812), mapfile, tile_dir, 0, 11, "tennessee-tn")
	render_tiles((-88.07053,36.67812,-88.01179,36.67703), mapfile, tile_dir, 0, 11, "tennessee-tn")