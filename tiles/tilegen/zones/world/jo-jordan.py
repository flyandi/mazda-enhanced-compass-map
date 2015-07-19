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
    # Region: JO
    # Region Name: Jordan

	render_tiles((36.06998,29.18888,36.06998,32.53888), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.06998,29.18888,36.06998,32.53888), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.06998,29.18888,36.06998,32.53888), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.06998,29.18888,36.06998,32.53888), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.98888,29.20193,36.06998,32.69916), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.98888,29.20193,36.06998,32.69916), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.98888,29.20193,36.06998,32.69916), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.98888,29.20193,36.06998,32.69916), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.73248,29.24249,36.06998,32.74415), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.73248,29.24249,36.06998,32.74415), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.73248,29.24249,36.06998,32.74415), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.73248,29.24249,36.06998,32.74415), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.47581,29.28249,36.06998,31.49783), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.47581,29.28249,36.06998,31.49783), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.47581,29.28249,36.06998,31.49783), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.47581,29.28249,36.06998,31.49783), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.21887,29.32194,36.06998,31.80833), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.21887,29.32194,36.06998,31.80833), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.21887,29.32194,36.06998,31.80833), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.21887,29.32194,36.06998,31.80833), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.96096,29.36243,38.79455,29.36499), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.96096,29.36243,38.79455,29.36499), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.96096,29.36243,38.79455,29.36499), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.96096,29.36243,38.79455,29.36499), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.96082,29.36499,38.79455,29.36243), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.96082,29.36499,38.79455,29.36243), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.96082,29.36499,38.79455,29.36243), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.96082,29.36499,38.79455,29.36243), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.32804,29.37722,36.06998,32.4511), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.32804,29.37722,36.06998,32.4511), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.32804,29.37722,36.06998,32.4511), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.32804,29.37722,36.06998,32.4511), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.00499,29.53083,36.06998,31.98558), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.00499,29.53083,36.06998,31.98558), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.00499,29.53083,36.06998,31.98558), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.00499,29.53083,36.06998,31.98558), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.98141,29.54457,38.79455,29.54611), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.98141,29.54457,38.79455,29.54611), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.98141,29.54457,38.79455,29.54611), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.98141,29.54457,38.79455,29.54611), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.97998,29.54611,38.79455,29.54457), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.97998,29.54611,38.79455,29.54457), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.97998,29.54611,38.79455,29.54457), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.97998,29.54611,38.79455,29.54457), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.96192,29.5511,38.79455,29.36243), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.96192,29.5511,38.79455,29.36243), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.96192,29.5511,38.79455,29.36243), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.96192,29.5511,38.79455,29.36243), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.96554,29.56249,36.06998,31.85889), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.96554,29.56249,36.06998,31.85889), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.96554,29.56249,36.06998,31.85889), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.96554,29.56249,36.06998,31.85889), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.54916,29.57527,36.06998,32.3436), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.54916,29.57527,36.06998,32.3436), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.54916,29.57527,36.06998,32.3436), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.54916,29.57527,36.06998,32.3436), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.9734,29.58552,36.06998,31.82471), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.9734,29.58552,36.06998,31.82471), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.9734,29.58552,36.06998,31.82471), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.9734,29.58552,36.06998,31.82471), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.69859,29.79805,36.06998,32.3436), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.69859,29.79805,36.06998,32.3436), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.69859,29.79805,36.06998,32.3436), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.69859,29.79805,36.06998,32.3436), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.7436,29.86472,38.79455,29.79805), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.7436,29.86472,38.79455,29.79805), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.7436,29.86472,38.79455,29.79805), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.7436,29.86472,38.79455,29.79805), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.00277,29.91221,36.06998,31.50555), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.00277,29.91221,36.06998,31.50555), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.00277,29.91221,36.06998,31.50555), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.00277,29.91221,36.06998,31.50555), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.26221,29.95916,36.06998,31.57333), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.26221,29.95916,36.06998,31.57333), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.26221,29.95916,36.06998,31.57333), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.26221,29.95916,36.06998,31.57333), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.51054,30.01805,36.06998,31.64055), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.51054,30.01805,36.06998,31.64055), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.51054,30.01805,36.06998,31.64055), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.51054,30.01805,36.06998,31.64055), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.16693,30.15166,38.79455,30.41566), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.16693,30.15166,38.79455,30.41566), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.16693,30.15166,38.79455,30.41566), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.16693,30.15166,38.79455,30.41566), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.14054,30.23971,36.06998,31.36194), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.14054,30.23971,36.06998,31.36194), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.14054,30.23971,36.06998,31.36194), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.14054,30.23971,36.06998,31.36194), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.61998,30.24027,36.06998,32.76388), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.61998,30.24027,36.06998,32.76388), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.61998,30.24027,36.06998,32.76388), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.61998,30.24027,36.06998,32.76388), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.66749,30.33638,36.06998,32.76388), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.66749,30.33638,36.06998,32.76388), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.66749,30.33638,36.06998,32.76388), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.66749,30.33638,36.06998,32.76388), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.18193,30.36694,38.79455,30.15166), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.18193,30.36694,38.79455,30.15166), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.18193,30.36694,38.79455,30.15166), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.18193,30.36694,38.79455,30.15166), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.15727,30.41566,38.79455,30.15166), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.15727,30.41566,38.79455,30.15166), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.15727,30.41566,38.79455,30.15166), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.15727,30.41566,38.79455,30.15166), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.93082,30.4686,36.06998,32.90943), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.93082,30.4686,36.06998,32.90943), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.93082,30.4686,36.06998,32.90943), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.93082,30.4686,36.06998,32.90943), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.00138,30.50417,36.06998,31.77305), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.00138,30.50417,36.06998,31.77305), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.00138,30.50417,36.06998,31.77305), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.00138,30.50417,36.06998,31.77305), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.20026,30.56777,38.79455,30.36694), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.20026,30.56777,38.79455,30.36694), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.20026,30.56777,38.79455,30.36694), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.20026,30.56777,38.79455,30.36694), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.28471,30.7186,36.06998,31.43916), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.28471,30.7186,36.06998,31.43916), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.28471,30.7186,36.06998,31.43916), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.28471,30.7186,36.06998,31.43916), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.77693,30.73221,36.06998,31.70721), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.77693,30.73221,36.06998,31.70721), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.77693,30.73221,36.06998,31.70721), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.77693,30.73221,36.06998,31.70721), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.33776,30.88694,36.06998,31.43916), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.33776,30.88694,36.06998,31.43916), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.33776,30.88694,36.06998,31.43916), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.33776,30.88694,36.06998,31.43916), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.39221,30.94333,36.06998,31.49166), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.39221,30.94333,36.06998,31.49166), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.39221,30.94333,36.06998,31.49166), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.39221,30.94333,36.06998,31.49166), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.55193,30.96027,36.06998,31.64055), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.55193,30.96027,36.06998,31.64055), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.55193,30.96027,36.06998,31.64055), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.55193,30.96027,36.06998,31.64055), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.44179,31.08933,36.06998,32.4161), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.44179,31.08933,36.06998,32.4161), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.44179,31.08933,36.06998,32.4161), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.44179,31.08933,36.06998,32.4161), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.4622,31.14944,36.06998,31.38055), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.4622,31.14944,36.06998,31.38055), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.4622,31.14944,36.06998,31.38055), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.4622,31.14944,36.06998,31.38055), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.32638,31.1861,36.06998,32.61804), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.32638,31.1861,36.06998,32.61804), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.32638,31.1861,36.06998,32.61804), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.32638,31.1861,36.06998,32.61804), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.41719,31.23467,36.06998,32.50082), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.41719,31.23467,36.06998,32.50082), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.41719,31.23467,36.06998,32.50082), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.41719,31.23467,36.06998,32.50082), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.39943,31.26833,36.06998,31.49174), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.39943,31.26833,36.06998,31.49174), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.39943,31.26833,36.06998,31.49174), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.39943,31.26833,36.06998,31.49174), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.43188,31.32319,36.06998,32.4161), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.43188,31.32319,36.06998,32.4161), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.43188,31.32319,36.06998,32.4161), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.43188,31.32319,36.06998,32.4161), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.1386,31.36194,38.79455,30.23971), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.1386,31.36194,38.79455,30.23971), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.1386,31.36194,38.79455,30.23971), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.1386,31.36194,38.79455,30.23971), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.90415,31.36361,36.06998,31.40471), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.90415,31.36361,36.06998,31.40471), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.90415,31.36361,36.06998,31.40471), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.90415,31.36361,36.06998,31.40471), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.46582,31.38055,38.79455,31.14944), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.46582,31.38055,38.79455,31.14944), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.46582,31.38055,38.79455,31.14944), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.46582,31.38055,38.79455,31.14944), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.88443,31.40471,36.06998,31.36361), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.88443,31.40471,36.06998,31.36361), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.88443,31.40471,36.06998,31.36361), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.88443,31.40471,36.06998,31.36361), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.09804,31.41333,36.06998,32.47027), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.09804,31.41333,36.06998,32.47027), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.09804,31.41333,36.06998,32.47027), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.09804,31.41333,36.06998,32.47027), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.32027,31.43916,38.79455,30.88694), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.32027,31.43916,38.79455,30.88694), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.32027,31.43916,38.79455,30.88694), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.32027,31.43916,38.79455,30.88694), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.39471,31.49166,36.06998,31.49174), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.39471,31.49166,36.06998,31.49174), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.39471,31.49166,36.06998,31.49174), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.39471,31.49166,36.06998,31.49174), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.39592,31.49174,36.06998,31.49166), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.39592,31.49174,36.06998,31.49166), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.39592,31.49174,36.06998,31.49166), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.39592,31.49174,36.06998,31.49166), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.47865,31.49783,38.79455,29.28249), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.47865,31.49783,38.79455,29.28249), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.47865,31.49783,38.79455,29.28249), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.47865,31.49783,38.79455,29.28249), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.00526,31.50555,38.79455,29.91221), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.00526,31.50555,38.79455,29.91221), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.00526,31.50555,38.79455,29.91221), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.00526,31.50555,38.79455,29.91221), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.26943,31.57333,38.79455,29.95916), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.26943,31.57333,38.79455,29.95916), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.26943,31.57333,38.79455,29.95916), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.26943,31.57333,38.79455,29.95916), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.9536,31.59472,38.79455,29.36499), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.9536,31.59472,38.79455,29.36499), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.9536,31.59472,38.79455,29.36499), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.9536,31.59472,38.79455,29.36499), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.53387,31.64055,38.79455,30.96027), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.53387,31.64055,38.79455,30.96027), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.53387,31.64055,38.79455,30.96027), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.53387,31.64055,38.79455,30.96027), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.02026,31.65805,36.06998,31.88388), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.02026,31.65805,36.06998,31.88388), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.02026,31.65805,36.06998,31.88388), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.02026,31.65805,36.06998,31.88388), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.79887,31.70721,38.79455,30.73221), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.79887,31.70721,38.79455,30.73221), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.79887,31.70721,38.79455,30.73221), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.79887,31.70721,38.79455,30.73221), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.22637,31.74777,36.06998,32.55193), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.22637,31.74777,36.06998,32.55193), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.22637,31.74777,36.06998,32.55193), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.22637,31.74777,36.06998,32.55193), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.06248,31.77305,38.79455,30.50417), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.06248,31.77305,38.79455,30.50417), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.06248,31.77305,38.79455,30.50417), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.06248,31.77305,38.79455,30.50417), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.21998,31.80833,38.79455,29.32194), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.21998,31.80833,38.79455,29.32194), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.21998,31.80833,38.79455,29.32194), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.21998,31.80833,38.79455,29.32194), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.97109,31.82471,38.79455,29.58552), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.97109,31.82471,38.79455,29.58552), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.97109,31.82471,38.79455,29.58552), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.97109,31.82471,38.79455,29.58552), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.10892,31.83342,36.06998,32.47054), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.10892,31.83342,36.06998,32.47054), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.10892,31.83342,36.06998,32.47054), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.10892,31.83342,36.06998,32.47054), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.32832,31.83944,36.06998,33.19748), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.32832,31.83944,36.06998,33.19748), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.32832,31.83944,36.06998,33.19748), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.32832,31.83944,36.06998,33.19748), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.96332,31.85889,38.79455,29.5511), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.96332,31.85889,38.79455,29.5511), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.96332,31.85889,38.79455,29.5511), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.96332,31.85889,38.79455,29.5511), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.03054,31.88388,36.06998,31.65805), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.03054,31.88388,36.06998,31.65805), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.03054,31.88388,36.06998,31.65805), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.03054,31.88388,36.06998,31.65805), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.59443,31.90527,36.06998,33.34054), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.59443,31.90527,36.06998,33.34054), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.59443,31.90527,36.06998,33.34054), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.59443,31.90527,36.06998,33.34054), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.86082,31.97055,36.06998,33.14471), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.86082,31.97055,36.06998,33.14471), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.86082,31.97055,36.06998,33.14471), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.86082,31.97055,36.06998,33.14471), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.00085,31.98558,38.79455,29.53083), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.00499,32.00555,36.06998,32.91776), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.00499,32.00555,36.06998,32.91776), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.00499,32.00555,36.06998,32.91776), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.00499,32.00555,36.06998,32.91776), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((34.98582,32.13248,38.79455,29.54457), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((34.98582,32.13248,38.79455,29.54457), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((34.98582,32.13248,38.79455,29.54457), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((34.98582,32.13248,38.79455,29.54457), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.27582,32.21665,36.06998,32.23734), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.27582,32.21665,36.06998,32.23734), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.27582,32.21665,36.06998,32.23734), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.27582,32.21665,36.06998,32.23734), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.30308,32.23734,36.06998,32.21665), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.30308,32.23734,36.06998,32.21665), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.30308,32.23734,36.06998,32.21665), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.30308,32.23734,36.06998,32.21665), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.83776,32.3136,38.79455,29.86472), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.83776,32.3136,38.79455,29.86472), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.83776,32.3136,38.79455,29.86472), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.83776,32.3136,38.79455,29.86472), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.67471,32.3436,38.79455,29.79805), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.67471,32.3436,38.79455,29.79805), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.67471,32.3436,38.79455,29.79805), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.67471,32.3436,38.79455,29.79805), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.40026,32.38194,38.79455,29.37722), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.40026,32.38194,38.79455,29.37722), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.40026,32.38194,38.79455,29.37722), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.40026,32.38194,38.79455,29.37722), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.55293,32.39379,36.06998,32.52138), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.55293,32.39379,36.06998,32.52138), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.55293,32.39379,36.06998,32.52138), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.55293,32.39379,36.06998,32.52138), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.43221,32.4161,36.06998,31.32319), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.43221,32.4161,36.06998,31.32319), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.43221,32.4161,36.06998,31.32319), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.43221,32.4161,36.06998,31.32319), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.56332,32.44109,36.06998,32.65192), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.56332,32.44109,36.06998,32.65192), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.56332,32.44109,36.06998,32.65192), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.56332,32.44109,36.06998,32.65192), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.32499,32.4511,38.79455,29.37722), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.32499,32.4511,38.79455,29.37722), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.32499,32.4511,38.79455,29.37722), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.32499,32.4511,38.79455,29.37722), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.20277,32.46443,36.06998,32.21665), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.20277,32.46443,36.06998,32.21665), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.20277,32.46443,36.06998,32.21665), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.20277,32.46443,36.06998,32.21665), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.09859,32.47027,36.06998,31.41333), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.09859,32.47027,36.06998,31.41333), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.09859,32.47027,36.06998,31.41333), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.09859,32.47027,36.06998,31.41333), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.07999,32.47054,36.06998,31.83342), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.07999,32.47054,36.06998,31.83342), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.07999,32.47054,36.06998,31.83342), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.07999,32.47054,36.06998,31.83342), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.4122,32.50082,38.79455,31.23467), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.4122,32.50082,38.79455,31.23467), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.4122,32.50082,38.79455,31.23467), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.4122,32.50082,38.79455,31.23467), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.55415,32.52138,36.06998,32.39379), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.55415,32.52138,36.06998,32.39379), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.55415,32.52138,36.06998,32.39379), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.55415,32.52138,36.06998,32.39379), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((36.08027,32.53888,38.79455,29.18888), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((36.08027,32.53888,38.79455,29.18888), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((36.08027,32.53888,38.79455,29.18888), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((36.08027,32.53888,38.79455,29.18888), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.22276,32.55193,36.06998,31.80833), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.22276,32.55193,36.06998,31.80833), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.22276,32.55193,36.06998,31.80833), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.22276,32.55193,36.06998,31.80833), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.36749,32.61804,38.79455,31.1861), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.36749,32.61804,38.79455,31.1861), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.36749,32.61804,38.79455,31.1861), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.36749,32.61804,38.79455,31.1861), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.5636,32.65192,36.06998,32.44109), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.5636,32.65192,36.06998,32.44109), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.5636,32.65192,36.06998,32.44109), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.5636,32.65192,36.06998,32.44109), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.63345,32.68651,36.06998,32.65192), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.63345,32.68651,36.06998,32.65192), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.63345,32.68651,36.06998,32.65192), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.63345,32.68651,36.06998,32.65192), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.10221,32.69165,36.06998,32.00555), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.10221,32.69165,36.06998,32.00555), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.10221,32.69165,36.06998,32.00555), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.10221,32.69165,36.06998,32.00555), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.93998,32.69916,38.79455,29.20193), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.93998,32.69916,38.79455,29.20193), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.93998,32.69916,38.79455,29.20193), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.93998,32.69916,38.79455,29.20193), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((35.7936,32.74415,38.79455,29.24249), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((35.7936,32.74415,38.79455,29.24249), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((35.7936,32.74415,38.79455,29.24249), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((35.7936,32.74415,38.79455,29.24249), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.6361,32.76388,38.79455,30.24027), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.6361,32.76388,38.79455,30.24027), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.6361,32.76388,38.79455,30.24027), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.6361,32.76388,38.79455,30.24027), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((37.90637,32.90943,38.79455,30.4686), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((37.90637,32.90943,38.79455,30.4686), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((37.90637,32.90943,38.79455,30.4686), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((37.90637,32.90943,38.79455,30.4686), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((39.00138,32.91776,36.06998,32.00555), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((39.00138,32.91776,36.06998,32.00555), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((39.00138,32.91776,36.06998,32.00555), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((39.00138,32.91776,36.06998,32.00555), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.17776,33.05387,36.06998,31.77305), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.17776,33.05387,36.06998,31.77305), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.17776,33.05387,36.06998,31.77305), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.17776,33.05387,36.06998,31.77305), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.89971,33.14471,36.06998,31.97055), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.89971,33.14471,36.06998,31.97055), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.89971,33.14471,36.06998,31.97055), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.89971,33.14471,36.06998,31.97055), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.44971,33.19748,36.06998,31.83944), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.44971,33.19748,36.06998,31.83944), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.44971,33.19748,36.06998,31.83944), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.44971,33.19748,36.06998,31.83944), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.72276,33.34054,36.06998,33.37723), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.72276,33.34054,36.06998,33.37723), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.72276,33.34054,36.06998,33.37723), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.72276,33.34054,36.06998,33.37723), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.79749,33.37165,36.06998,33.37723), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.79749,33.37165,36.06998,33.37723), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.79749,33.37165,36.06998,33.37723), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.79749,33.37165,36.06998,33.37723), mapfile, tile_dir, 17, 17, "jo-jordan")
	render_tiles((38.79455,33.37723,36.06998,33.37165), mapfile, tile_dir, 0, 11, "jo-jordan")
	render_tiles((38.79455,33.37723,36.06998,33.37165), mapfile, tile_dir, 13, 13, "jo-jordan")
	render_tiles((38.79455,33.37723,36.06998,33.37165), mapfile, tile_dir, 15, 15, "jo-jordan")
	render_tiles((38.79455,33.37723,36.06998,33.37165), mapfile, tile_dir, 17, 17, "jo-jordan")