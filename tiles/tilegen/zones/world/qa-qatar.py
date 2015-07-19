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
    # Region: QA
    # Region Name: Qatar

	render_tiles((51.10862,24.55559,51.10862,26.04944), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.10862,24.55559,51.10862,26.04944), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.10862,24.55559,51.10862,26.04944), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.10862,24.55559,51.10862,26.04944), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.33166,24.5725,51.10862,26.04083), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.33166,24.5725,51.10862,26.04083), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.33166,24.5725,51.10862,26.04083), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.33166,24.5725,51.10862,26.04083), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.96943,24.57777,51.10862,25.8025), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.96943,24.57777,51.10862,25.8025), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.96943,24.57777,51.10862,25.8025), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.96943,24.57777,51.10862,25.8025), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.21603,24.61903,51.10862,26.15277), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.34666,24.63611,51.10862,26.04083), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.34666,24.63611,51.10862,26.04083), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.34666,24.63611,51.10862,26.04083), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.34666,24.63611,51.10862,26.04083), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.29166,24.64611,51.10862,26.15277), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.29166,24.64611,51.10862,26.15277), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.29166,24.64611,51.10862,26.15277), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.29166,24.64611,51.10862,26.15277), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.43388,24.65916,51.10862,25.94611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.43388,24.65916,51.10862,25.94611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.43388,24.65916,51.10862,25.94611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.43388,24.65916,51.10862,25.94611), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.82764,24.75386,51.10862,25.57638), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.82764,24.75386,51.10862,25.57638), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.82764,24.75386,51.10862,25.57638), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.82764,24.75386,51.10862,25.57638), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.85749,24.87611,51.10862,25.56916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.85749,24.87611,51.10862,25.56916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.85749,24.87611,51.10862,25.56916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.85749,24.87611,51.10862,25.56916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.54749,24.90138,51.10862,25.72528), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.54749,24.90138,51.10862,25.72528), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.54749,24.90138,51.10862,25.72528), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.54749,24.90138,51.10862,25.72528), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.7961,24.99028,51.10862,25.61666), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.7961,24.99028,51.10862,25.61666), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.7961,24.99028,51.10862,25.61666), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.7961,24.99028,51.10862,25.61666), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.61166,25.01028,51.25443,25.26916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.61166,25.01028,51.25443,25.26916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.61166,25.01028,51.25443,25.26916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.61166,25.01028,51.25443,25.26916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.60638,25.26916,51.25443,25.01028), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.60638,25.26916,51.25443,25.01028), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.60638,25.26916,51.25443,25.01028), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.60638,25.26916,51.25443,25.01028), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.51305,25.29472,51.10862,25.94388), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.51305,25.29472,51.10862,25.94388), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.51305,25.29472,51.10862,25.94388), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.51305,25.29472,51.10862,25.94388), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.75249,25.43833,51.10862,25.52388), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.75249,25.43833,51.10862,25.52388), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.75249,25.43833,51.10862,25.52388), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.75249,25.43833,51.10862,25.52388), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.84471,25.46416,51.25443,24.87611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.84471,25.46416,51.25443,24.87611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.84471,25.46416,51.25443,24.87611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.84471,25.46416,51.25443,24.87611), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.80777,25.49027,51.10862,25.61666), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.80777,25.49027,51.10862,25.61666), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.80777,25.49027,51.10862,25.61666), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.80777,25.49027,51.10862,25.61666), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.78471,25.52388,51.25443,24.99028), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.78471,25.52388,51.25443,24.99028), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.78471,25.52388,51.25443,24.99028), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.78471,25.52388,51.25443,24.99028), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.89777,25.52666,51.10862,25.64), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.89777,25.52666,51.10862,25.64), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.89777,25.52666,51.10862,25.64), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.89777,25.52666,51.10862,25.64), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.86249,25.56916,51.25443,24.87611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.86249,25.56916,51.25443,24.87611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.86249,25.56916,51.25443,24.87611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.86249,25.56916,51.25443,24.87611), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.83082,25.57638,51.25443,24.75386), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.83082,25.57638,51.25443,24.75386), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.83082,25.57638,51.25443,24.75386), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.83082,25.57638,51.25443,24.75386), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.48916,25.615,51.25443,25.29472), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.48916,25.615,51.25443,25.29472), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.48916,25.615,51.25443,25.29472), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.48916,25.615,51.25443,25.29472), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.7986,25.61666,51.25443,24.99028), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.7986,25.61666,51.25443,24.99028), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.7986,25.61666,51.25443,24.99028), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.7986,25.61666,51.25443,24.99028), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.53832,25.61749,51.10862,25.72528), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.53832,25.61749,51.10862,25.72528), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.53832,25.61749,51.10862,25.72528), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.53832,25.61749,51.10862,25.72528), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.87498,25.6325,51.10862,25.56916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.87498,25.6325,51.10862,25.56916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.87498,25.6325,51.10862,25.56916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.87498,25.6325,51.10862,25.56916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.90471,25.64,51.10862,25.52666), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.90471,25.64,51.10862,25.52666), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.90471,25.64,51.10862,25.52666), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.90471,25.64,51.10862,25.52666), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.97555,25.64083,51.10862,25.8025), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.97555,25.64083,51.10862,25.8025), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.97555,25.64083,51.10862,25.8025), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.97555,25.64083,51.10862,25.8025), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.53138,25.67166,51.10862,25.61749), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.53138,25.67166,51.10862,25.61749), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.53138,25.67166,51.10862,25.61749), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.53138,25.67166,51.10862,25.61749), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.58471,25.68916,51.10862,25.72721), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.58471,25.68916,51.10862,25.72721), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.58471,25.68916,51.10862,25.72721), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.58471,25.68916,51.10862,25.72721), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.88915,25.71861,51.10862,25.52666), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.88915,25.71861,51.10862,25.52666), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.88915,25.71861,51.10862,25.52666), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.88915,25.71861,51.10862,25.52666), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.54388,25.72528,51.25443,24.90138), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.54388,25.72528,51.25443,24.90138), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.54388,25.72528,51.25443,24.90138), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.54388,25.72528,51.25443,24.90138), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.58221,25.72721,51.10862,25.68916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.58221,25.72721,51.10862,25.68916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.58221,25.72721,51.10862,25.68916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.58221,25.72721,51.10862,25.68916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.58777,25.74055,51.10862,25.68916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.58777,25.74055,51.10862,25.68916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.58777,25.74055,51.10862,25.68916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.58777,25.74055,51.10862,25.68916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.55777,25.75972,51.25443,24.90138), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.55777,25.75972,51.25443,24.90138), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.55777,25.75972,51.25443,24.90138), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.55777,25.75972,51.25443,24.90138), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.94193,25.79833,51.10862,25.85638), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.94193,25.79833,51.10862,25.85638), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.94193,25.79833,51.10862,25.85638), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.94193,25.79833,51.10862,25.85638), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.97527,25.8025,51.10862,25.64083), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.97527,25.8025,51.10862,25.64083), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.97527,25.8025,51.10862,25.64083), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.97527,25.8025,51.10862,25.64083), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((50.94832,25.85638,51.10862,25.79833), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((50.94832,25.85638,51.10862,25.79833), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((50.94832,25.85638,51.10862,25.79833), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((50.94832,25.85638,51.10862,25.79833), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.57304,25.89305,51.10862,25.72721), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.57304,25.89305,51.10862,25.72721), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.57304,25.89305,51.10862,25.72721), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.57304,25.89305,51.10862,25.72721), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.51471,25.94388,51.25443,25.29472), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.51471,25.94388,51.25443,25.29472), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.51471,25.94388,51.25443,25.29472), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.51471,25.94388,51.25443,25.29472), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.42055,25.94611,51.25443,24.65916), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.42055,25.94611,51.25443,24.65916), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.42055,25.94611,51.25443,24.65916), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.42055,25.94611,51.25443,24.65916), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.34388,26.04083,51.25443,24.63611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.34388,26.04083,51.25443,24.63611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.34388,26.04083,51.25443,24.63611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.34388,26.04083,51.25443,24.63611), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.04249,26.04944,51.25443,24.55559), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.04249,26.04944,51.25443,24.55559), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.04249,26.04944,51.25443,24.55559), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.04249,26.04944,51.25443,24.55559), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.34388,26.10277,51.25443,24.63611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.34388,26.10277,51.25443,24.63611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.34388,26.10277,51.25443,24.63611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.34388,26.10277,51.25443,24.63611), mapfile, tile_dir, 17, 17, "qa-qatar")
	render_tiles((51.25443,26.15277,51.25443,24.64611), mapfile, tile_dir, 0, 11, "qa-qatar")
	render_tiles((51.25443,26.15277,51.25443,24.64611), mapfile, tile_dir, 13, 13, "qa-qatar")
	render_tiles((51.25443,26.15277,51.25443,24.64611), mapfile, tile_dir, 15, 15, "qa-qatar")
	render_tiles((51.25443,26.15277,51.25443,24.64611), mapfile, tile_dir, 17, 17, "qa-qatar")