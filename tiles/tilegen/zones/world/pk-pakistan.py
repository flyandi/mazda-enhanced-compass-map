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
    # Region: PK
    # Region Name: Pakistan

	render_tiles((68.15082,23.68805,68.05026,23.72305), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.05026,23.72305,68.18221,23.75694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.18221,23.75694,68.01804,23.76527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.01804,23.76527,68.07581,23.76722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.07581,23.76722,68.01804,23.76527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.19609,23.77334,68.07581,23.76722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.70914,23.78944,67.74525,23.80055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.74525,23.80055,67.83803,23.81055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.83803,23.81055,67.62526,23.8111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.62526,23.8111,67.83803,23.81055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.04997,23.82333,67.92303,23.83333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.92303,23.83333,68.04997,23.82333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.62053,23.84639,67.86525,23.85221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.86525,23.85221,67.62053,23.84639), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.04997,23.87888,67.4847,23.88194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.4847,23.88194,68.04997,23.87888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.85719,23.91,68.33054,23.91471), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.33054,23.91471,68.06581,23.91694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.06581,23.91694,68.33054,23.91471), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.50137,23.92444,67.64748,23.92777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.64748,23.92777,67.50137,23.92444), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.01031,23.93614,68.29053,23.94444), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.29053,23.94444,68.01031,23.93614), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.74719,23.96999,68.36191,23.97388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.36191,23.97388,67.50053,23.97499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.50053,23.97499,68.36191,23.97388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.45859,23.97861,67.50053,23.97499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.45747,23.99778,67.45859,23.97861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.53186,24.06248,67.37804,24.06249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.37804,24.06249,67.53186,24.06248), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.33803,24.08972,67.37804,24.06249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.02776,24.17277,69.69647,24.19438), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.69647,24.19438,70.07109,24.19721), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.07109,24.19721,69.69647,24.19438), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.78497,24.23832,68.87051,24.24194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.87051,24.24194,70.64413,24.2436), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.64413,24.2436,68.87051,24.24194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.99442,24.25777,69.18941,24.25805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.18941,24.25805,68.99442,24.25777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.22864,24.26994,68.83803,24.27027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.83803,24.27027,69.22864,24.26994), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.8858,24.27444,68.83803,24.27027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.59859,24.28139,70.5797,24.28722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.5797,24.28722,69.4222,24.28833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.4222,24.28833,70.5797,24.28722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.88664,24.28944,69.4222,24.28833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.08026,24.29722,68.88664,24.28944), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.12091,24.30853,70.86275,24.31027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.86275,24.31027,70.12091,24.30853), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.74524,24.31638,70.86275,24.31027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.75775,24.3236,68.92691,24.32555), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.92691,24.32555,68.75775,24.3236), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.82469,24.33278,68.92691,24.32555), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.27887,24.35694,70.38107,24.36777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.38107,24.36777,70.94164,24.36916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.94164,24.36916,70.38107,24.36777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.09859,24.40889,67.2572,24.4136), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.2572,24.4136,71.09859,24.40889), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.59331,24.42221,67.2572,24.4136), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.54552,24.43194,71.1058,24.43333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.1058,24.43333,70.54552,24.43194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.00609,24.45805,71.1058,24.43333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.18137,24.58527,67.25165,24.58916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.25165,24.58916,67.18137,24.58527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.97691,24.59639,67.25165,24.58916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.15053,24.61555,70.97691,24.59639), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.99969,24.64444,67.15942,24.66527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.15942,24.66527,70.99969,24.64444), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.08713,24.68633,67.15942,24.66527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.06052,24.72221,67.25276,24.73027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.25276,24.73027,71.06052,24.72221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.2397,24.76083,67.25276,24.73027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.64554,24.82917,66.85275,24.855), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.85275,24.855,66.64554,24.82917), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.67415,24.93389,66.85275,24.855), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.75777,25.03249,61.72415,25.05805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.72415,25.05805,61.86721,25.06972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.86721,25.06972,61.72415,25.05805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.33305,25.09194,62.38721,25.09861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.38721,25.09861,62.10443,25.10167), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.10443,25.10167,62.38721,25.09861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.89665,25.10666,62.27943,25.10972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.27943,25.10972,61.75943,25.11139), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.75943,25.11139,62.27943,25.10972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.95888,25.12,62.33638,25.12777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.33638,25.12777,61.95888,25.12), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.3436,25.15528,62.06999,25.15805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.06999,25.15805,62.3436,25.15528), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.31888,25.16639,66.7397,25.16694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.7397,25.16694,62.31888,25.16639), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.86858,25.17249,64.6022,25.17305), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.6022,25.17305,70.86858,25.17249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.70581,25.1775,64.6022,25.17305), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.08388,25.19027,61.77637,25.19249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.77637,25.19249,61.61097,25.19332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.61097,25.19332,61.77637,25.19249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.29054,25.20194,62.41693,25.20416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.41693,25.20416,62.29054,25.20194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.5211,25.20833,64.63109,25.20861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.63109,25.20861,63.5211,25.20833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.66081,25.21833,63.30666,25.21861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.30666,25.21861,64.66081,25.21833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.21082,25.22055,63.30666,25.21861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.71637,25.22861,64.43498,25.23111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.43498,25.23111,66.71637,25.22861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.79135,25.23805,64.43498,25.23111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.61443,25.24555,70.79135,25.23805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.6861,25.25833,64.52859,25.27083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.52859,25.27083,64.36693,25.27722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.36693,25.27722,64.67804,25.27777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.67804,25.27777,64.36693,25.27722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.46999,25.28027,64.67804,25.27777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.15164,25.28944,63.46999,25.28027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.64693,25.30611,65.05359,25.31222), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.05359,25.31222,64.72859,25.31388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.72859,25.31388,65.05359,25.31222), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.09164,25.32861,64.05553,25.33722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.05553,25.33722,65.66136,25.34249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.66136,25.34249,64.05553,25.33722), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.55664,25.34999,65.66136,25.34249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.07553,25.36388,63.80415,25.3736), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.80415,25.3736,66.54747,25.37833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.54747,25.37833,63.65638,25.37888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.65638,25.37888,66.54747,25.37833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.58691,25.3825,63.65638,25.37888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.3372,25.38639,66.58691,25.3825), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.49664,25.39805,64.08136,25.40139), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.08136,25.40139,66.49664,25.39805), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.05359,25.40944,63.97665,25.41194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.97665,25.41194,64.05359,25.40944), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.67441,25.41694,66.58803,25.42139), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.58803,25.42139,70.67441,25.41694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.12053,25.42861,64.09137,25.42916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.09137,25.42916,64.12053,25.42861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.48997,25.435,64.09137,25.42916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.0847,25.44611,64.0347,25.45083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.0347,25.45083,64.15053,25.45166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.15053,25.45166,64.0347,25.45083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.4597,25.45166,64.0347,25.45083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.3022,25.46861,66.11304,25.46916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.11304,25.46916,66.3022,25.46861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.48997,25.48027,66.11304,25.46916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.11331,25.495,66.51025,25.50888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.51025,25.50888,70.67969,25.5111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.67969,25.5111,66.51025,25.50888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.33331,25.55472,66.43803,25.59333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.43803,25.59333,66.2608,25.60666), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.2608,25.60666,66.43803,25.59333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.37914,25.67555,70.66275,25.70193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.66275,25.70193,70.2722,25.71277), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.2722,25.71277,70.6183,25.71499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.6183,25.71499,70.2722,25.71277), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.68637,25.79499,61.76943,25.80972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.76943,25.80972,61.68637,25.79499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.18246,25.83472,61.76943,25.80972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.08609,25.99333,70.18246,25.83472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.83276,26.17944,61.85804,26.23471), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.85804,26.23471,70.17386,26.24444), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.17386,26.24444,61.85804,26.23471), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.1236,26.31833,62.27667,26.35406), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.27667,26.35406,62.13582,26.38083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.13582,26.38083,62.27667,26.35406), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.27332,26.42944,62.13582,26.38083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.18941,26.51083,62.37137,26.54249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.37137,26.54249,70.1683,26.55639), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.1683,26.55639,62.37137,26.54249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.6136,26.57999,69.8008,26.59416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.8008,26.59416,70.08498,26.59472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.08498,26.59472,69.8008,26.59416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.1811,26.63388,63.14027,26.63527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.14027,26.63527,63.1811,26.63388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.77248,26.64972,63.14027,26.63527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.50887,26.75027,69.48497,26.81166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.48497,26.81166,63.20638,26.84222), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.20638,26.84222,69.48497,26.81166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.49663,26.87555,63.28693,26.8811), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.28693,26.8811,69.49663,26.87555), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.25417,27.01864,63.27859,27.12194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.27859,27.12194,63.34193,27.12249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.34193,27.12249,63.27859,27.12194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.31776,27.16944,69.58331,27.17833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.58331,27.17833,63.31776,27.16944), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.91971,27.21499,69.65663,27.23888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.65663,27.23888,62.91971,27.21499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.19804,27.26777,62.76471,27.27194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.76471,27.27194,63.19804,27.26777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.83971,27.47444,70.03748,27.6), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.03748,27.6,70.79442,27.70972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.79442,27.70972,70.89442,27.71221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.89442,27.71221,70.79442,27.70972), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.73775,27.73138,70.08394,27.73613), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.08394,27.73613,70.73775,27.73138), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.82193,27.76027,70.08394,27.73613), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.12802,27.82888,71.23969,27.8486), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.23969,27.8486,70.12802,27.82888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.62691,27.8761,71.23969,27.8486), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.89693,27.96194,70.59859,27.98999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.59859,27.98999,62.75777,28.00027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.75777,28.00027,70.59859,27.98999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.37579,28.02333,70.51442,28.03916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.51442,28.03916,70.37579,28.02333), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.91803,28.11749,70.51442,28.03916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.99219,28.21583,62.59248,28.2336), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.59248,28.2336,71.99219,28.21583), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.78137,28.26694,62.59248,28.2336), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.20663,28.40249,62.39471,28.42166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.39471,28.42166,72.20663,28.40249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.03971,28.50055,62.39471,28.42166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.29219,28.67999,72.38969,28.78499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.38969,28.78499,61.65137,28.78527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.65137,28.78527,72.38969,28.78499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.54758,28.98494,72.9483,29.03583), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.9483,29.03583,61.54758,28.98494), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.5111,29.0886,72.9483,29.03583), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.41998,29.16444,61.5111,29.0886), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.35526,29.38833,64.13135,29.39416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.13135,29.39416,61.35526,29.38833), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.48443,29.4061,64.13135,29.39416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((62.78693,29.43388,62.48443,29.4061), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.19302,29.48749,63.5872,29.50388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((63.5872,29.50388,64.19302,29.48749), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.27386,29.52389,65.03413,29.5411), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.03413,29.5411,64.27386,29.52389), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.2697,29.56166,65.03413,29.5411), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((64.69551,29.58638,73.2697,29.56166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((61.7436,29.61583,65.41747,29.64055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((65.41747,29.64055,61.7436,29.61583), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((60.99387,29.8261,66.25664,29.85194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.25664,29.85194,60.86687,29.86243), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((60.86687,29.86243,66.25664,29.85194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.39748,29.94277,66.36136,29.9661), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.36136,29.9661,73.39748,29.94277), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.58858,30.02028,73.79886,30.07055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.79886,30.07055,66.2383,30.07138), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.2383,30.07138,73.79886,30.07055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.2597,30.11416,66.2383,30.07138), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.97191,30.19499,66.2597,30.11416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.9344,30.31416,73.85608,30.36472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.85608,30.36472,73.90663,30.39861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.90663,30.39861,73.85608,30.36472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.35025,30.45055,73.90663,30.39861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.28192,30.57527,66.35025,30.45055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.21664,30.7011,66.28192,30.57527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.40497,30.94611,66.56636,30.97777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.56636,30.97777,66.40497,30.94611), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.62885,31.02527,74.66302,31.05888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.66302,31.05888,74.56859,31.06416), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.56859,31.06416,74.66302,31.05888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.68413,31.08611,74.67247,31.09916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.67247,31.09916,66.68413,31.08611), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.53748,31.14222,74.67247,31.09916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.53081,31.19444,66.72302,31.21221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.72302,31.21221,67.28802,31.2136), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.28802,31.2136,66.72302,31.21221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.25636,31.22249,67.28802,31.2136), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.0697,31.23916,67.0347,31.25444), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.0347,31.25444,67.0697,31.23916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((66.89165,31.2961,67.05164,31.29778), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.05164,31.29778,66.89165,31.2961), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.03691,31.31861,67.05164,31.29778), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.79192,31.3411,67.03691,31.31861), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.7997,31.38249,67.64636,31.40999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.64636,31.40999,67.76692,31.4111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.76692,31.4111,67.64636,31.40999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.63914,31.48166,67.73802,31.53083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.73802,31.53083,67.58109,31.5336), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.58109,31.5336,67.73802,31.53083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.58844,31.59803,68.83304,31.60388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.83304,31.60388,74.58844,31.59803), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.98053,31.63583,67.88748,31.63999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((67.88748,31.63999,67.98053,31.63583), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.02969,31.64555,67.88748,31.63999), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.73192,31.69944,74.51608,31.72472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.51608,31.72472,68.53775,31.72666), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.53775,31.72666,74.51608,31.72472), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.57164,31.76527,68.44774,31.77277), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.44774,31.77277,68.71248,31.77888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.71248,31.77888,68.44774,31.77277), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.44412,31.79472,68.71248,31.77888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.22163,31.81559,74.53802,31.81666), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.53802,31.81666,68.22163,31.81559), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.54747,31.82916,68.16608,31.83305), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((68.16608,31.83305,68.54747,31.82916), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.62024,31.87194,68.16608,31.83305), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.33386,31.94389,74.62024,31.87194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.7847,31.94389,74.62024,31.87194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.81413,32.01971,74.96275,32.03193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.96275,32.03193,74.81413,32.01971), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.28748,32.06915,75.10802,32.07083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.10802,32.07083,69.28748,32.06915), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.03748,32.09888,75.10802,32.07083), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.32776,32.17249,69.28304,32.21777), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.28304,32.21777,75.32776,32.17249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.38219,32.27555,75.35753,32.30504), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.35753,32.30504,75.38219,32.27555), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.24802,32.44387,74.74442,32.465), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.74442,32.465,75.03748,32.48027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.03748,32.48027,74.74442,32.465), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.68747,32.5011,75.03748,32.48027), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.28748,32.52638,74.68747,32.5011), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.39775,32.58776,74.65358,32.59332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.65358,32.59332,69.39775,32.58776), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.45692,32.68221,74.59859,32.75888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.59859,32.75888,69.39497,32.77387), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.39497,32.77387,74.3597,32.77582), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.3597,32.77582,69.39497,32.77387), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.64636,32.78054,74.3597,32.77582), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.33298,32.81525,74.66692,32.83611), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.66692,32.83611,74.7133,32.83887), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.7133,32.83887,74.66692,32.83611), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.36386,32.86943,69.51526,32.87388), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.51526,32.87388,74.36386,32.86943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.33414,33.00055,69.49246,33.0086), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.49246,33.0086,74.33414,33.00055), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.56108,33.08193,69.88107,33.08998), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.88107,33.08998,69.56108,33.08193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.79135,33.12694,70.03358,33.13943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.03358,33.13943,69.79135,33.12694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.0383,33.16443,70.03358,33.13943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.14497,33.20249,70.06775,33.20499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.06775,33.20499,70.14497,33.20249), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.01192,33.21054,70.06775,33.20499), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.02414,33.27637,70.32692,33.33194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.32692,33.33194,74.12579,33.34332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.12579,33.34332,70.32692,33.33194), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.30608,33.3961,74.12579,33.34332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.19774,33.48582,74.18246,33.51054), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.18246,33.51054,70.19774,33.48582), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.16248,33.55388,74.18246,33.51054), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.01469,33.63888,70.19662,33.64082), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.19662,33.64082,74.01469,33.63888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.98497,33.68777,70.19662,33.64082), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.13274,33.73554,69.98552,33.75304), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.98552,33.75304,73.99469,33.75555), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.99469,33.75555,69.98552,33.75304), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.0558,33.83443,74.16997,33.84832), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.16997,33.84832,74.0558,33.83443), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.90747,33.88193,74.25748,33.90109), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.25748,33.90109,69.90747,33.88193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.49135,33.94304,74.29469,33.96443), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.29469,33.96443,70.90053,33.97359), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.90053,33.97359,74.29469,33.96443), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.98108,34.00888,70.90553,34.01332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.90553,34.01332,70.98108,34.00888), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.24442,34.01915,73.95581,34.0211), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.95581,34.0211,74.24442,34.01915), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((69.90276,34.0311,73.95581,34.0211), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.90775,34.08998,73.92636,34.1322), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.92636,34.1322,71.13553,34.16609), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.13553,34.16609,74.01414,34.17554), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.01414,34.17554,71.13553,34.16609), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.02109,34.20832,74.01414,34.17554), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.96831,34.30609,73.85469,34.31582), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.85469,34.31582,73.96831,34.30609), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.80608,34.35332,71.15331,34.36137), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.15331,34.36137,73.80608,34.35332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.07303,34.39415,73.79913,34.3986), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.79913,34.3986,71.07303,34.39415), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.80553,34.50721,70.97803,34.51082), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.97803,34.51082,75.80553,34.50721), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.39998,34.55304,70.9958,34.55859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((70.9958,34.55859,75.39998,34.55304), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.09497,34.56805,70.9958,34.55859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.16052,34.58527,73.94608,34.5936), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.94608,34.5936,76.16052,34.58527), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.93747,34.60804,76.22914,34.60943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.22914,34.60943,75.93747,34.60804), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.20053,34.62415,76.04663,34.62943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.04663,34.62943,75.20053,34.62415), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.86996,34.65887,73.95775,34.66137), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.95775,34.66137,76.86996,34.65887), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.80136,34.67221,71.09581,34.67665), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.09581,34.67665,76.80136,34.67221), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.91692,34.68137,71.09581,34.67665), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.67941,34.69637,76.29358,34.70026), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.29358,34.70026,74.67941,34.69637), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.73857,34.73859,71.22552,34.74443), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.22552,34.74443,76.73857,34.73859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.24747,34.75471,71.22552,34.74443), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.47025,34.76998,74.38914,34.78193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.38914,34.78193,76.47025,34.76998), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.31386,34.88693,76.96414,34.94054), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.96414,34.94054,71.49608,34.95943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.49608,34.95943,76.96414,34.94054), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.02386,34.98248,71.49608,34.95943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.06525,35.03999,77.04112,35.09393), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.04112,35.09393,71.5433,35.0947), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.5433,35.0947,77.04112,35.09393), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.61803,35.13137,71.5433,35.0947), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.65913,35.20749,77.2381,35.21732), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.2381,35.21732,71.65913,35.20749), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.55357,35.28915,77.52763,35.31225), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.52763,35.31225,71.54913,35.32832), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.54913,35.32832,77.52763,35.31225), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.75546,35.40481,71.6472,35.43694), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.6472,35.43694,77.70108,35.4611), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.70108,35.4611,77.83141,35.48075), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.83141,35.48075,71.60692,35.48193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.60692,35.48193,77.83141,35.48075), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((77.52109,35.48415,71.60692,35.48193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.61386,35.56193,76.91997,35.59554), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.91997,35.59554,71.50859,35.62665), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.50859,35.62665,76.91997,35.59554), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.54524,35.7111,76.5883,35.76082), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.5883,35.76082,71.54524,35.7111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.2222,35.81332,76.15775,35.82804), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.15775,35.82804,76.2222,35.81332), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.5258,35.8736,76.5847,35.89193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.5847,35.89193,76.5258,35.8736), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.56413,35.91276,76.5847,35.89193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.38052,35.94609,71.29108,35.96859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.29108,35.96859,76.12746,35.97276), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.12746,35.97276,71.29108,35.96859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.09053,36.00054,76.00775,36.00166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.00775,36.00166,76.09053,36.00054), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.95137,36.03137,71.18802,36.04721), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.18802,36.04721,75.95137,36.03137), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.91997,36.11915,71.24858,36.13304), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.24858,36.13304,75.91997,36.11915), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.00443,36.17748,71.24858,36.13304), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.58275,36.33582,71.56302,36.37248), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.56302,36.37248,71.75304,36.40749), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.75304,36.40749,71.81775,36.41666), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.81775,36.41666,71.75304,36.40749), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((76.00497,36.4586,71.64664,36.46804), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.64664,36.46804,76.00497,36.4586), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((71.79581,36.49193,71.64664,36.46804), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.07469,36.58942,75.89886,36.62859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.89886,36.62859,72.0733,36.62887), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.0733,36.62887,75.89886,36.62859), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.19164,36.6572,72.0733,36.62887), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.18164,36.71471,75.47275,36.72554), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.47275,36.72554,72.18164,36.71471), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.72386,36.74276,75.43108,36.74776), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.43108,36.74776,75.72386,36.74276), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.55414,36.77082,72.49246,36.77193), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((72.49246,36.77193,75.55414,36.77082), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.06218,36.82166,73.96303,36.83776), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.96303,36.83776,74.06218,36.82166), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.11302,36.87387,74.25192,36.89943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.25192,36.89943,73.77969,36.90109), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((73.77969,36.90109,74.25192,36.89943), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.15886,36.90665,74.89525,36.91165), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.89525,36.91165,74.15886,36.90665), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.39247,36.91749,74.89525,36.91165), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.33691,36.95888,74.55801,36.96526), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.55801,36.96526,74.92775,36.96609), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.92775,36.96609,74.55801,36.96526), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((75.13135,37.00138,74.48219,37.0111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.48219,37.0111,75.13135,37.00138), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.83887,37.0111,75.13135,37.00138), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.56775,37.02647,74.48219,37.0111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.56775,37.02647,74.48219,37.0111), mapfile, tile_dir, 0, 11, "pk-pakistan")
	render_tiles((74.6908,37.06276,74.56775,37.02647), mapfile, tile_dir, 0, 11, "pk-pakistan")