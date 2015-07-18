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
    # Region: Delaware
    # Region Name: DE

	render_tiles((-75.18546,38.45101,-75.04894,38.45126), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.04894,38.45126,-75.18546,38.45101), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.34129,38.45244,-75.04894,38.45126), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.47928,38.4537,-75.34129,38.45244), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.69372,38.46013,-75.47928,38.4537), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.05397,38.53627,-75.70038,38.54274), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.70038,38.54274,-75.05397,38.53627), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.70178,38.56077,-75.70038,38.54274), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.70755,38.63534,-75.70756,38.63539), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.70756,38.63539,-75.70755,38.63534), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.07181,38.6965,-75.70756,38.63539), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.11333,38.783,-75.15902,38.79019), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.15902,38.79019,-75.08947,38.7972), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.08947,38.7972,-75.15902,38.79019), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.7231,38.82983,-75.23203,38.84425), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.23203,38.84425,-75.7231,38.82983), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.30408,38.91316,-75.30255,38.939), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.30255,38.939,-75.30665,38.94766), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.30665,38.94766,-75.30255,38.939), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.34089,39.01996,-75.39628,39.05788), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.39628,39.05788,-75.34089,39.01996), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.40747,39.13371,-75.74815,39.14313), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.74815,39.14313,-75.40747,39.13371), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.39479,39.18835,-75.74815,39.14313), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.75644,39.24669,-75.40838,39.2647), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.40838,39.2647,-75.75644,39.24669), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.76044,39.29679,-75.40838,39.2647), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.46932,39.33082,-75.76044,39.29679), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.50564,39.37039,-75.7669,39.3775), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.7669,39.3775,-75.7669,39.37765), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.7669,39.37765,-75.7669,39.3775), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.52168,39.38787,-75.7669,39.37765), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.57183,39.4389,-75.59307,39.47919), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.59307,39.47919,-75.52809,39.49811), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.52809,39.49811,-75.59307,39.47919), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.52768,39.53528,-75.52809,39.49811), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.51273,39.578,-75.54397,39.596), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.54397,39.596,-75.51273,39.578), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.55945,39.62981,-75.53514,39.64721), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.53514,39.64721,-75.55945,39.62981), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.50974,39.68611,-75.47764,39.71501), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.47764,39.71501,-75.77379,39.7222), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.77379,39.7222,-75.47764,39.71501), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.77379,39.7222,-75.47764,39.71501), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.7886,39.7222,-75.47764,39.71501), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.75323,39.75799,-75.45944,39.76581), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.45944,39.76581,-75.75323,39.75799), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.44104,39.78079,-75.71706,39.79233), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.71706,39.79233,-75.41506,39.80192), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.41506,39.80192,-75.71706,39.79233), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.66285,39.82143,-75.48121,39.82919), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.48121,39.82919,-75.59432,39.83459), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.59432,39.83459,-75.57043,39.83919), mapfile, tile_dir, 0, 11, "delaware-de")
	render_tiles((-75.57043,39.83919,-75.59432,39.83459), mapfile, tile_dir, 0, 11, "delaware-de")