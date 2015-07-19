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
    # Region: IL
    # Region Name: Israel

	render_tiles((34.90154,29.49379,35.56666,31.36361), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.90154,29.49379,35.56666,31.36361), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.90154,29.49379,35.56666,31.36361), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.90154,29.49379,35.56666,31.36361), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.9461,29.535,34.90154,31.59472), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.9461,29.535,34.90154,31.59472), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.9461,29.535,34.90154,31.59472), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.9461,29.535,34.90154,31.59472), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.96192,29.5511,34.90154,31.85889), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.96192,29.5511,34.90154,31.85889), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.96192,29.5511,34.90154,31.85889), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.96192,29.5511,34.90154,31.85889), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.96554,29.56249,34.90154,32.83332), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.96554,29.56249,34.90154,32.83332), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.96554,29.56249,34.90154,32.83332), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.96554,29.56249,34.90154,32.83332), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.86582,29.58416,35.56666,29.67388), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.86582,29.58416,35.56666,29.67388), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.86582,29.58416,35.56666,29.67388), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.86582,29.58416,35.56666,29.67388), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.9734,29.58552,34.90154,31.82471), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.9734,29.58552,34.90154,31.82471), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.9734,29.58552,34.90154,31.82471), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.9734,29.58552,34.90154,31.82471), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.86221,29.67388,35.56666,29.58416), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.86221,29.67388,35.56666,29.58416), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.86221,29.67388,35.56666,29.58416), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.86221,29.67388,35.56666,29.58416), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.83332,29.77777,34.90154,32.30804), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.83332,29.77777,34.90154,32.30804), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.83332,29.77777,34.90154,32.30804), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.83332,29.77777,34.90154,32.30804), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.78915,29.89083,34.90154,32.13194), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.78915,29.89083,34.90154,32.13194), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.78915,29.89083,34.90154,32.13194), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.78915,29.89083,34.90154,32.13194), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.75471,30,34.90154,32.13194), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.75471,30,34.90154,32.13194), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.75471,30,34.90154,32.13194), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.75471,30,34.90154,32.13194), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.7136,30.11277,34.90154,31.96555), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.7136,30.11277,34.90154,31.96555), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.7136,30.11277,34.90154,31.96555), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.7136,30.11277,34.90154,31.96555), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.16693,30.15166,35.56666,30.41566), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.16693,30.15166,35.56666,30.41566), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.16693,30.15166,35.56666,30.41566), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.16693,30.15166,35.56666,30.41566), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.66776,30.22583,34.90154,31.81055), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.66776,30.22583,34.90154,31.81055), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.66776,30.22583,34.90154,31.81055), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.66776,30.22583,34.90154,31.81055), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.14054,30.23971,35.56666,31.36194), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.14054,30.23971,35.56666,31.36194), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.14054,30.23971,35.56666,31.36194), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.14054,30.23971,35.56666,31.36194), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.62248,30.33833,34.90154,31.81055), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.62248,30.33833,34.90154,31.81055), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.62248,30.33833,34.90154,31.81055), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.62248,30.33833,34.90154,31.81055), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.18193,30.36694,35.56666,30.15166), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.18193,30.36694,35.56666,30.15166), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.18193,30.36694,35.56666,30.15166), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.18193,30.36694,35.56666,30.15166), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.15727,30.41566,35.56666,30.15166), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.15727,30.41566,35.56666,30.15166), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.15727,30.41566,35.56666,30.15166), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.15727,30.41566,35.56666,30.15166), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.5436,30.42999,34.90154,31.66471), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.5436,30.42999,34.90154,31.66471), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.5436,30.42999,34.90154,31.66471), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.5436,30.42999,34.90154,31.66471), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.52054,30.5411,35.56666,30.65305), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.52054,30.5411,35.56666,30.65305), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.52054,30.5411,35.56666,30.65305), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.52054,30.5411,35.56666,30.65305), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.20026,30.56777,35.56666,30.36694), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.20026,30.56777,35.56666,30.36694), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.20026,30.56777,35.56666,30.36694), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.20026,30.56777,35.56666,30.36694), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.5011,30.65305,34.90154,31.59666), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.5011,30.65305,34.90154,31.59666), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.5011,30.65305,34.90154,31.59666), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.5011,30.65305,34.90154,31.59666), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.28471,30.7186,34.90154,33.10721), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.28471,30.7186,34.90154,33.10721), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.28471,30.7186,34.90154,33.10721), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.28471,30.7186,34.90154,33.10721), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.45054,30.76583,34.90154,31.59666), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.45054,30.76583,34.90154,31.59666), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.45054,30.76583,34.90154,31.59666), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.45054,30.76583,34.90154,31.59666), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.39415,30.87888,35.56666,31.29472), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.39415,30.87888,35.56666,31.29472), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.39415,30.87888,35.56666,31.29472), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.39415,30.87888,35.56666,31.29472), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.33776,30.88694,34.90154,33.06304), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.33776,30.88694,34.90154,33.06304), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.33776,30.88694,34.90154,33.06304), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.33776,30.88694,34.90154,33.06304), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.39221,30.94333,34.90154,31.49166), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.39221,30.94333,34.90154,31.49166), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.39221,30.94333,34.90154,31.49166), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.39221,30.94333,34.90154,31.49166), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.35221,30.99166,35.56666,31.36082), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.35221,30.99166,35.56666,31.36082), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.35221,30.99166,35.56666,31.36082), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.35221,30.99166,35.56666,31.36082), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.44179,31.08933,34.90154,32.4161), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.44179,31.08933,34.90154,32.4161), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.44179,31.08933,34.90154,32.4161), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.44179,31.08933,34.90154,32.4161), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.30971,31.10444,35.56666,30.99166), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.30971,31.10444,35.56666,30.99166), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.30971,31.10444,35.56666,30.99166), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.30971,31.10444,35.56666,30.99166), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.4622,31.14944,35.56666,31.38055), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.4622,31.14944,35.56666,31.38055), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.4622,31.14944,35.56666,31.38055), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.4622,31.14944,35.56666,31.38055), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.26708,31.21626,35.56666,31.10444), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.26708,31.21626,35.56666,31.10444), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.26708,31.21626,35.56666,31.10444), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.26708,31.21626,35.56666,31.10444), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.41719,31.23467,34.90154,32.50082), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.41719,31.23467,34.90154,32.50082), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.41719,31.23467,34.90154,32.50082), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.41719,31.23467,34.90154,32.50082), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.39943,31.26833,34.90154,31.49174), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.39943,31.26833,34.90154,31.49174), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.39943,31.26833,34.90154,31.49174), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.39943,31.26833,34.90154,31.49174), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.37054,31.29472,35.56666,31.36082), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.37054,31.29472,35.56666,31.36082), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.37054,31.29472,35.56666,31.36082), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.37054,31.29472,35.56666,31.36082), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.43188,31.32319,34.90154,32.4161), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.43188,31.32319,34.90154,32.4161), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.43188,31.32319,34.90154,32.4161), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.43188,31.32319,34.90154,32.4161), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.36415,31.36082,35.56666,31.29472), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.36415,31.36082,35.56666,31.29472), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.36415,31.36082,35.56666,31.29472), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.36415,31.36082,35.56666,31.29472), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.1386,31.36194,35.56666,30.23971), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.1386,31.36194,35.56666,30.23971), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.1386,31.36194,35.56666,30.23971), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.1386,31.36194,35.56666,30.23971), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.90415,31.36361,35.56666,29.49379), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.90415,31.36361,35.56666,29.49379), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.90415,31.36361,35.56666,29.49379), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.90415,31.36361,35.56666,29.49379), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.46582,31.38055,35.56666,31.14944), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.46582,31.38055,35.56666,31.14944), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.46582,31.38055,35.56666,31.14944), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.46582,31.38055,35.56666,31.14944), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.88443,31.40471,34.90154,32.48693), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.88443,31.40471,34.90154,32.48693), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.88443,31.40471,34.90154,32.48693), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.88443,31.40471,34.90154,32.48693), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.32027,31.43916,34.90154,33.10721), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.32027,31.43916,34.90154,33.10721), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.32027,31.43916,34.90154,33.10721), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.32027,31.43916,34.90154,33.10721), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.39471,31.49166,34.90154,31.49174), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.39471,31.49166,34.90154,31.49174), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.39471,31.49166,34.90154,31.49174), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.39471,31.49166,34.90154,31.49174), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.39592,31.49174,34.90154,31.49166), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.39592,31.49174,34.90154,31.49166), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.39592,31.49174,34.90154,31.49166), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.39592,31.49174,34.90154,31.49166), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.47865,31.49783,35.56666,31.38055), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.47865,31.49783,35.56666,31.38055), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.47865,31.49783,35.56666,31.38055), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.47865,31.49783,35.56666,31.38055), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.55859,31.53305,34.90154,31.66471), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.55859,31.53305,34.90154,31.66471), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.55859,31.53305,34.90154,31.66471), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.55859,31.53305,34.90154,31.66471), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.9536,31.59472,35.56666,29.535), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.9536,31.59472,35.56666,29.535), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.9536,31.59472,35.56666,29.535), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.9536,31.59472,35.56666,29.535), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.49026,31.59666,35.56666,30.65305), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.49026,31.59666,35.56666,30.65305), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.49026,31.59666,35.56666,30.65305), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.49026,31.59666,35.56666,30.65305), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.02026,31.65805,34.90154,32.81527), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.02026,31.65805,34.90154,32.81527), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.02026,31.65805,34.90154,32.81527), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.02026,31.65805,34.90154,32.81527), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.54388,31.66471,35.56666,30.42999), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.54388,31.66471,35.56666,30.42999), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.54388,31.66471,35.56666,30.42999), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.54388,31.66471,35.56666,30.42999), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.22637,31.74777,34.90154,32.55193), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.22637,31.74777,34.90154,32.55193), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.22637,31.74777,34.90154,32.55193), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.22637,31.74777,34.90154,32.55193), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.21998,31.80833,34.90154,32.55193), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.21998,31.80833,34.90154,32.55193), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.21998,31.80833,34.90154,32.55193), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.21998,31.80833,34.90154,32.55193), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.63527,31.81055,35.56666,30.33833), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.63527,31.81055,35.56666,30.33833), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.63527,31.81055,35.56666,30.33833), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.63527,31.81055,35.56666,30.33833), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.97109,31.82471,35.56666,29.58552), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.97109,31.82471,35.56666,29.58552), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.97109,31.82471,35.56666,29.58552), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.97109,31.82471,35.56666,29.58552), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.10892,31.83342,34.90154,33.09211), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.10892,31.83342,34.90154,33.09211), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.10892,31.83342,34.90154,33.09211), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.10892,31.83342,34.90154,33.09211), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.96332,31.85889,34.90154,32.83332), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.96332,31.85889,34.90154,32.83332), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.96332,31.85889,34.90154,32.83332), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.96332,31.85889,34.90154,32.83332), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.03054,31.88388,34.90154,31.65805), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.03054,31.88388,34.90154,31.65805), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.03054,31.88388,34.90154,31.65805), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.03054,31.88388,34.90154,31.65805), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.71555,31.96555,35.56666,30.11277), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.71555,31.96555,35.56666,30.11277), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.71555,31.96555,35.56666,30.11277), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.71555,31.96555,35.56666,30.11277), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.00085,31.98558,34.90154,32.13248), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.00085,31.98558,34.90154,32.13248), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.00085,31.98558,34.90154,32.13248), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.00085,31.98558,34.90154,32.13248), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.78221,32.13194,35.56666,29.89083), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.78221,32.13194,35.56666,29.89083), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.78221,32.13194,35.56666,29.89083), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.78221,32.13194,35.56666,29.89083), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.98582,32.13248,35.56666,29.58552), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.98582,32.13248,35.56666,29.58552), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.98582,32.13248,35.56666,29.58552), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.98582,32.13248,35.56666,29.58552), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.83804,32.30804,35.56666,29.77777), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.83804,32.30804,35.56666,29.77777), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.83804,32.30804,35.56666,29.77777), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.83804,32.30804,35.56666,29.77777), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.55293,32.39379,34.90154,32.52138), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.55293,32.39379,34.90154,32.52138), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.55293,32.39379,34.90154,32.52138), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.55293,32.39379,34.90154,32.52138), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.43221,32.4161,35.56666,31.32319), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.43221,32.4161,35.56666,31.32319), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.43221,32.4161,35.56666,31.32319), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.43221,32.4161,35.56666,31.32319), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.56332,32.44109,34.90154,32.65192), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.56332,32.44109,34.90154,32.65192), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.56332,32.44109,34.90154,32.65192), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.56332,32.44109,34.90154,32.65192), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.07999,32.47054,34.90154,32.92249), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.07999,32.47054,34.90154,32.92249), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.07999,32.47054,34.90154,32.92249), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.07999,32.47054,34.90154,32.92249), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.88805,32.48693,34.90154,31.40471), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.88805,32.48693,34.90154,31.40471), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.88805,32.48693,34.90154,31.40471), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.88805,32.48693,34.90154,31.40471), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.4122,32.50082,35.56666,31.23467), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.4122,32.50082,35.56666,31.23467), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.4122,32.50082,35.56666,31.23467), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.4122,32.50082,35.56666,31.23467), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.55415,32.52138,34.90154,32.39379), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.55415,32.52138,34.90154,32.39379), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.55415,32.52138,34.90154,32.39379), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.55415,32.52138,34.90154,32.39379), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.22276,32.55193,34.90154,31.80833), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.22276,32.55193,34.90154,31.80833), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.22276,32.55193,34.90154,31.80833), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.22276,32.55193,34.90154,31.80833), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.5636,32.65192,34.90154,32.44109), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.5636,32.65192,34.90154,32.44109), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.5636,32.65192,34.90154,32.44109), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.5636,32.65192,34.90154,32.44109), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.92666,32.66444,35.56666,29.535), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.92666,32.66444,35.56666,29.535), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.92666,32.66444,35.56666,29.535), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.92666,32.66444,35.56666,29.535), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.63345,32.68651,34.90154,32.74999), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.63345,32.68651,34.90154,32.74999), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.63345,32.68651,34.90154,32.74999), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.63345,32.68651,34.90154,32.74999), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.59917,32.71503,34.90154,33.01776), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.59917,32.71503,34.90154,33.01776), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.59917,32.71503,34.90154,33.01776), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.59917,32.71503,34.90154,33.01776), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.63165,32.74999,34.90154,32.68651), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.63165,32.74999,34.90154,32.68651), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.63165,32.74999,34.90154,32.68651), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.63165,32.74999,34.90154,32.68651), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.6394,32.81448,34.90154,33.04971), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.6394,32.81448,34.90154,33.04971), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.6394,32.81448,34.90154,33.04971), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.6394,32.81448,34.90154,33.04971), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.01721,32.81527,34.90154,31.65805), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.01721,32.81527,34.90154,31.65805), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.01721,32.81527,34.90154,31.65805), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.01721,32.81527,34.90154,31.65805), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((34.96387,32.83332,34.90154,31.85889), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((34.96387,32.83332,34.90154,31.85889), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((34.96387,32.83332,34.90154,31.85889), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((34.96387,32.83332,34.90154,31.85889), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.06888,32.85805,34.90154,32.92249), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.06888,32.85805,34.90154,32.92249), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.06888,32.85805,34.90154,32.92249), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.06888,32.85805,34.90154,32.92249), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.61482,32.89716,34.90154,33.24866), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.61482,32.89716,34.90154,33.24866), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.61482,32.89716,34.90154,33.24866), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.61482,32.89716,34.90154,33.24866), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.07277,32.92249,34.90154,32.85805), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.07277,32.92249,34.90154,32.85805), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.07277,32.92249,34.90154,32.85805), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.07277,32.92249,34.90154,32.85805), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.62248,32.98776,34.90154,33.24866), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.62248,32.98776,34.90154,33.24866), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.62248,32.98776,34.90154,33.24866), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.62248,32.98776,34.90154,33.24866), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.59054,33.01776,34.90154,32.71503), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.59054,33.01776,34.90154,32.71503), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.59054,33.01776,34.90154,32.71503), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.59054,33.01776,34.90154,32.71503), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.63943,33.04971,34.90154,32.81448), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.63943,33.04971,34.90154,32.81448), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.63943,33.04971,34.90154,32.81448), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.63943,33.04971,34.90154,32.81448), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.34915,33.06304,35.56666,30.88694), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.34915,33.06304,35.56666,30.88694), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.34915,33.06304,35.56666,30.88694), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.34915,33.06304,35.56666,30.88694), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.09706,33.09211,34.90154,31.83342), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.50471,33.09415,34.90154,31.49783), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.50471,33.09415,34.90154,31.49783), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.50471,33.09415,34.90154,31.49783), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.50471,33.09415,34.90154,31.49783), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.31693,33.10721,34.90154,31.43916), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.31693,33.10721,34.90154,31.43916), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.31693,33.10721,34.90154,31.43916), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.31693,33.10721,34.90154,31.43916), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.61928,33.24866,34.90154,32.98776), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.61928,33.24866,34.90154,32.98776), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.61928,33.24866,34.90154,32.98776), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.61928,33.24866,34.90154,32.98776), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.66971,33.25166,34.90154,33.04971), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.66971,33.25166,34.90154,33.04971), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.66971,33.25166,34.90154,33.04971), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.66971,33.25166,34.90154,33.04971), mapfile, tile_dir, 17, 17, "il-israel")
	render_tiles((35.56666,33.29027,34.90154,32.65192), mapfile, tile_dir, 0, 11, "il-israel")
	render_tiles((35.56666,33.29027,34.90154,32.65192), mapfile, tile_dir, 13, 13, "il-israel")
	render_tiles((35.56666,33.29027,34.90154,32.65192), mapfile, tile_dir, 15, 15, "il-israel")
	render_tiles((35.56666,33.29027,34.90154,32.65192), mapfile, tile_dir, 17, 17, "il-israel")