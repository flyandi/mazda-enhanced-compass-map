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
    # Region: LR
    # Region Name: Liberia

	render_tiles((-7.52556,4.35145,-9.77371,4.94055), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.52556,4.35145,-9.77371,4.94055), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.52556,4.35145,-9.77371,4.94055), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.52556,4.35145,-9.77371,4.94055), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.72278,4.35972,-9.77371,5.90444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.72278,4.35972,-9.77371,5.90444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.72278,4.35972,-9.77371,5.90444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.72278,4.35972,-9.77371,5.90444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.56333,4.38722,-9.77371,5.08333), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.56333,4.38722,-9.77371,5.08333), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.56333,4.38722,-9.77371,5.08333), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.56333,4.38722,-9.77371,5.08333), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.54667,4.42416,-9.77371,4.94055), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.54667,4.42416,-9.77371,4.94055), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.54667,4.42416,-9.77371,4.94055), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.54667,4.42416,-9.77371,4.94055), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.90167,4.48167,-9.77371,6.27833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.90167,4.48167,-9.77371,6.27833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.90167,4.48167,-9.77371,6.27833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.90167,4.48167,-9.77371,6.27833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.2525,4.57444,-7.52556,7.18083), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.2525,4.57444,-7.52556,7.18083), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.2525,4.57444,-7.52556,7.18083), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.2525,4.57444,-7.52556,7.18083), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.54333,4.75167,-7.52556,7.61861), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.54333,4.75167,-7.52556,7.61861), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.54333,4.75167,-7.52556,7.61861), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.54333,4.75167,-7.52556,7.61861), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.58833,4.90583,-9.77371,4.38722), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.58833,4.90583,-9.77371,4.38722), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.58833,4.90583,-9.77371,4.38722), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.58833,4.90583,-9.77371,4.38722), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.53944,4.94055,-9.77371,4.42416), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.53944,4.94055,-9.77371,4.42416), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.53944,4.94055,-9.77371,4.42416), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.53944,4.94055,-9.77371,4.42416), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.98695,4.9675,-7.52556,7.28805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.98695,4.9675,-7.52556,7.28805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.98695,4.9675,-7.52556,7.28805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.98695,4.9675,-7.52556,7.28805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.555,5.08333,-9.77371,4.38722), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.555,5.08333,-9.77371,4.38722), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.555,5.08333,-9.77371,4.38722), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.555,5.08333,-9.77371,4.38722), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.49528,5.10111,-9.77371,4.35145), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.49528,5.10111,-9.77371,4.35145), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.49528,5.10111,-9.77371,4.35145), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.49528,5.10111,-9.77371,4.35145), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.35306,5.21083,-7.52556,7.74359), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.35306,5.21083,-7.52556,7.74359), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.35306,5.21083,-7.52556,7.74359), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.35306,5.21083,-7.52556,7.74359), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.46445,5.27472,-9.77371,5.86083), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.46445,5.27472,-9.77371,5.86083), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.46445,5.27472,-9.77371,5.86083), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.46445,5.27472,-9.77371,5.86083), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.41278,5.28527,-9.77371,5.65722), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.41278,5.28527,-9.77371,5.65722), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.41278,5.28527,-9.77371,5.65722), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.41278,5.28527,-9.77371,5.65722), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.36778,5.33555,-9.77371,5.57222), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.36778,5.33555,-9.77371,5.57222), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.36778,5.33555,-9.77371,5.57222), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.36778,5.33555,-9.77371,5.57222), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.40639,5.36444,-9.77371,5.28527), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.40639,5.36444,-9.77371,5.28527), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.40639,5.36444,-9.77371,5.28527), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.40639,5.36444,-9.77371,5.28527), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.50306,5.3875,-7.52556,8.17527), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.50306,5.3875,-7.52556,8.17527), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.50306,5.3875,-7.52556,8.17527), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.50306,5.3875,-7.52556,8.17527), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.595,5.43166,-9.77371,5.46472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.595,5.43166,-9.77371,5.46472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.595,5.43166,-9.77371,5.46472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.595,5.43166,-9.77371,5.46472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.43639,5.43722,-9.77371,5.845), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.43639,5.43722,-9.77371,5.845), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.43639,5.43722,-9.77371,5.845), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.43639,5.43722,-9.77371,5.845), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.58333,5.46472,-7.52556,8.41389), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.58333,5.46472,-7.52556,8.41389), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.58333,5.46472,-7.52556,8.41389), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.58333,5.46472,-7.52556,8.41389), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.625,5.49805,-7.52556,8.45555), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.625,5.49805,-7.52556,8.45555), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.625,5.49805,-7.52556,8.45555), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.625,5.49805,-7.52556,8.45555), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.36833,5.57222,-9.77371,5.33555), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.36833,5.57222,-9.77371,5.33555), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.36833,5.57222,-9.77371,5.33555), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.36833,5.57222,-9.77371,5.33555), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.41556,5.65722,-9.77371,5.28527), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.41556,5.65722,-9.77371,5.28527), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.41556,5.65722,-9.77371,5.28527), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.41556,5.65722,-9.77371,5.28527), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.84972,5.69028,-7.52556,8.49194), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.84972,5.69028,-7.52556,8.49194), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.84972,5.69028,-7.52556,8.49194), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.84972,5.69028,-7.52556,8.49194), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.42472,5.845,-9.77371,5.65722), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.42472,5.845,-9.77371,5.65722), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.42472,5.845,-9.77371,5.65722), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.42472,5.845,-9.77371,5.65722), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.45806,5.86083,-9.77371,5.27472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.45806,5.86083,-9.77371,5.27472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.45806,5.86083,-9.77371,5.27472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.45806,5.86083,-9.77371,5.27472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.68583,5.90444,-9.77371,4.35972), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.68583,5.90444,-9.77371,4.35972), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.68583,5.90444,-9.77371,4.35972), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.68583,5.90444,-9.77371,4.35972), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.64194,5.91972,-9.77371,5.90444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.64194,5.91972,-9.77371,5.90444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.64194,5.91972,-9.77371,5.90444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.64194,5.91972,-9.77371,5.90444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.7625,5.95055,-9.77371,6.03583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.7625,5.95055,-9.77371,6.03583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.7625,5.95055,-9.77371,6.03583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.7625,5.95055,-9.77371,6.03583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.18861,5.9975,-7.52556,8.53222), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.18861,5.9975,-7.52556,8.53222), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.18861,5.9975,-7.52556,8.53222), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.18861,5.9975,-7.52556,8.53222), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.77306,6.03583,-9.77371,5.95055), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.77306,6.03583,-9.77371,5.95055), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.77306,6.03583,-9.77371,5.95055), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.77306,6.03583,-9.77371,5.95055), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.84528,6.08278,-9.77371,6.20278), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.84528,6.08278,-9.77371,6.20278), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.84528,6.08278,-9.77371,6.20278), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.84528,6.08278,-9.77371,6.20278), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.345,6.09389,-7.52556,8.14639), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.345,6.09389,-7.52556,8.14639), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.345,6.09389,-7.52556,8.14639), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.345,6.09389,-7.52556,8.14639), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.38028,6.12667,-9.77371,6.16722), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.38028,6.12667,-9.77371,6.16722), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.38028,6.12667,-9.77371,6.16722), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.38028,6.12667,-9.77371,6.16722), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.3775,6.16722,-9.77371,6.12667), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.3775,6.16722,-9.77371,6.12667), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.3775,6.16722,-9.77371,6.12667), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.3775,6.16722,-9.77371,6.12667), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.56222,6.19444,-7.52556,7.77316), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.56222,6.19444,-7.52556,7.77316), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.56222,6.19444,-7.52556,7.77316), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.56222,6.19444,-7.52556,7.77316), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.46472,6.20222,-7.52556,8.13638), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.46472,6.20222,-7.52556,8.13638), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.46472,6.20222,-7.52556,8.13638), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.46472,6.20222,-7.52556,8.13638), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.8225,6.20278,-9.77371,6.08278), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.8225,6.20278,-9.77371,6.08278), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.8225,6.20278,-9.77371,6.08278), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.8225,6.20278,-9.77371,6.08278), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-7.90222,6.27833,-9.77371,4.48167), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-7.90222,6.27833,-9.77371,4.48167), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-7.90222,6.27833,-9.77371,4.48167), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-7.90222,6.27833,-9.77371,4.48167), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.07333,6.29333,-9.77371,6.29861), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.07333,6.29333,-9.77371,6.29861), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.07333,6.29333,-9.77371,6.29861), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.07333,6.29333,-9.77371,6.29861), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.20805,6.29861,-9.77371,4.57444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.20805,6.29861,-9.77371,4.57444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.20805,6.29861,-9.77371,4.57444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.20805,6.29861,-9.77371,4.57444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.81056,6.30694,-9.77371,6.44805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.81056,6.30694,-9.77371,6.44805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.81056,6.30694,-9.77371,6.44805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.81056,6.30694,-9.77371,6.44805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.77278,6.31333,-9.77371,6.30694), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.77278,6.31333,-9.77371,6.30694), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.77278,6.31333,-9.77371,6.30694), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.77278,6.31333,-9.77371,6.30694), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.28944,6.34805,-7.52556,7.18083), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.28944,6.34805,-7.52556,7.18083), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.28944,6.34805,-7.52556,7.18083), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.28944,6.34805,-7.52556,7.18083), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.36694,6.35444,-7.52556,7.23944), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.36694,6.35444,-7.52556,7.23944), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.36694,6.35444,-7.52556,7.23944), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.36694,6.35444,-7.52556,7.23944), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.40833,6.425,-7.52556,7.50111), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.40833,6.425,-7.52556,7.50111), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.40833,6.425,-7.52556,7.50111), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.40833,6.425,-7.52556,7.50111), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.47528,6.43583,-7.52556,7.55987), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.47528,6.43583,-7.52556,7.55987), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.47528,6.43583,-7.52556,7.55987), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.47528,6.43583,-7.52556,7.55987), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.38194,6.4425,-9.77371,6.35444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.38194,6.4425,-9.77371,6.35444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.38194,6.4425,-9.77371,6.35444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.38194,6.4425,-9.77371,6.35444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.83556,6.44805,-9.77371,6.30694), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.83556,6.44805,-9.77371,6.30694), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.83556,6.44805,-9.77371,6.30694), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.83556,6.44805,-9.77371,6.30694), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.45472,6.49,-7.52556,7.55987), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.45472,6.49,-7.52556,7.55987), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.45472,6.49,-7.52556,7.55987), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.45472,6.49,-7.52556,7.55987), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.60667,6.50778,-7.52556,7.69167), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.60667,6.50778,-7.52556,7.69167), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.60667,6.50778,-7.52556,7.69167), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.60667,6.50778,-7.52556,7.69167), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.50611,6.59416,-9.77371,6.43583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.50611,6.59416,-9.77371,6.43583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.50611,6.59416,-9.77371,6.43583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.50611,6.59416,-9.77371,6.43583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.27917,6.66972,-7.52556,7.22444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.27917,6.66972,-7.52556,7.22444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.27917,6.66972,-7.52556,7.22444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.27917,6.66972,-7.52556,7.22444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.38389,6.73694,-7.52556,6.80583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.38389,6.73694,-7.52556,6.80583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.38389,6.73694,-7.52556,6.80583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.38389,6.73694,-7.52556,6.80583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.35833,6.75417,-7.52556,7.23944), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.35833,6.75417,-7.52556,7.23944), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.35833,6.75417,-7.52556,7.23944), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.35833,6.75417,-7.52556,7.23944), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.36611,6.80583,-7.52556,7.14694), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.36611,6.80583,-7.52556,7.14694), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.36611,6.80583,-7.52556,7.14694), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.36611,6.80583,-7.52556,7.14694), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.41028,6.85111,-7.52556,6.73694), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.41028,6.85111,-7.52556,6.73694), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.41028,6.85111,-7.52556,6.73694), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.41028,6.85111,-7.52556,6.73694), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.4987,6.9132,-7.52556,6.85111), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.4987,6.9132,-7.52556,6.85111), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.4987,6.9132,-7.52556,6.85111), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.4987,6.9132,-7.52556,6.85111), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.33778,7.09278,-7.52556,7.14694), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.33778,7.09278,-7.52556,7.14694), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.33778,7.09278,-7.52556,7.14694), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.33778,7.09278,-7.52556,7.14694), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.35583,7.14694,-7.52556,6.80583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.35583,7.14694,-7.52556,6.80583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.35583,7.14694,-7.52556,6.80583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.35583,7.14694,-7.52556,6.80583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.28806,7.18083,-9.77371,6.34805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.28806,7.18083,-9.77371,6.34805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.28806,7.18083,-9.77371,6.34805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.28806,7.18083,-9.77371,6.34805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.11091,7.19394,-7.52556,7.2325), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.11091,7.19394,-7.52556,7.2325), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.11091,7.19394,-7.52556,7.2325), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.11091,7.19394,-7.52556,7.2325), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.2775,7.22444,-7.52556,6.66972), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.2775,7.22444,-7.52556,6.66972), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.2775,7.22444,-7.52556,6.66972), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.2775,7.22444,-7.52556,6.66972), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.09778,7.2325,-7.52556,7.19394), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.09778,7.2325,-7.52556,7.19394), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.09778,7.2325,-7.52556,7.19394), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.09778,7.2325,-7.52556,7.19394), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.36139,7.23944,-7.52556,6.75417), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.36139,7.23944,-7.52556,6.75417), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.36139,7.23944,-7.52556,6.75417), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.36139,7.23944,-7.52556,6.75417), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.90417,7.25361,-7.52556,7.28805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.90417,7.25361,-7.52556,7.28805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.90417,7.25361,-7.52556,7.28805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.90417,7.25361,-7.52556,7.28805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.92861,7.28805,-7.52556,7.25361), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.92861,7.28805,-7.52556,7.25361), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.92861,7.28805,-7.52556,7.25361), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.92861,7.28805,-7.52556,7.25361), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.83944,7.30028,-7.52556,7.25361), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.83944,7.30028,-7.52556,7.25361), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.83944,7.30028,-7.52556,7.25361), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.83944,7.30028,-7.52556,7.25361), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.19806,7.3125,-7.52556,7.38139), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.19806,7.3125,-7.52556,7.38139), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.19806,7.3125,-7.52556,7.38139), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.19806,7.3125,-7.52556,7.38139), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.48442,7.36366,-7.52556,8.34638), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.48442,7.36366,-7.52556,8.34638), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.48442,7.36366,-7.52556,8.34638), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.48442,7.36366,-7.52556,8.34638), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.20417,7.38139,-7.52556,7.3125), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.20417,7.38139,-7.52556,7.3125), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.20417,7.38139,-7.52556,7.3125), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.20417,7.38139,-7.52556,7.3125), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-11.09889,7.38722,-7.52556,7.22444), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-11.09889,7.38722,-7.52556,7.22444), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-11.09889,7.38722,-7.52556,7.22444), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-11.09889,7.38722,-7.52556,7.22444), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.39,7.38861,-7.52556,8.03277), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.39,7.38861,-7.52556,8.03277), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.39,7.38861,-7.52556,8.03277), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.39,7.38861,-7.52556,8.03277), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.30506,7.41637,-9.77371,5.21083), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.30506,7.41637,-9.77371,5.21083), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.30506,7.41637,-9.77371,5.21083), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.30506,7.41637,-9.77371,5.21083), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.4225,7.425,-7.52556,8.05833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.4225,7.425,-7.52556,8.05833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.4225,7.425,-7.52556,8.05833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.4225,7.425,-7.52556,8.05833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.41333,7.48944,-7.52556,8.03277), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.41333,7.48944,-7.52556,8.03277), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.41333,7.48944,-7.52556,8.03277), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.41333,7.48944,-7.52556,8.03277), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.41833,7.50111,-9.77371,6.425), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.41833,7.50111,-9.77371,6.425), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.41833,7.50111,-9.77371,6.425), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.41833,7.50111,-9.77371,6.425), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.90417,7.50916,-9.77371,6.44805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.90417,7.50916,-9.77371,6.44805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.90417,7.50916,-9.77371,6.44805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.90417,7.50916,-9.77371,6.44805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.70805,7.51833,-7.52556,7.57222), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.70805,7.51833,-7.52556,7.57222), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.70805,7.51833,-7.52556,7.57222), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.70805,7.51833,-7.52556,7.57222), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.46896,7.55987,-9.77371,6.43583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.46896,7.55987,-9.77371,6.43583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.46896,7.55987,-9.77371,6.43583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.46896,7.55987,-9.77371,6.43583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.72305,7.57222,-7.52556,7.51833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.72305,7.57222,-7.52556,7.51833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.72305,7.57222,-7.52556,7.51833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.72305,7.57222,-7.52556,7.51833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.55611,7.61861,-7.52556,7.69167), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.55611,7.61861,-7.52556,7.69167), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.55611,7.61861,-7.52556,7.69167), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.55611,7.61861,-7.52556,7.69167), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.55722,7.69167,-7.52556,7.61861), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.55722,7.69167,-7.52556,7.61861), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.55722,7.69167,-7.52556,7.61861), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.55722,7.69167,-7.52556,7.61861), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-8.67055,7.69666,-7.52556,7.51833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-8.67055,7.69666,-7.52556,7.51833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-8.67055,7.69666,-7.52556,7.51833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-8.67055,7.69666,-7.52556,7.51833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.3552,7.74359,-9.77371,5.21083), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.3552,7.74359,-9.77371,5.21083), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.3552,7.74359,-9.77371,5.21083), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.3552,7.74359,-9.77371,5.21083), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.68472,7.74444,-7.52556,8.03055), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.68472,7.74444,-7.52556,8.03055), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.68472,7.74444,-7.52556,8.03055), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.68472,7.74444,-7.52556,8.03055), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.60044,7.77316,-7.52556,8.03055), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.60044,7.77316,-7.52556,8.03055), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.60044,7.77316,-7.52556,8.03055), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.60044,7.77316,-7.52556,8.03055), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.43611,7.93194,-7.52556,8.05833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.43611,7.93194,-7.52556,8.05833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.43611,7.93194,-7.52556,8.05833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.43611,7.93194,-7.52556,8.05833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.60333,8.03055,-7.52556,7.77316), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.60333,8.03055,-7.52556,7.77316), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.60333,8.03055,-7.52556,7.77316), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.60333,8.03055,-7.52556,7.77316), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.41028,8.03277,-7.52556,7.48944), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.41028,8.03277,-7.52556,7.48944), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.41028,8.03277,-7.52556,7.48944), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.41028,8.03277,-7.52556,7.48944), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.42833,8.05833,-7.52556,7.425), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.42833,8.05833,-7.52556,7.425), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.42833,8.05833,-7.52556,7.425), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.42833,8.05833,-7.52556,7.425), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.46278,8.13389,-7.52556,7.36366), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.46278,8.13389,-7.52556,7.36366), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.46278,8.13389,-7.52556,7.36366), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.46278,8.13389,-7.52556,7.36366), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.49806,8.13638,-9.77371,6.20222), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.49806,8.13638,-9.77371,6.20222), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.49806,8.13638,-9.77371,6.20222), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.49806,8.13638,-9.77371,6.20222), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.35555,8.14639,-9.77371,6.09389), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.35555,8.14639,-9.77371,6.09389), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.35555,8.14639,-9.77371,6.09389), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.35555,8.14639,-9.77371,6.09389), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.49139,8.17527,-7.52556,8.34638), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.49139,8.17527,-7.52556,8.34638), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.49139,8.17527,-7.52556,8.34638), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.49139,8.17527,-7.52556,8.34638), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.29722,8.19833,-7.52556,8.36583), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.29722,8.19833,-7.52556,8.36583), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.29722,8.19833,-7.52556,8.36583), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.29722,8.19833,-7.52556,8.36583), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.51722,8.24277,-9.77371,5.3875), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.51722,8.24277,-9.77371,5.3875), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.51722,8.24277,-9.77371,5.3875), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.51722,8.24277,-9.77371,5.3875), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.48444,8.34638,-7.52556,7.36366), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.48444,8.34638,-7.52556,7.36366), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.48444,8.34638,-7.52556,7.36366), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.48444,8.34638,-7.52556,7.36366), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.29445,8.36583,-7.52556,8.19833), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.29445,8.36583,-7.52556,8.19833), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.29445,8.36583,-7.52556,8.19833), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.29445,8.36583,-7.52556,8.19833), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.65611,8.38972,-7.52556,8.48916), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.65611,8.38972,-7.52556,8.48916), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.65611,8.38972,-7.52556,8.48916), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.65611,8.38972,-7.52556,8.48916), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.58194,8.41389,-9.77371,5.46472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.58194,8.41389,-9.77371,5.46472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.58194,8.41389,-9.77371,5.46472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.58194,8.41389,-9.77371,5.46472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.04528,8.42138,-7.52556,8.50472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.04528,8.42138,-7.52556,8.50472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.04528,8.42138,-7.52556,8.50472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.04528,8.42138,-7.52556,8.50472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.70667,8.43277,-7.52556,8.48777), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.70667,8.43277,-7.52556,8.48777), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.70667,8.43277,-7.52556,8.48777), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.70667,8.43277,-7.52556,8.48777), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.06361,8.43472,-7.52556,8.50472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.06361,8.43472,-7.52556,8.50472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.06361,8.43472,-7.52556,8.50472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.06361,8.43472,-7.52556,8.50472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.6275,8.45555,-9.77371,5.49805), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.6275,8.45555,-9.77371,5.49805), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.6275,8.45555,-9.77371,5.49805), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.6275,8.45555,-9.77371,5.49805), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.95222,8.48222,-7.52556,8.42138), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.95222,8.48222,-7.52556,8.42138), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.95222,8.48222,-7.52556,8.42138), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.95222,8.48222,-7.52556,8.42138), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.24889,8.48777,-7.52556,8.4887), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.24889,8.48777,-7.52556,8.4887), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.24889,8.48777,-7.52556,8.4887), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.24889,8.48777,-7.52556,8.4887), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.69417,8.48777,-7.52556,8.43277), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.69417,8.48777,-7.52556,8.43277), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.69417,8.48777,-7.52556,8.43277), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.69417,8.48777,-7.52556,8.43277), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.26652,8.4887,-7.52556,8.48777), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.6625,8.48916,-7.52556,8.38972), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.6625,8.48916,-7.52556,8.38972), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.6625,8.48916,-7.52556,8.38972), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.6625,8.48916,-7.52556,8.38972), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.83778,8.49194,-9.77371,5.69028), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.83778,8.49194,-9.77371,5.69028), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.83778,8.49194,-9.77371,5.69028), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.83778,8.49194,-9.77371,5.69028), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.05667,8.50472,-7.52556,8.43472), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.05667,8.50472,-7.52556,8.43472), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.05667,8.50472,-7.52556,8.43472), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.05667,8.50472,-7.52556,8.43472), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-10.16222,8.53222,-9.77371,5.9975), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-10.16222,8.53222,-9.77371,5.9975), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-10.16222,8.53222,-9.77371,5.9975), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-10.16222,8.53222,-9.77371,5.9975), mapfile, tile_dir, 17, 17, "lr-liberia")
	render_tiles((-9.77371,8.56968,-7.52556,8.49194), mapfile, tile_dir, 0, 11, "lr-liberia")
	render_tiles((-9.77371,8.56968,-7.52556,8.49194), mapfile, tile_dir, 13, 13, "lr-liberia")
	render_tiles((-9.77371,8.56968,-7.52556,8.49194), mapfile, tile_dir, 15, 15, "lr-liberia")
	render_tiles((-9.77371,8.56968,-7.52556,8.49194), mapfile, tile_dir, 17, 17, "lr-liberia")