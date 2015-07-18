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
    # Region: CI
    # Region Name: Ivory Coast

	render_tiles((-7.44222,4.34778,-7.52556,4.35145), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.52556,4.35145,-7.44222,4.34778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.56333,4.38722,-7.52556,4.35145), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.54667,4.42416,-7.56333,4.38722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.19861,4.51389,-7.06639,4.53139), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.06639,4.53139,-7.19861,4.51389), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.91222,4.65611,-7.06639,4.53139), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.41111,4.82139,-7.58833,4.90583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.58833,4.90583,-7.53944,4.94055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.53944,4.94055,-7.58833,4.90583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.92806,5.00555,-7.53944,4.94055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.555,5.08333,-5.51083,5.09139), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.51083,5.09139,-2.92858,5.09918), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.92858,5.09918,-7.49528,5.10111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.49528,5.10111,-2.92858,5.09918), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.55361,5.10833,-2.90203,5.11245), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.90203,5.11245,-2.73472,5.11278), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.73472,5.11278,-2.90203,5.11245), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.96839,5.11688,-3.32333,5.11694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.32333,5.11694,-2.96839,5.11688), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.28917,5.11916,-3.32333,5.11694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.01583,5.12389,-4.89417,5.12861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.89417,5.12861,-5.01583,5.12389), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.40639,5.14139,-3.11194,5.14417), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.11194,5.14417,-3.27722,5.14667), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.27722,5.14667,-3.11194,5.14417), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.84167,5.14667,-3.11194,5.14417), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.00945,5.1525,-3.27722,5.14667), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.795,5.16833,-5.30889,5.17222), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.30889,5.17222,-4.795,5.16833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.18639,5.17639,-5.02917,5.17916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.02917,5.17916,-3.325,5.18111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.325,5.18111,-5.02917,5.17916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.11,5.18389,-2.87306,5.18639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.87306,5.18639,-3.72083,5.18694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.72083,5.18694,-2.87306,5.18639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.74667,5.19028,-3.72083,5.18694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.32889,5.19611,-3.82944,5.19694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.82944,5.19694,-5.32889,5.19611), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.53528,5.2,-4.31167,5.20083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.31167,5.20083,-4.53528,5.2), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.19945,5.20944,-4.99722,5.21), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.99722,5.21,-3.19945,5.20944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.7975,5.21333,-5.03333,5.21472), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.03333,5.21472,-4.7975,5.21333), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.73167,5.22805,-3.99861,5.23111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.30083,5.22805,-3.99861,5.23111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.99861,5.23111,-4.73167,5.22805), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.72889,5.23583,-3.99861,5.23111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.77306,5.24166,-4.59778,5.24444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.59778,5.24444,-3.77306,5.24166), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.67028,5.25,-4.59778,5.24444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.005,5.25833,-4.67028,5.25), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.54167,5.27028,-4.47972,5.27194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.47972,5.27194,-3.79583,5.2725), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.79583,5.2725,-4.47972,5.27194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.73889,5.27361,-3.79583,5.2725), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.46445,5.27472,-3.73889,5.27361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.14028,5.27667,-7.46445,5.27472), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.71389,5.28083,-4.14028,5.27667), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.41278,5.28527,-3.88,5.28639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.88,5.28639,-7.41278,5.28527), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.06945,5.28917,-3.88,5.28639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.24195,5.29361,-4.43056,5.29778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.43056,5.29778,-3.99056,5.29916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.99056,5.29916,-4.43056,5.29778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.67028,5.31055,-3.88167,5.32111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.88167,5.32111,-4.67028,5.31055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.25889,5.33528,-7.36778,5.33555), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.36778,5.33555,-3.25889,5.33528), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.71814,5.34631,-2.77333,5.35), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.77333,5.35,-2.71814,5.34631), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.12778,5.35556,-2.77333,5.35), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.40639,5.36444,-3.81806,5.36528), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.81806,5.36528,-7.40639,5.36444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.15472,5.36666,-3.81806,5.36528), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.77139,5.37055,-3.15472,5.36666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.43639,5.43722,-3.77139,5.37055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.36833,5.57222,-2.76403,5.59034), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.76403,5.59034,-7.36833,5.57222), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.77667,5.61333,-2.90639,5.61639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.90639,5.61639,-2.77667,5.61333), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.96056,5.62722,-2.90639,5.61639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.41556,5.65722,-2.96056,5.62722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.01389,5.7075,-2.94861,5.71305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.94861,5.71305,-3.01389,5.7075), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.42472,5.845,-3.00667,5.85778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.00667,5.85778,-7.45806,5.86083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.45806,5.86083,-3.00667,5.85778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.68583,5.90444,-7.64194,5.91972), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.64194,5.91972,-7.68583,5.90444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.7625,5.95055,-7.64194,5.91972), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.77306,6.03583,-7.84528,6.08278), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.84528,6.08278,-7.77306,6.03583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.8225,6.20278,-3.16945,6.27722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.16945,6.27722,-7.90222,6.27833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.90222,6.27833,-3.16945,6.27722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.07333,6.29333,-8.20805,6.29861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.20805,6.29861,-8.07333,6.29333), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.28944,6.34805,-8.36694,6.35444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.36694,6.35444,-8.28944,6.34805), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.40833,6.425,-8.47528,6.43583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.47528,6.43583,-8.38194,6.4425), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.38194,6.4425,-8.47528,6.43583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.45472,6.49,-8.60667,6.50778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.60667,6.50778,-8.45472,6.49), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.50611,6.59416,-3.24917,6.61139), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.24917,6.61139,-8.50611,6.59416), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.18639,6.71639,-8.35833,6.75417), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.35833,6.75417,-3.18639,6.71639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.22417,6.81361,-8.35833,6.75417), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.09,7.06194,-3.02417,7.07305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.02417,7.07305,-3.09,7.06194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.28806,7.18083,-8.36139,7.23944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.36139,7.23944,-8.28806,7.18083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.41833,7.50111,-8.21222,7.545), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.21222,7.545,-8.46896,7.55987), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.46896,7.55987,-8.21222,7.545), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.18667,7.59139,-8.39917,7.61861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.39917,7.61861,-8.18667,7.59139), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.82389,7.82361,-2.78528,7.85361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.78528,7.85361,-2.82389,7.82361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.74111,7.93889,-2.77472,7.94666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.77472,7.94666,-2.74111,7.93889), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.68417,8.0125,-7.94833,8.01861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.94833,8.01861,-2.68417,8.0125), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.05,8.03194,-2.58889,8.04083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.58889,8.04083,-8.05,8.03194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.96917,8.06639,-2.58889,8.04083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.61139,8.13778,-8.07278,8.16389), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.07278,8.16389,-2.61139,8.13778), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.98861,8.19166,-2.48806,8.19777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.48806,8.19777,-7.98861,8.19166), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.25111,8.25222,-2.48806,8.19777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.22111,8.35638,-7.74861,8.37638), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.74861,8.37638,-7.64798,8.37976), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.64798,8.37976,-7.74861,8.37638), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.765,8.42222,-7.88,8.42833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.88,8.42833,-7.765,8.42222), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.83056,8.43583,-8.24229,8.44266), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.24229,8.44266,-7.83056,8.43583), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.82472,8.48611,-8.19833,8.49666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.19833,8.49666,-7.93528,8.49777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.93528,8.49777,-8.19833,8.49666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.67472,8.62083,-7.93528,8.49777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.79472,8.75694,-7.91444,8.76722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.91444,8.76722,-7.79472,8.75694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.58465,8.78153,-7.91444,8.76722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.95639,8.80111,-2.58465,8.78153), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.93667,8.93305,-2.65583,9.01305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.65583,9.01305,-7.895,9.02166), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.895,9.02166,-2.65583,9.01305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.77306,9.05527,-7.73694,9.07277), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.73694,9.07277,-2.77306,9.05527), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.74222,9.09972,-7.73694,9.07277), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.90833,9.18277,-7.92083,9.21777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.92083,9.21777,-7.90833,9.18277), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.6625,9.26916,-2.71611,9.31166), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.71611,9.31166,-2.6625,9.26916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.85972,9.37527,-2.66806,9.38277), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.66806,9.38277,-7.97055,9.3875), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.97055,9.3875,-2.66806,9.38277), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.745,9.39639,-8.05139,9.3975), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.05139,9.3975,-2.745,9.39639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.79111,9.41333,-7.86194,9.42389), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.86194,9.42389,-2.79111,9.41333), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.68685,9.48234,-8.13818,9.49788), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.13818,9.49788,-2.68685,9.48234), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.37695,9.5825,-4.31083,9.59833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.31083,9.59833,-4.37695,9.5825), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.94861,9.65111,-4.50917,9.65444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.50917,9.65444,-4.42639,9.6575), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.42639,9.6575,-4.50917,9.65444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.68555,9.67527,-4.57139,9.68694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.57139,9.68694,-4.28722,9.69027), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.28722,9.69027,-4.57139,9.68694), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.50861,9.71139,-4.60222,9.72138), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.60222,9.72138,-3.06556,9.725), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.06556,9.725,-2.98972,9.72777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-2.98972,9.72777,-3.06556,9.725), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.77528,9.73666,-2.98972,9.72777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.53278,9.74666,-4.74222,9.75027), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.74222,9.75027,-4.53278,9.74666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.78028,9.79083,-4.04083,9.79944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.04083,9.79944,-8.11583,9.80305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.11583,9.80305,-4.04083,9.79944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.1175,9.82916,-4.12167,9.83), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.12167,9.83,-3.1175,9.82916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.17944,9.84,-3.25917,9.84805), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.25917,9.84805,-3.17944,9.84), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.30278,9.85805,-8.10472,9.86722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.10472,9.86722,-4.92833,9.86805), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.92833,9.86805,-8.10472,9.86722), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.86,9.875,-4.92833,9.86805), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.29695,9.90083,-4.97444,9.90528), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.97444,9.90528,-3.29695,9.90083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.19111,9.92389,-3.765,9.93027), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-3.765,9.93027,-3.19111,9.92389), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.16056,9.94416,-4.95,9.94861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.95,9.94861,-8.16056,9.94416), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.13333,10.01055,-4.98,10.05361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-4.98,10.05361,-8.13333,10.01055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-8.00917,10.09944,-7.01056,10.14194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.01056,10.14194,-7.97768,10.16547), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.97768,10.16547,-6.95,10.17194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.95,10.17194,-7.88528,10.17666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.88528,10.17666,-6.95,10.17194), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.0625,10.19111,-7.88528,10.17666), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.98333,10.20861,-6.94611,10.21472), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.94611,10.21472,-5.98333,10.20861), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.19306,10.23388,-7.34583,10.25027), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.34583,10.25027,-6.98222,10.25361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.98222,10.25361,-7.26972,10.25444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.26972,10.25444,-6.98222,10.25361), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.22325,10.25848,-7.26972,10.25444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.88944,10.27583,-5.95611,10.28444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.95611,10.28444,-5.175,10.28944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.175,10.28944,-5.95611,10.28444), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.3225,10.29916,-5.41139,10.30111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.41139,10.30111,-5.3225,10.29916), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.13,10.30472,-5.41139,10.30111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.22694,10.31416,-5.20806,10.3225), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.20806,10.3225,-6.22694,10.31416), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.43083,10.33777,-6.68946,10.3395), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.68946,10.3395,-7.43083,10.33777), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.36,10.35083,-6.94389,10.35305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.94389,10.35305,-7.36,10.35083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.16833,10.35861,-6.94389,10.35305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.87806,10.37555,-6.77611,10.37749), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.77611,10.37749,-5.87806,10.37555), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.69972,10.40639,-6.63806,10.40944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.63806,10.40944,-7.69972,10.40639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.53472,10.41972,-6.63806,10.40944), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.52066,10.43099,-7.53472,10.41972), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.45667,10.45083,-5.65028,10.45111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-5.65028,10.45111,-7.45667,10.45083), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-7.50028,10.46139,-5.65028,10.45111), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.21028,10.50972,-6.24667,10.51639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.24667,10.51639,-6.21028,10.50972), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.41833,10.55055,-6.24667,10.51639), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.38194,10.59055,-6.67667,10.59833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.67667,10.59833,-6.38194,10.59055), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.58528,10.60722,-6.67667,10.59833), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.42028,10.62888,-6.18889,10.63027), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.18889,10.63027,-6.42028,10.62888), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.64509,10.66679,-6.41,10.69305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.64509,10.66679,-6.41,10.69305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.41,10.69305,-6.64509,10.66679), mapfile, tile_dir, 0, 11, "ci-ivory-coast")
	render_tiles((-6.2432,10.73495,-6.41,10.69305), mapfile, tile_dir, 0, 11, "ci-ivory-coast")