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
    # Region: EG
    # Region Name: Egypt

	render_tiles((26.91444,21.99666,27.55194,21.99777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.55194,21.99777,34.10059,21.99802), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.10059,21.99802,29.69083,21.99805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.69083,21.99805,34.10059,21.99802), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((26.2761,21.99833,31.08027,21.9986), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.45388,21.99833,31.08027,21.9986), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.08027,21.9986,26.2761,21.99833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.6311,21.99916,33.85082,21.99944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.27749,21.99916,33.85082,21.99944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.85082,21.99944,25.6311,21.99916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.81527,22,30.38499,22.00027), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.38499,22.00027,28.81527,22), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.8311,22.00083,25.00355,22.0013), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.00355,22.0013,32.8311,22.00083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.18349,22.0013,32.8311,22.00083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.0002,22.0013,32.8311,22.00083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.17833,22.00249,32.14443,22.00333), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.14443,22.00333,28.17833,22.00249), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.51583,22.17055,34.17165,22.19666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.17165,22.19666,31.38916,22.20666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.38916,22.20666,31.48555,22.21638), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.48555,22.21638,31.38916,22.20666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.4411,22.23221,31.48555,22.21638), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.71387,22.28888,31.4411,22.23221), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.00111,22.47582,34.71387,22.28888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.22693,22.77083,34.96915,22.8486), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.96915,22.8486,35.22693,22.77083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.62459,23.128,35.56805,23.24361), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.56805,23.24361,35.62459,23.128), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.99944,23.82972,35.81304,23.91611), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.81304,23.91611,35.48305,23.93833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.48305,23.93833,35.5586,23.95138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.5586,23.95138,35.48305,23.93833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.53665,23.98583,35.71971,24.00916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.71971,24.00916,35.62388,24.01805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.62388,24.01805,35.71971,24.00916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.20971,24.43833,35.08166,24.73138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((35.08166,24.73138,34.99888,24.80833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.99888,24.80833,34.99582,24.86722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.99582,24.86722,34.99888,24.80833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.88026,25.0961,24.99777,25.18), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.99777,25.18,34.88026,25.0961), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.62527,25.58111,34.37444,25.94916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.37444,25.94916,34.62527,25.58111), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.06332,26.52888,24.99944,26.53944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.99944,26.53944,34.06332,26.52888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.93526,26.65944,24.99944,26.53944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.94666,26.78722,33.97221,26.84222), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.97221,26.84222,34.0011,26.86194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.0011,26.86194,33.97221,26.84222), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.93138,26.9711,34.0011,26.86194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.83554,27.11388,33.83249,27.25166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.83249,27.25166,33.69138,27.33611), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.69138,27.33611,33.83249,27.25166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.65887,27.44028,33.55527,27.53472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.55527,27.53472,33.55832,27.58611), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.55832,27.58611,33.55527,27.53472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.49471,27.64388,33.53027,27.67805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.53027,27.67805,33.5486,27.685), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.5486,27.685,33.53027,27.67805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.25804,27.7325,33.55916,27.75444), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.55916,27.75444,34.25804,27.7325), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.18943,27.78722,34.09138,27.79277), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.09138,27.79277,34.18943,27.78722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.47694,27.80111,34.2661,27.80861), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.2661,27.80861,33.55832,27.81583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.55832,27.81583,34.2661,27.80861), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.47694,27.83472,33.55832,27.81583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.00999,27.85944,33.56082,27.86861), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.56082,27.86861,34.00999,27.85944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.33499,27.89833,25.00222,27.89888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.00222,27.89888,34.33499,27.89833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.87276,27.94611,25.00222,27.89888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.44193,27.99694,33.45693,28.00138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.45693,28.00138,34.44193,27.99694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.75388,28.05138,33.33193,28.07499), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.33193,28.07499,33.75388,28.05138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.45304,28.16166,33.65415,28.16722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.65415,28.16722,34.45304,28.16166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.2136,28.18555,33.65415,28.16722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.56443,28.29416,33.51138,28.31749), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.51138,28.31749,33.11027,28.31805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.41082,28.31749,33.11027,28.31805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.11027,28.31805,33.51138,28.31749), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.37721,28.42056,33.01804,28.45472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.01804,28.45472,34.48305,28.46999), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.48305,28.46999,34.51276,28.47555), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.51276,28.47555,34.48305,28.46999), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.26499,28.5386,34.51755,28.54021), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.51755,28.54021,33.26499,28.5386), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.88776,28.56472,33.22027,28.58555), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.22027,28.58555,32.88776,28.56472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.60221,28.68638,32.83166,28.72916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.83166,28.72916,33.21693,28.73027), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.21693,28.73027,32.83166,28.72916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.75045,28.80396,33.1736,28.82583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.1736,28.82583,34.62332,28.84305), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.62332,28.84305,33.1736,28.82583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.64638,28.89166,33.18054,28.91861), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.18054,28.91861,32.65443,28.94221), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.65443,28.94221,34.63915,28.95944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.63915,28.95944,32.65443,28.94221), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.68193,28.98388,34.63915,28.95944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.15137,29.01694,32.62638,29.02277), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.62638,29.02277,33.15137,29.01694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.02832,29.12805,34.69027,29.13722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.69027,29.13722,33.02832,29.12805), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.64665,29.15277,34.69027,29.13722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.8911,29.23249,24.99777,29.24888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.99777,29.24888,32.8911,29.23249), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.74221,29.29416,32.60277,29.30722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.60277,29.30722,34.74221,29.29416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.82027,29.37666,32.56832,29.3811), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.56832,29.3811,32.82027,29.37666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.82777,29.42333,32.56832,29.3811), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.72832,29.47888,32.43804,29.49194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.43804,29.49194,34.90154,29.49379), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.90154,29.49379,32.43804,29.49194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.8761,29.51194,34.90154,29.49379), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.71304,29.57249,34.86582,29.58416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.86582,29.58416,32.34304,29.59027), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.34304,29.59027,34.86582,29.58416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.88694,29.66388,34.86221,29.67388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.86221,29.67388,24.88694,29.66388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.69582,29.72083,32.40415,29.74194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.40415,29.74194,32.69582,29.72083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.81721,29.77416,34.83332,29.77777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.83332,29.77777,24.81721,29.77416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.60638,29.82583,32.49971,29.86388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.49971,29.86388,24.83138,29.88666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.83138,29.88666,34.78915,29.89083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.78915,29.89083,24.83138,29.88666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.45943,29.89555,34.78915,29.89083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.58007,29.95192,32.50721,29.95277), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.50721,29.95277,32.58007,29.95192), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.57526,29.98555,34.75471,30), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.75471,30,32.57526,29.98555), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.7136,30.11277,24.70638,30.1561), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.70638,30.1561,34.7136,30.11277), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.66776,30.22583,24.72694,30.23388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.72694,30.23388,34.66776,30.22583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.62248,30.33833,34.5436,30.42999), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.5436,30.42999,24.9286,30.5111), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.9286,30.5111,34.52054,30.5411), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.52054,30.5411,24.9286,30.5111), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.5011,30.65305,34.52054,30.5411), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.45054,30.76583,25.0186,30.79305), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.0186,30.79305,34.45054,30.76583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.02786,30.82726,29.17388,30.82888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.17388,30.82888,29.02786,30.82726), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.39415,30.87888,29.36444,30.88916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.36444,30.88916,34.39415,30.87888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.83666,30.90722,29.36444,30.88916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.53388,30.96749,28.75527,30.97499), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.75527,30.97499,29.53388,30.96749), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.35221,30.99166,28.75527,30.97499), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.13888,31.04333,32.76971,31.04361), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.76971,31.04361,33.13888,31.04333), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.10777,31.04638,32.76971,31.04361), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.67304,31.05,32.99443,31.05333), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.99443,31.05333,32.67304,31.05), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.62166,31.05777,32.99443,31.05333), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.68194,31.06416,28.21305,31.06583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.21305,31.06583,29.68194,31.06416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.19666,31.07166,28.21305,31.06583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.13527,31.0775,32.8361,31.08166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.8361,31.08166,28.40972,31.08472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((28.40972,31.08472,32.8361,31.08166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.91582,31.09916,33.30693,31.09944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.30693,31.09944,32.91582,31.09916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.91471,31.09944,32.91582,31.09916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.20388,31.10249,32.51166,31.10416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.51166,31.10416,34.30971,31.10444), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.30971,31.10444,32.51166,31.10416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.95443,31.10472,34.30971,31.10444), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.82999,31.10666,32.95443,31.10472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.02583,31.11138,32.8561,31.11222), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.8561,31.11222,33.02583,31.11138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.66749,31.11944,32.8561,31.11222), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.28499,31.12777,32.03443,31.12833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.03443,31.12833,32.28499,31.12777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.13943,31.1361,33.37832,31.14111), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.37832,31.14111,33.45249,31.14305), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.45249,31.14305,33.37832,31.14111), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.82694,31.14555,33.45249,31.14305), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.03027,31.15472,24.86888,31.16194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.86888,31.16194,33.87777,31.16694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.87777,31.16694,24.86888,31.16194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.29249,31.17277,33.87777,31.16694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((33.12498,31.19194,31.93944,31.20194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.93944,31.20194,29.86944,31.2025), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((29.86944,31.2025,31.93944,31.20194), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.53249,31.20777,29.86944,31.2025), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.26708,31.21626,32.01832,31.21666), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.01832,31.21666,34.26708,31.21626), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.38666,31.22138,30.14305,31.22166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.14305,31.22166,32.38666,31.22138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.18527,31.22416,30.14305,31.22166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.42777,31.22777,31.90027,31.22944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.90027,31.22944,27.42777,31.22777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.06332,31.23361,27.86138,31.23388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.86138,31.23388,34.06332,31.23361), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.28999,31.23694,27.86138,31.23388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.27388,31.2536,32.3161,31.25916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.3161,31.25916,32.27388,31.2536), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.17138,31.26888,32.3211,31.27138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.21999,31.26888,32.3211,31.27138), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.3211,31.27138,30.17138,31.26888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.79138,31.2836,30.01027,31.28944), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.01027,31.28944,31.79138,31.2836), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.07555,31.29777,32.17443,31.30305), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.17443,31.30305,30.07555,31.29777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.21748,31.32249,30.30305,31.33667), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((34.21748,31.32249,30.30305,31.33667), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.30305,31.33667,27.34499,31.34388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.34499,31.34388,32.0836,31.3475), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.0836,31.3475,27.34499,31.34388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.80388,31.35583,32.0836,31.3475), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.34333,31.37055,24.87471,31.38388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.87471,31.38388,30.57027,31.38583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.57027,31.38583,30.72944,31.38638), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.72944,31.38638,30.57027,31.38583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((32.02749,31.39999,30.55083,31.41027), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.55083,31.41027,31.88163,31.41142), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.88163,31.41142,30.55083,31.41027), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((27.00638,31.41444,31.88163,31.41142), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.98305,31.41972,30.67722,31.42222), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.67722,31.42222,31.98305,31.41972), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.82583,31.42694,24.9486,31.42888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((24.9486,31.42888,31.87388,31.43055), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.87388,31.43055,24.9486,31.42888), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.3511,31.43694,26.84194,31.43777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((26.84194,31.43777,30.3511,31.43694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.78749,31.44138,26.84194,31.43777), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.95833,31.44555,26.96472,31.44833), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((26.96472,31.44833,30.95833,31.44555), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.50555,31.45416,30.46527,31.45527), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.46527,31.45527,31.50555,31.45416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.36777,31.46611,31.71861,31.47055), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.71861,31.47055,30.36777,31.46611), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.71472,31.47916,31.10694,31.48694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.10694,31.48694,30.71472,31.47916), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.66972,31.49472,25.32388,31.50083), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.32388,31.50083,30.66972,31.49472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.36027,31.50777,26.44305,31.51416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((26.44305,31.51416,31.93139,31.51527), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.02111,31.51416,31.93139,31.51527), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.93139,31.51527,26.44305,31.51416), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.13027,31.51638,31.94055,31.51694), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.94055,31.51694,31.13027,31.51638), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.88749,31.52638,25.1786,31.53388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.1786,31.53388,31.33638,31.53472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.33638,31.53472,25.1786,31.53388), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.89861,31.5386,31.33638,31.53472), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.89111,31.55055,30.86277,31.55249), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.86277,31.55249,30.89111,31.55055), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.06972,31.58166,30.97972,31.58583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((30.97972,31.58583,25.06972,31.58166), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((31.14194,31.59583,25.78027,31.59722), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.78027,31.59722,31.14194,31.59583), mapfile, tile_dir, 0, 11, "eg-egypt")
	render_tiles((25.13423,31.63933,25.78027,31.59722), mapfile, tile_dir, 0, 11, "eg-egypt")