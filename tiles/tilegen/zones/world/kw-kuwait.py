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
    # Region: KW
    # Region Name: Kuwait

	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.22471,29.59472,48.22471,29.92388), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.17832,29.6011,48.22471,29.98241), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.17832,29.6011,48.22471,29.98241), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.17832,29.6011,48.22471,29.98241), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.17832,29.6011,48.22471,29.98241), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.27832,29.62388,48.15221,29.59472), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.27832,29.62388,48.15221,29.59472), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.27832,29.62388,48.15221,29.59472), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.27832,29.62388,48.15221,29.59472), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.35999,29.73194,48.15221,29.78222), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.35999,29.73194,48.15221,29.78222), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.35999,29.73194,48.15221,29.78222), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.35999,29.73194,48.15221,29.78222), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.34776,29.78222,48.15221,29.73194), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.34776,29.78222,48.15221,29.73194), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.34776,29.78222,48.15221,29.73194), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.34776,29.78222,48.15221,29.73194), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.04555,29.79111,48.22471,29.86722), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.04555,29.79111,48.22471,29.86722), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.04555,29.79111,48.22471,29.86722), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.04555,29.79111,48.22471,29.86722), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.11443,29.86722,48.22471,29.96861), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.11443,29.86722,48.22471,29.96861), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.11443,29.86722,48.22471,29.96861), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.11443,29.86722,48.22471,29.96861), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.15665,29.87555,48.22471,29.99194), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.15665,29.87555,48.22471,29.99194), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.15665,29.87555,48.22471,29.99194), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.15665,29.87555,48.22471,29.99194), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.20055,29.92388,48.22471,29.98241), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.20055,29.92388,48.22471,29.98241), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.20055,29.92388,48.22471,29.98241), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.20055,29.92388,48.22471,29.98241), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.11694,29.96861,48.22471,29.86722), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.11694,29.96861,48.22471,29.86722), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.11694,29.96861,48.22471,29.86722), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.11694,29.96861,48.22471,29.86722), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.18669,29.98241,48.15221,29.6011), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.18669,29.98241,48.15221,29.6011), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.18669,29.98241,48.15221,29.6011), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.18669,29.98241,48.15221,29.6011), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.15221,29.99194,48.22471,29.87555), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.15221,29.99194,48.22471,29.87555), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.15221,29.99194,48.22471,29.87555), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.15221,29.99194,48.22471,29.87555), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.41973,28.54436,47.73137,28.54686), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.41973,28.54436,47.73137,28.54686), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.41973,28.54436,47.73137,28.54686), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.41973,28.54436,47.73137,28.54686), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.41532,28.54686,47.73137,28.54436), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.41532,28.54686,47.73137,28.54436), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.41532,28.54686,47.73137,28.54436), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.41532,28.54686,47.73137,28.54436), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.68415,28.56194,48.41973,29.40555), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.68415,28.56194,48.41973,29.40555), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.68415,28.56194,48.41973,29.40555), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.68415,28.56194,48.41973,29.40555), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.37804,28.69416,47.73137,28.73916), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.37804,28.69416,47.73137,28.73916), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.37804,28.69416,47.73137,28.73916), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.37804,28.69416,47.73137,28.73916), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.57526,28.69999,47.73137,28.8561), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.57526,28.69999,47.73137,28.8561), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.57526,28.69999,47.73137,28.8561), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.57526,28.69999,47.73137,28.8561), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.3911,28.73916,47.73137,28.69416), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.3911,28.73916,47.73137,28.69416), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.3911,28.73916,47.73137,28.69416), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.3911,28.73916,47.73137,28.69416), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.31471,28.77139,47.73137,28.69416), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.31471,28.77139,47.73137,28.69416), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.31471,28.77139,47.73137,28.69416), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.31471,28.77139,47.73137,28.69416), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.53915,28.8561,47.73137,28.69999), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.53915,28.8561,47.73137,28.69999), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.53915,28.8561,47.73137,28.69999), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.53915,28.8561,47.73137,28.69999), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.46082,28.99888,47.73137,28.8561), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.46082,28.99888,47.73137,28.8561), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.46082,28.99888,47.73137,28.8561), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.46082,28.99888,47.73137,28.8561), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((46.69804,29.08638,47.73137,29.11249), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((46.69804,29.08638,47.73137,29.11249), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((46.69804,29.08638,47.73137,29.11249), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((46.69804,29.08638,47.73137,29.11249), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((46.54161,29.10416,47.73137,29.11249), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((46.55526,29.11249,47.73137,29.10416), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((46.55526,29.11249,47.73137,29.10416), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((46.55526,29.11249,47.73137,29.10416), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((46.55526,29.11249,47.73137,29.10416), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.09055,29.23,48.41973,29.35305), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.09055,29.23,48.41973,29.35305), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.09055,29.23,48.41973,29.35305), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.09055,29.23,48.41973,29.35305), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.8161,29.32999,48.41973,29.39305), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.8161,29.32999,48.41973,29.39305), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.8161,29.32999,48.41973,29.39305), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.8161,29.32999,48.41973,29.39305), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.87721,29.33528,48.41973,29.56972), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.87721,29.33528,48.41973,29.56972), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.87721,29.33528,48.41973,29.56972), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.87721,29.33528,48.41973,29.56972), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.0861,29.35305,48.41973,29.73583), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.0861,29.35305,48.41973,29.73583), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.0861,29.35305,48.41973,29.73583), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.0861,29.35305,48.41973,29.73583), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.70721,29.37583,48.41973,29.40555), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.70721,29.37583,48.41973,29.40555), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.70721,29.37583,48.41973,29.40555), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.70721,29.37583,48.41973,29.40555), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.82971,29.39305,48.41973,29.32999), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.82971,29.39305,48.41973,29.32999), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.82971,29.39305,48.41973,29.32999), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.82971,29.39305,48.41973,29.32999), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.9786,29.39777,48.41973,29.63139), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.9786,29.39777,48.41973,29.63139), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.9786,29.39777,48.41973,29.63139), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.9786,29.39777,48.41973,29.63139), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.70277,29.40555,48.41973,29.37583), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.70277,29.40555,48.41973,29.37583), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.70277,29.40555,48.41973,29.37583), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.70277,29.40555,48.41973,29.37583), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((46.8836,29.50277,47.73137,29.08638), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((46.8836,29.50277,47.73137,29.08638), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((46.8836,29.50277,47.73137,29.08638), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((46.8836,29.50277,47.73137,29.08638), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.13832,29.54722,48.41973,29.55305), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.13832,29.54722,48.41973,29.55305), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.13832,29.54722,48.41973,29.55305), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.13832,29.54722,48.41973,29.55305), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.16721,29.55305,48.41973,29.54722), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.16721,29.55305,48.41973,29.54722), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.16721,29.55305,48.41973,29.54722), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.16721,29.55305,48.41973,29.54722), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.91805,29.56972,48.41973,30.01166), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.91805,29.56972,48.41973,30.01166), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.91805,29.56972,48.41973,30.01166), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.91805,29.56972,48.41973,30.01166), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.9486,29.61972,48.41973,30.01302), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.9486,29.61972,48.41973,30.01302), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.9486,29.61972,48.41973,30.01302), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.9486,29.61972,48.41973,30.01302), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.00388,29.63139,48.41973,29.79944), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.00388,29.63139,48.41973,29.79944), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.00388,29.63139,48.41973,29.79944), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.00388,29.63139,48.41973,29.79944), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.08193,29.73583,48.41973,29.35305), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.08193,29.73583,48.41973,29.35305), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.08193,29.73583,48.41973,29.35305), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.08193,29.73583,48.41973,29.35305), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((48.00916,29.79944,48.41973,29.63139), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((48.00916,29.79944,48.41973,29.63139), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((48.00916,29.79944,48.41973,29.63139), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((48.00916,29.79944,48.41973,29.63139), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.12887,29.97471,48.41973,30.01527), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.12887,29.97471,48.41973,30.01527), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.12887,29.97471,48.41973,30.01527), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.12887,29.97471,48.41973,30.01527), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.94444,30.01166,48.41973,29.61972), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.94444,30.01166,48.41973,29.61972), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.94444,30.01166,48.41973,29.61972), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.94444,30.01166,48.41973,29.61972), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.95044,30.01302,48.41973,29.61972), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.95044,30.01302,48.41973,29.61972), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.95044,30.01302,48.41973,29.61972), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.95044,30.01302,48.41973,29.61972), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.16998,30.01527,48.41973,29.97471), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.16998,30.01527,48.41973,29.97471), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.16998,30.01527,48.41973,29.97471), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.16998,30.01527,48.41973,29.97471), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.35693,30.08221,47.73137,28.99888), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.35693,30.08221,47.73137,28.99888), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.35693,30.08221,47.73137,28.99888), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.35693,30.08221,47.73137,28.99888), mapfile, tile_dir, 17, 17, "kw-kuwait")
	render_tiles((47.73137,30.08388,48.41973,29.37583), mapfile, tile_dir, 0, 11, "kw-kuwait")
	render_tiles((47.73137,30.08388,48.41973,29.37583), mapfile, tile_dir, 13, 13, "kw-kuwait")
	render_tiles((47.73137,30.08388,48.41973,29.37583), mapfile, tile_dir, 15, 15, "kw-kuwait")
	render_tiles((47.73137,30.08388,48.41973,29.37583), mapfile, tile_dir, 17, 17, "kw-kuwait")