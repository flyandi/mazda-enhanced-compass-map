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
    # Region: NP
    # Region Name: Nepal

	render_tiles((87.32803,26.34777,88.00331,26.36721), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.00331,26.36721,87.59303,26.37944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.59303,26.37944,88.00331,26.36721), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.13359,26.41333,87.39053,26.41471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.39053,26.41471,87.13359,26.41333), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.7258,26.42138,87.39053,26.41471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.08942,26.43111,87.47691,26.43555), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.47691,26.43555,88.08942,26.43111), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.92191,26.4486,87.47691,26.43555), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.99442,26.52888,86.94852,26.53265), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.94852,26.53265,86.99442,26.52888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.85553,26.57027,86.20663,26.58555), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.20663,26.58555,87.05385,26.58722), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.05385,26.58722,86.20663,26.58555), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.80719,26.60249,86.32581,26.61416), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.32581,26.61416,85.80719,26.60249), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.02942,26.66305,85.72858,26.67305), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.72858,26.67305,86.02942,26.66305), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.19273,26.73138,85.33441,26.73582), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.33441,26.73582,88.19273,26.73138), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.21164,26.76694,85.18663,26.79499), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.18663,26.79499,85.72607,26.80166), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.72607,26.80166,85.18663,26.79499), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.03636,26.85055,85.19441,26.85444), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.19441,26.85444,85.03636,26.85055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.62329,26.86694,85.16719,26.87083), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.16719,26.87083,85.62329,26.86694), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.14914,26.92027,84.9333,26.96749), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.9333,26.96749,84.75026,27.0075), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.75026,27.0075,84.9333,26.96749), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.64413,27.05666,87.99692,27.10249), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.99692,27.10249,84.68108,27.14083), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.68108,27.14083,87.99692,27.10249), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.01303,27.21221,84.67914,27.23527), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.67914,27.23527,88.01303,27.21221), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.33054,27.33749,84.59497,27.34194), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.59497,27.34194,83.2758,27.34333), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.2758,27.34333,84.59497,27.34194), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.85831,27.35222,83.2758,27.34333), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.8783,27.36861,83.85831,27.35222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.29774,27.38833,83.38997,27.39666), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.38997,27.39666,84.29774,27.38833), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.83275,27.42749,88.06665,27.43694), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.06665,27.43694,84.04025,27.44471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.04025,27.44471,83.17636,27.44888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.17636,27.44888,83.92368,27.45297), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.92368,27.45297,83.17636,27.44888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.60774,27.46916,83.38637,27.47277), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.38637,27.47277,84.19246,27.47638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.19246,27.47638,83.38637,27.47277), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.04581,27.48527,84.19246,27.47638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.76442,27.50527,84.09886,27.51611), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.09886,27.51611,82.76442,27.50527), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.42413,27.67944,82.70108,27.71111), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.70108,27.71111,82.42413,27.67944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.72052,27.80499,87.39359,27.80916), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.39359,27.80916,87.72052,27.80499), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.19273,27.82305,87.39359,27.80916), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.7972,27.83722,88.1958,27.84138), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.1958,27.84138,87.7972,27.83722), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.54608,27.84583,88.1958,27.84138), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.17081,27.85722,87.40414,27.86055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.88025,27.85722,87.40414,27.86055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.40414,27.86055,82.17081,27.85722), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((88.14236,27.86809,87.40414,27.86055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.01442,27.88277,88.14236,27.86809), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.44496,27.90805,87.85385,27.91472), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.85385,27.91472,85.97302,27.91582), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.97302,27.91582,87.85385,27.91472), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.99969,27.92221,86.13164,27.92611), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.13164,27.92611,81.99969,27.92221), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.93303,27.95055,86.53497,27.95499), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.53497,27.95499,86.93303,27.95055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((87.03026,27.96527,86.53497,27.95499), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.97803,27.98971,87.03026,27.96527), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.2197,28.01611,86.87413,28.01778), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.87413,28.01778,86.2197,28.01611), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.76414,28.02666,85.91885,28.03333), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.91885,28.03333,86.76414,28.02666), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.09247,28.07805,86.75803,28.08027), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.75803,28.08027,86.09247,28.07805), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.47607,28.08333,86.75803,28.08027), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.91637,28.09694,86.57191,28.10583), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.57191,28.10583,86.70415,28.1111), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.70415,28.1111,86.57191,28.10583), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.21526,28.11833,81.47025,28.1236), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.47025,28.1236,86.21526,28.11833), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.31998,28.13,81.47025,28.1236), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((86.18358,28.16388,81.38831,28.17444), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.38831,28.17444,86.18358,28.16388), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.78081,28.2061,81.38831,28.17444), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.61996,28.25111,85.28386,28.27222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.28386,28.27222,85.5383,28.28944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.5383,28.28944,85.28386,28.27222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.1033,28.31638,85.70581,28.34055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.70581,28.34055,85.1033,28.31638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.18886,28.36916,85.70581,28.34055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.10191,28.44666,80.91052,28.45722), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.91052,28.45722,85.10191,28.44666), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.83109,28.50527,85.18192,28.53), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.18192,28.53,84.93968,28.54), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.93968,28.54,85.18192,28.53), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.8233,28.55194,84.93968,28.54), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.50859,28.56694,84.8233,28.55194), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.18996,28.60332,80.44025,28.62888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.44025,28.62888,80.35774,28.62944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.35774,28.62944,80.44025,28.62888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((85.05997,28.6411,80.35774,28.62944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.50554,28.65833,85.05997,28.6411), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.56998,28.68472,80.31302,28.69721), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.31302,28.69721,80.56998,28.68472), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.0558,28.83611,84.23135,28.92944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.23135,28.92944,80.06218,28.93638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.06218,28.93638,84.23135,28.92944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.12468,28.98416,80.06218,28.93638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.25609,29.04222,84.19969,29.06277), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.19969,29.06277,84.25609,29.04222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.1497,29.11249,80.25914,29.14944), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.25914,29.14944,83.67719,29.16194), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.67719,29.16194,83.60246,29.16583), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.60246,29.16583,83.67719,29.16194), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.28775,29.20555,84.17636,29.21388), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.17636,29.21388,80.23996,29.21471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.23996,29.21471,84.17636,29.21388), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.72052,29.22888,80.23996,29.21471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((84.02359,29.27916,80.30692,29.31055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.30692,29.31055,80.2758,29.32249), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.2758,29.32249,80.30692,29.31055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.24135,29.43999,83.28441,29.47443), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.28441,29.47443,83.34608,29.49666), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.34608,29.49666,83.28441,29.47443), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.25748,29.58,83.08525,29.58527), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.08525,29.58527,83.25748,29.58), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.40997,29.60638,83.08525,29.58527), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((83.16498,29.63305,80.40997,29.60638), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.85524,29.66888,82.99219,29.6725), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.99219,29.6725,82.85524,29.66888), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.37413,29.74416,80.42024,29.78916), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.42024,29.78916,80.47997,29.79666), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.47997,29.79666,80.42024,29.78916), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.55774,29.94277,81.22359,30.01027), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.22359,30.01027,81.1097,30.02472), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.1097,30.02472,81.22359,30.01027), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.1658,30.07972,81.30191,30.0836), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.30191,30.0836,82.1658,30.07972), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.2908,30.13055,80.87552,30.14305), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.87552,30.14305,81.35663,30.14528), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.35663,30.14528,80.87552,30.14305), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.85858,30.17471,80.93636,30.18138), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.85858,30.17471,80.93636,30.18138), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.93636,30.18138,80.85858,30.17471), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.0274,30.2047,80.90414,30.21693), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((80.90414,30.21693,81.0274,30.2047), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.39636,30.28305,81.54192,30.33055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.54192,30.33055,82.10052,30.34222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((82.10052,30.34222,81.54192,30.33055), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.41136,30.38083,82.10052,30.34222), mapfile, tile_dir, 0, 11, "np-nepal")
	render_tiles((81.61552,30.41971,81.41136,30.38083), mapfile, tile_dir, 0, 11, "np-nepal")