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
    # Region: BI
    # Region Name: Burundi

	render_tiles((29.71194,-4.45472,30.3811,-4.45365), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.71194,-4.45472,30.3811,-4.45365), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.71194,-4.45472,30.3811,-4.45365), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.71194,-4.45472,30.3811,-4.45365), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.65313,-4.45365,30.3811,-4.45472), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.65313,-4.45365,30.3811,-4.45472), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.65313,-4.45365,30.3811,-4.45472), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.65313,-4.45365,30.3811,-4.45472), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.42359,-4.44947,29.71194,-2.79583), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.78885,-4.4044,30.3811,-4.36028), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.78885,-4.4044,30.3811,-4.36028), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.78885,-4.4044,30.3811,-4.36028), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.78885,-4.4044,30.3811,-4.36028), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.81277,-4.36028,29.71194,-2.77278), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.81277,-4.36028,29.71194,-2.77278), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.81277,-4.36028,29.71194,-2.77278), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.81277,-4.36028,29.71194,-2.77278), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.90583,-4.34972,29.71194,-2.70556), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.90583,-4.34972,29.71194,-2.70556), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.90583,-4.34972,29.71194,-2.70556), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.90583,-4.34972,29.71194,-2.70556), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.04444,-4.24639,29.71194,-2.35033), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.04444,-4.24639,29.71194,-2.35033), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.04444,-4.24639,29.71194,-2.35033), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.04444,-4.24639,29.71194,-2.35033), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.07277,-4.16667,29.71194,-2.38056), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.07277,-4.16667,29.71194,-2.38056), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.07277,-4.16667,29.71194,-2.38056), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.07277,-4.16667,29.71194,-2.38056), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.38555,-4.15417,29.71194,-2.82611), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.38555,-4.15417,29.71194,-2.82611), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.38555,-4.15417,29.71194,-2.82611), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.38555,-4.15417,29.71194,-2.82611), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.20777,-4.02611,29.71194,-2.33861), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.20777,-4.02611,29.71194,-2.33861), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.20777,-4.02611,29.71194,-2.33861), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.20777,-4.02611,29.71194,-2.33861), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.25583,-3.88695,29.71194,-2.33861), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.25583,-3.88695,29.71194,-2.33861), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.25583,-3.88695,29.71194,-2.33861), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.25583,-3.88695,29.71194,-2.33861), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.23249,-3.885,29.71194,-3.11083), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.23249,-3.885,29.71194,-3.11083), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.23249,-3.885,29.71194,-3.11083), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.23249,-3.885,29.71194,-3.11083), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.40083,-3.78611,29.71194,-2.86194), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.40083,-3.78611,29.71194,-2.86194), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.40083,-3.78611,29.71194,-2.86194), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.40083,-3.78611,29.71194,-2.86194), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.33583,-3.77417,29.71194,-2.33576), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.33583,-3.77417,29.71194,-2.33576), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.33583,-3.77417,29.71194,-2.33576), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.33583,-3.77417,29.71194,-2.33576), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.24694,-3.59444,29.71194,-3.11083), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.24694,-3.59444,29.71194,-3.11083), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.24694,-3.59444,29.71194,-3.11083), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.24694,-3.59444,29.71194,-3.11083), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.4494,-3.548,29.71194,-2.72306), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.4494,-3.548,29.71194,-2.72306), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.4494,-3.548,29.71194,-2.72306), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.4494,-3.548,29.71194,-2.72306), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.63166,-3.45056,29.71194,-3.37139), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.63166,-3.45056,29.71194,-3.37139), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.63166,-3.45056,29.71194,-3.37139), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.63166,-3.45056,29.71194,-3.37139), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.6636,-3.38667,29.71194,-3.31667), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.6636,-3.38667,29.71194,-3.31667), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.6636,-3.38667,29.71194,-3.31667), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.6636,-3.38667,29.71194,-3.31667), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.62416,-3.37139,30.3811,-3.45056), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.62416,-3.37139,30.3811,-3.45056), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.62416,-3.37139,30.3811,-3.45056), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.62416,-3.37139,30.3811,-3.45056), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.21551,-3.33816,29.71194,-3.33662), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.21551,-3.33816,29.71194,-3.33662), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.21551,-3.33816,29.71194,-3.33662), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.21551,-3.33816,29.71194,-3.33662), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.21532,-3.33662,29.71194,-3.33816), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.21532,-3.33662,29.71194,-3.33816), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.21532,-3.33662,29.71194,-3.33816), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.21532,-3.33662,29.71194,-3.33816), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.67833,-3.31667,30.3811,-3.38667), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.67833,-3.31667,30.3811,-3.38667), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.67833,-3.31667,30.3811,-3.38667), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.67833,-3.31667,30.3811,-3.38667), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.76749,-3.3,29.71194,-2.98972), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.76749,-3.3,29.71194,-2.98972), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.76749,-3.3,29.71194,-2.98972), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.76749,-3.3,29.71194,-2.98972), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.83499,-3.25694,29.71194,-3.11806), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.83499,-3.25694,29.71194,-3.11806), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.83499,-3.25694,29.71194,-3.11806), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.83499,-3.25694,29.71194,-3.11806), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.21055,-3.23667,29.71194,-3.33662), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.21055,-3.23667,29.71194,-3.33662), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.21055,-3.23667,29.71194,-3.33662), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.21055,-3.23667,29.71194,-3.33662), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.85388,-3.1575,29.71194,-2.97417), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.85388,-3.1575,29.71194,-2.97417), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.85388,-3.1575,29.71194,-2.97417), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.85388,-3.1575,29.71194,-2.97417), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.8311,-3.11806,29.71194,-3.25694), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.8311,-3.11806,29.71194,-3.25694), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.8311,-3.11806,29.71194,-3.25694), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.8311,-3.11806,29.71194,-3.25694), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.24055,-3.11083,30.3811,-3.59444), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.24055,-3.11083,30.3811,-3.59444), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.24055,-3.11083,30.3811,-3.59444), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.24055,-3.11083,30.3811,-3.59444), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.21916,-3.02111,29.71194,-3.33816), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.21916,-3.02111,29.71194,-3.33816), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.21916,-3.02111,29.71194,-3.33816), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.21916,-3.02111,29.71194,-3.33816), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.1486,-2.99611,29.71194,-2.58917), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.1486,-2.99611,29.71194,-2.58917), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.1486,-2.99611,29.71194,-2.58917), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.1486,-2.99611,29.71194,-2.58917), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.74277,-2.98972,29.71194,-3.3), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.74277,-2.98972,29.71194,-3.3), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.74277,-2.98972,29.71194,-3.3), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.74277,-2.98972,29.71194,-3.3), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.84027,-2.97417,29.71194,-3.25694), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.84027,-2.97417,29.71194,-3.25694), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.84027,-2.97417,29.71194,-3.25694), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.84027,-2.97417,29.71194,-3.25694), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.48516,-2.9465,29.71194,-2.67083), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.48516,-2.9465,29.71194,-2.67083), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.48516,-2.9465,29.71194,-2.67083), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.48516,-2.9465,29.71194,-2.67083), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.55722,-2.89389,29.71194,-2.3994), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.55722,-2.89389,29.71194,-2.3994), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.55722,-2.89389,29.71194,-2.3994), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.55722,-2.89389,29.71194,-2.3994), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.07444,-2.87472,29.71194,-2.60472), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.07444,-2.87472,29.71194,-2.60472), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.07444,-2.87472,29.71194,-2.60472), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.07444,-2.87472,29.71194,-2.60472), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.41749,-2.86194,29.71194,-2.67528), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.41749,-2.86194,29.71194,-2.67528), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.41749,-2.86194,29.71194,-2.67528), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.41749,-2.86194,29.71194,-2.67528), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.52749,-2.82667,29.71194,-2.8004), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.52749,-2.82667,29.71194,-2.8004), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.52749,-2.82667,29.71194,-2.8004), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.52749,-2.82667,29.71194,-2.8004), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.37944,-2.82611,30.3811,-4.15417), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.37944,-2.82611,30.3811,-4.15417), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.37944,-2.82611,30.3811,-4.15417), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.37944,-2.82611,30.3811,-4.15417), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((28.98749,-2.81056,29.71194,-2.77), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((28.98749,-2.81056,29.71194,-2.77), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((28.98749,-2.81056,29.71194,-2.77), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((28.98749,-2.81056,29.71194,-2.77), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.34972,-2.80806,29.71194,-2.65361), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.34972,-2.80806,29.71194,-2.65361), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.34972,-2.80806,29.71194,-2.65361), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.34972,-2.80806,29.71194,-2.65361), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.57125,-2.8004,29.71194,-2.82667), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.57125,-2.8004,29.71194,-2.82667), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.57125,-2.8004,29.71194,-2.82667), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.57125,-2.8004,29.71194,-2.82667), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.44388,-2.79583,30.3811,-4.44947), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.44388,-2.79583,30.3811,-4.44947), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.44388,-2.79583,30.3811,-4.44947), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.44388,-2.79583,30.3811,-4.44947), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.82194,-2.77278,30.3811,-4.36028), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.82194,-2.77278,30.3811,-4.36028), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.82194,-2.77278,30.3811,-4.36028), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.82194,-2.77278,30.3811,-4.36028), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((28.98777,-2.77,29.71194,-2.81056), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((28.98777,-2.77,29.71194,-2.81056), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((28.98777,-2.77,29.71194,-2.81056), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((28.98777,-2.77,29.71194,-2.81056), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.02415,-2.74445,29.71194,-2.60472), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.02415,-2.74445,29.71194,-2.60472), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.02415,-2.74445,29.71194,-2.60472), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.02415,-2.74445,29.71194,-2.60472), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.45721,-2.72306,30.3811,-3.548), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.45721,-2.72306,30.3811,-3.548), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.45721,-2.72306,30.3811,-3.548), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.45721,-2.72306,30.3811,-3.548), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.89944,-2.70556,30.3811,-4.34972), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.89944,-2.70556,30.3811,-4.34972), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.89944,-2.70556,30.3811,-4.34972), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.89944,-2.70556,30.3811,-4.34972), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.4336,-2.67528,29.71194,-2.64306), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.4336,-2.67528,29.71194,-2.64306), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.4336,-2.67528,29.71194,-2.64306), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.4336,-2.67528,29.71194,-2.64306), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.48916,-2.67083,29.71194,-2.9465), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.48916,-2.67083,29.71194,-2.9465), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.48916,-2.67083,29.71194,-2.9465), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.48916,-2.67083,29.71194,-2.9465), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.32888,-2.65361,29.71194,-2.80806), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.32888,-2.65361,29.71194,-2.80806), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.32888,-2.65361,29.71194,-2.80806), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.32888,-2.65361,29.71194,-2.80806), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.52246,-2.64964,29.71194,-2.67083), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.52246,-2.64964,29.71194,-2.67083), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.52246,-2.64964,29.71194,-2.67083), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.52246,-2.64964,29.71194,-2.67083), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.43527,-2.64306,29.71194,-2.67528), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.43527,-2.64306,29.71194,-2.67528), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.43527,-2.64306,29.71194,-2.67528), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.43527,-2.64306,29.71194,-2.67528), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.05888,-2.60472,29.71194,-2.87472), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.05888,-2.60472,29.71194,-2.87472), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.05888,-2.60472,29.71194,-2.87472), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.05888,-2.60472,29.71194,-2.87472), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.14055,-2.58917,29.71194,-2.99611), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.14055,-2.58917,29.71194,-2.99611), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.14055,-2.58917,29.71194,-2.99611), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.14055,-2.58917,29.71194,-2.99611), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.95471,-2.475,29.71194,-2.32111), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.95471,-2.475,29.71194,-2.32111), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.95471,-2.475,29.71194,-2.32111), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.95471,-2.475,29.71194,-2.32111), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.1536,-2.43056,29.71194,-2.42516), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.1536,-2.43056,29.71194,-2.42516), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.1536,-2.43056,29.71194,-2.42516), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.1536,-2.43056,29.71194,-2.42516), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.13243,-2.42516,29.71194,-2.43056), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.13243,-2.42516,29.71194,-2.43056), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.13243,-2.42516,29.71194,-2.43056), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.13243,-2.42516,29.71194,-2.43056), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.57375,-2.3994,29.71194,-2.89389), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.57375,-2.3994,29.71194,-2.89389), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.57375,-2.3994,29.71194,-2.89389), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.57375,-2.3994,29.71194,-2.89389), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.09361,-2.38056,30.3811,-4.16667), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.09361,-2.38056,30.3811,-4.16667), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.09361,-2.38056,30.3811,-4.16667), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.09361,-2.38056,30.3811,-4.16667), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.03504,-2.35033,30.3811,-4.24639), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.03504,-2.35033,30.3811,-4.24639), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.03504,-2.35033,30.3811,-4.24639), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.03504,-2.35033,30.3811,-4.24639), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.9911,-2.3425,29.71194,-2.33961), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.9911,-2.3425,29.71194,-2.33961), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.9911,-2.3425,29.71194,-2.33961), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.9911,-2.3425,29.71194,-2.33961), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.00735,-2.33961,29.71194,-2.3387), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.00735,-2.33961,29.71194,-2.3387), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.00735,-2.33961,29.71194,-2.3387), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.00735,-2.33961,29.71194,-2.3387), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.01251,-2.3387,29.71194,-2.33961), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.01251,-2.3387,29.71194,-2.33961), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.01251,-2.3387,29.71194,-2.33961), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.01251,-2.3387,29.71194,-2.33961), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.22499,-2.33861,30.3811,-4.02611), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.22499,-2.33861,30.3811,-4.02611), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.22499,-2.33861,30.3811,-4.02611), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.22499,-2.33861,30.3811,-4.02611), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.29598,-2.33702,30.3811,-3.77417), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.29598,-2.33702,30.3811,-3.77417), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.29598,-2.33702,30.3811,-3.77417), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.29598,-2.33702,30.3811,-3.77417), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.35255,-2.33576,29.71194,-2.33568), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.35255,-2.33576,29.71194,-2.33568), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.35255,-2.33576,29.71194,-2.33568), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.35255,-2.33576,29.71194,-2.33568), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.35615,-2.33568,29.71194,-2.33576), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.35615,-2.33568,29.71194,-2.33576), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.35615,-2.33568,29.71194,-2.33576), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.35615,-2.33568,29.71194,-2.33576), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((29.94694,-2.32111,29.71194,-2.475), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((29.94694,-2.32111,29.71194,-2.475), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((29.94694,-2.32111,29.71194,-2.475), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((29.94694,-2.32111,29.71194,-2.475), mapfile, tile_dir, 17, 17, "bi-burundi")
	render_tiles((30.3811,-2.29944,30.3811,-3.78611), mapfile, tile_dir, 0, 11, "bi-burundi")
	render_tiles((30.3811,-2.29944,30.3811,-3.78611), mapfile, tile_dir, 13, 13, "bi-burundi")
	render_tiles((30.3811,-2.29944,30.3811,-3.78611), mapfile, tile_dir, 15, 15, "bi-burundi")
	render_tiles((30.3811,-2.29944,30.3811,-3.78611), mapfile, tile_dir, 17, 17, "bi-burundi")