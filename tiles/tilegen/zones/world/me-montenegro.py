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
    # Region: ME
    # Region Name: Montenegro

	render_tiles((19.36846,41.84932,19.0117,41.99943), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.36846,41.84932,19.0117,41.99943), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.36846,41.84932,19.0117,41.99943), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.36846,41.84932,19.0117,41.99943), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.14027,41.9936,19.0117,42.04332), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.14027,41.9936,19.0117,42.04332), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.14027,41.9936,19.0117,42.04332), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.14027,41.9936,19.0117,42.04332), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.3811,41.99943,19.0117,41.84932), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.3811,41.99943,19.0117,41.84932), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.3811,41.99943,19.0117,41.84932), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.3811,41.99943,19.0117,41.84932), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.13861,42.04332,19.0117,41.9936), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.13861,42.04332,19.0117,41.9936), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.13861,42.04332,19.0117,41.9936), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.13861,42.04332,19.0117,41.9936), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.39509,42.08789,19.0117,42.31707), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.39509,42.08789,19.0117,42.31707), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.39509,42.08789,19.0117,42.31707), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.39509,42.08789,19.0117,42.31707), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.39833,42.10832,19.0117,42.31707), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.39833,42.10832,19.0117,42.31707), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.39833,42.10832,19.0117,42.31707), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.39833,42.10832,19.0117,42.31707), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.28249,42.18554,19.0117,41.84932), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.28249,42.18554,19.0117,41.84932), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.28249,42.18554,19.0117,41.84932), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.28249,42.18554,19.0117,41.84932), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.89166,42.26276,19.36846,43.3569), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.89166,42.26276,19.36846,43.3569), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.89166,42.26276,19.36846,43.3569), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.89166,42.26276,19.36846,43.3569), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.79333,42.28138,19.36846,43.3489), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.79333,42.28138,19.36846,43.3489), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.79333,42.28138,19.36846,43.3489), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.79333,42.28138,19.36846,43.3489), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.39732,42.31707,19.0117,42.10832), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.39732,42.31707,19.0117,42.10832), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.39732,42.31707,19.0117,42.10832), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.39732,42.31707,19.0117,42.10832), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.69249,42.35082,19.0117,42.47832), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.69249,42.35082,19.0117,42.47832), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.69249,42.35082,19.0117,42.47832), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.69249,42.35082,19.0117,42.47832), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.68416,42.38277,19.0117,42.47832), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.68416,42.38277,19.0117,42.47832), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.68416,42.38277,19.0117,42.47832), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.68416,42.38277,19.0117,42.47832), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.7025,42.39332,19.36846,43.2525), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.7025,42.39332,19.36846,43.2525), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.7025,42.39332,19.36846,43.2525), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.7025,42.39332,19.36846,43.2525), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.46971,42.39999,19.0117,42.10832), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.46971,42.39999,19.0117,42.10832), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.46971,42.39999,19.0117,42.10832), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.46971,42.39999,19.0117,42.10832), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.56583,42.40276,19.36846,42.7122), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.56583,42.40276,19.36846,42.7122), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.56583,42.40276,19.36846,42.7122), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.56583,42.40276,19.36846,42.7122), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.55611,42.43138,19.0117,42.6211), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.55611,42.43138,19.0117,42.6211), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.55611,42.43138,19.0117,42.6211), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.55611,42.43138,19.0117,42.6211), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.61305,42.43777,19.36846,43.1392), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.61305,42.43777,19.36846,43.1392), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.61305,42.43777,19.36846,43.1392), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.61305,42.43777,19.36846,43.1392), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.51026,42.4494,19.0117,42.45471), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.51026,42.4494,19.0117,42.45471), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.51026,42.4494,19.0117,42.45471), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.51026,42.4494,19.0117,42.45471), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.50861,42.45471,19.0117,42.4494), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.50861,42.45471,19.0117,42.4494), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.50861,42.45471,19.0117,42.4494), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.50861,42.45471,19.0117,42.4494), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.83027,42.4697,19.0117,42.63971), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.83027,42.4697,19.0117,42.63971), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.83027,42.4697,19.0117,42.63971), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.83027,42.4697,19.0117,42.63971), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.68888,42.47832,19.0117,42.35082), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.68888,42.47832,19.0117,42.35082), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.68888,42.47832,19.0117,42.35082), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.68888,42.47832,19.0117,42.35082), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.4603,42.4869,19.0117,42.56452), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.4603,42.4869,19.0117,42.56452), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.4603,42.4869,19.0117,42.56452), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.4603,42.4869,19.0117,42.56452), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.74666,42.54305,19.0117,42.63971), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.74666,42.54305,19.0117,42.63971), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.74666,42.54305,19.0117,42.63971), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.74666,42.54305,19.0117,42.63971), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.07272,42.55844,19.36846,42.81409), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.07272,42.55844,19.36846,42.81409), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.07272,42.55844,19.36846,42.81409), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.07272,42.55844,19.36846,42.81409), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.06416,42.56304,19.0117,42.55844), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.06416,42.56304,19.0117,42.55844), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.06416,42.56304,19.0117,42.55844), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.06416,42.56304,19.0117,42.55844), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.45529,42.56452,19.36846,42.8286), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.45529,42.56452,19.36846,42.8286), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.45529,42.56452,19.36846,42.8286), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.45529,42.56452,19.36846,42.8286), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.63583,42.60609,19.0117,42.66137), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.63583,42.60609,19.0117,42.66137), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.63583,42.60609,19.0117,42.66137), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.63583,42.60609,19.0117,42.66137), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.5539,42.6211,19.0117,42.43138), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.5539,42.6211,19.0117,42.43138), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.5539,42.6211,19.0117,42.43138), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.5539,42.6211,19.0117,42.43138), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.75583,42.63971,19.0117,42.54305), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.75583,42.63971,19.0117,42.54305), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.75583,42.63971,19.0117,42.54305), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.75583,42.63971,19.0117,42.54305), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.5775,42.655,19.0117,42.40276), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.5775,42.655,19.0117,42.40276), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.5775,42.655,19.0117,42.40276), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.5775,42.655,19.0117,42.40276), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.71749,42.66137,19.0117,42.54305), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.71749,42.66137,19.0117,42.54305), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.71749,42.66137,19.0117,42.54305), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.71749,42.66137,19.0117,42.54305), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.5603,42.7122,19.0117,42.43138), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.5603,42.7122,19.0117,42.43138), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.5603,42.7122,19.0117,42.43138), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.5603,42.7122,19.0117,42.43138), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.02896,42.72105,19.0117,42.56304), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.02896,42.72105,19.0117,42.56304), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.02896,42.72105,19.0117,42.56304), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.02896,42.72105,19.0117,42.56304), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.5158,42.7311,19.0117,42.4494), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.5158,42.7311,19.0117,42.4494), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.5158,42.7311,19.0117,42.4494), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.5158,42.7311,19.0117,42.4494), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.07548,42.81409,19.0117,42.55844), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.07548,42.81409,19.0117,42.55844), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.07548,42.81409,19.0117,42.55844), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.07548,42.81409,19.0117,42.55844), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.4547,42.8286,19.0117,42.56452), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.4547,42.8286,19.0117,42.56452), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.4547,42.8286,19.0117,42.56452), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.4547,42.8286,19.0117,42.56452), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.23055,42.8451,19.36846,42.93815), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.23055,42.8451,19.36846,42.93815), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.23055,42.8451,19.36846,42.93815), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.23055,42.8451,19.36846,42.93815), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.98244,42.86061,19.36846,42.72105), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.98244,42.86061,19.36846,42.72105), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.98244,42.86061,19.36846,42.72105), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.98244,42.86061,19.36846,42.72105), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.4908,42.8617,19.36846,42.9719), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.4908,42.8617,19.36846,42.9719), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.4908,42.8617,19.36846,42.9719), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.4908,42.8617,19.36846,42.9719), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((20.29258,42.93815,19.36846,42.8451), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((20.29258,42.93815,19.36846,42.8451), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((20.29258,42.93815,19.36846,42.8451), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((20.29258,42.93815,19.36846,42.8451), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.4839,42.9719,19.36846,42.8617), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.4839,42.9719,19.36846,42.8617), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.4839,42.9719,19.36846,42.8617), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.4839,42.9719,19.36846,42.8617), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.5356,43.0172,19.0117,42.6211), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.5356,43.0172,19.0117,42.6211), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.5356,43.0172,19.0117,42.6211), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.5356,43.0172,19.0117,42.6211), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.65,43.0442,19.36846,43.1392), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.65,43.0442,19.36846,43.1392), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.65,43.0442,19.36846,43.1392), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.65,43.0442,19.36846,43.1392), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.6592,43.0803,19.36846,43.0442), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.6592,43.0803,19.36846,43.0442), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.6592,43.0803,19.36846,43.0442), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.6592,43.0803,19.36846,43.0442), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.6439,43.1392,19.36846,43.0442), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.6439,43.1392,19.36846,43.0442), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.6439,43.1392,19.36846,43.0442), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.6439,43.1392,19.36846,43.0442), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.7075,43.2144,19.0117,42.39332), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.7075,43.2144,19.0117,42.39332), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.7075,43.2144,19.0117,42.39332), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.7075,43.2144,19.0117,42.39332), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0481,43.2325,19.36846,43.5356), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0481,43.2325,19.36846,43.5356), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0481,43.2325,19.36846,43.5356), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0481,43.2325,19.36846,43.5356), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0167,43.2392,19.36846,43.3919), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0167,43.2392,19.36846,43.3919), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0167,43.2392,19.36846,43.3919), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0167,43.2392,19.36846,43.3919), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0722,43.2481,19.36846,43.5086), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0722,43.2481,19.36846,43.5086), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0722,43.2481,19.36846,43.5086), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0722,43.2481,19.36846,43.5086), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.6978,43.2525,19.0117,42.39332), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.6978,43.2525,19.0117,42.39332), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.6978,43.2525,19.0117,42.39332), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.6978,43.2525,19.0117,42.39332), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9814,43.2842,19.36846,43.5222), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9814,43.2842,19.36846,43.5222), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9814,43.2842,19.36846,43.5222), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9814,43.2842,19.36846,43.5222), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9542,43.2933,19.36846,43.4544), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9542,43.2933,19.36846,43.4544), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9542,43.2933,19.36846,43.4544), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9542,43.2933,19.36846,43.4544), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0911,43.3164,19.36846,43.2481), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0911,43.3164,19.36846,43.2481), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0911,43.3164,19.36846,43.2481), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0911,43.3164,19.36846,43.2481), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9603,43.3228,19.36846,43.4544), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9603,43.3228,19.36846,43.4544), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9603,43.3228,19.36846,43.4544), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9603,43.3228,19.36846,43.4544), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.8558,43.3328,19.36846,43.3489), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.8558,43.3328,19.36846,43.3489), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.8558,43.3328,19.36846,43.3489), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.8558,43.3328,19.36846,43.3489), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.8475,43.3489,19.36846,43.3328), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.8475,43.3489,19.36846,43.3328), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.8475,43.3489,19.36846,43.3328), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.8475,43.3489,19.36846,43.3328), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9194,43.3569,19.0117,42.26276), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9194,43.3569,19.0117,42.26276), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9194,43.3569,19.0117,42.26276), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9194,43.3569,19.0117,42.26276), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0206,43.3919,19.36846,43.4356), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0206,43.3919,19.36846,43.4356), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0206,43.3919,19.36846,43.4356), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0206,43.3919,19.36846,43.4356), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0231,43.4356,19.36846,43.3919), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0231,43.4356,19.36846,43.3919), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0231,43.4356,19.36846,43.3919), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0231,43.4356,19.36846,43.3919), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9567,43.4544,19.36846,43.2933), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9567,43.4544,19.36846,43.2933), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9567,43.4544,19.36846,43.2933), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9567,43.4544,19.36846,43.2933), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.07,43.5086,19.36846,43.2481), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9494,43.5094,19.36846,43.2933), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9494,43.5094,19.36846,43.2933), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9494,43.5094,19.36846,43.2933), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9494,43.5094,19.36846,43.2933), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((18.9869,43.5222,19.36846,43.2842), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((18.9869,43.5222,19.36846,43.2842), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((18.9869,43.5222,19.36846,43.2842), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((18.9869,43.5222,19.36846,43.2842), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0539,43.5356,19.36846,43.2325), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0539,43.5356,19.36846,43.2325), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0539,43.5356,19.36846,43.2325), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0539,43.5356,19.36846,43.2325), mapfile, tile_dir, 17, 17, "me-montenegro")
	render_tiles((19.0117,43.5561,19.36846,43.2392), mapfile, tile_dir, 0, 11, "me-montenegro")
	render_tiles((19.0117,43.5561,19.36846,43.2392), mapfile, tile_dir, 13, 13, "me-montenegro")
	render_tiles((19.0117,43.5561,19.36846,43.2392), mapfile, tile_dir, 15, 15, "me-montenegro")
	render_tiles((19.0117,43.5561,19.36846,43.2392), mapfile, tile_dir, 17, 17, "me-montenegro")