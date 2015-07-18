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
    # Region: SV
    # Region Name: El Salvador

	render_tiles((-87.89639,13.15666,-88.19667,13.15944), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.19667,13.15944,-87.89639,13.15666), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.45251,13.16528,-88.42751,13.16916), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.42751,13.16916,-88.34167,13.17), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.34167,13.17,-88.42751,13.16916), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.38417,13.19166,-87.91528,13.19694), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.91528,13.19694,-88.38417,13.19166), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.44695,13.21555,-88.71918,13.23027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.71918,13.23027,-88.52779,13.24194), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.52779,13.24194,-87.82028,13.24583), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.82028,13.24583,-88.56029,13.24666), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.56029,13.24666,-87.82028,13.24583), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.60583,13.25861,-88.71973,13.26889), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.71973,13.26889,-88.60583,13.25861), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.60139,13.28305,-88.71973,13.26889), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.78528,13.29778,-88.60139,13.28305), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.9614,13.32917,-87.78528,13.29778), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.06001,13.36806,-87.87195,13.38916), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.87195,13.38916,-87.8145,13.40747), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.8145,13.40747,-87.87195,13.38916), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.83667,13.43722,-87.8145,13.40747), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.71445,13.47055,-89.28946,13.48027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.28946,13.48027,-87.71445,13.47055), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.57751,13.51111,-89.80945,13.52611), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.80945,13.52611,-87.78333,13.52639), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.78333,13.52639,-89.80945,13.52611), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.84668,13.60722,-87.78333,13.52639), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-90.0625,13.7275,-90.09509,13.74547), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-90.09509,13.74547,-90.0625,13.7275), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.69527,13.81805,-90.10583,13.83583), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-90.10583,13.83583,-87.69527,13.81805), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.75047,13.86406,-88.48917,13.86555), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.48917,13.86555,-87.75047,13.86406), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.35306,13.86833,-88.00696,13.86944), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.00696,13.86944,-88.35306,13.86833), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.90028,13.89805,-90.03751,13.90027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-90.03751,13.90027,-87.8786,13.90194), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-87.8786,13.90194,-90.03751,13.90027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.04083,13.93055,-87.8786,13.90194), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.06,13.96389,-88.50473,13.98138), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.50473,13.98138,-88.19666,13.98777), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.19666,13.98777,-88.50473,13.98138), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.66144,14.01413,-88.19666,13.98777), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.73778,14.04361,-89.8546,14.05946), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.8546,14.05946,-89.73778,14.04361), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.74306,14.08389,-88.80943,14.09305), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.80943,14.09305,-88.72139,14.0975), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.72139,14.0975,-88.80943,14.09305), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.83139,14.11416,-88.72139,14.0975), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.76723,14.13778,-88.83139,14.11416), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.95473,14.18833,-89.64612,14.19972), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.64612,14.19972,-88.90862,14.20722), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-88.90862,14.20722,-89.64612,14.19972), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.49271,14.24125,-88.90862,14.20722), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.57501,14.27722,-89.55345,14.28625), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.55345,14.28625,-89.57501,14.27722), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.59029,14.32194,-89.1261,14.32555), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.1261,14.32555,-89.59029,14.32194), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.03722,14.33472,-89.1261,14.32555), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.52695,14.38638,-89.57333,14.41333), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.57333,14.41333,-89.54222,14.42027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.54222,14.42027,-89.57333,14.41333), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.35487,14.42769,-89.54222,14.42027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.35487,14.42769,-89.54222,14.42027), mapfile, tile_dir, 0, 11, "sv-el-salvador")
	render_tiles((-89.39612,14.45166,-89.35487,14.42769), mapfile, tile_dir, 0, 11, "sv-el-salvador")