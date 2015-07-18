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
    # Region: BD
    # Region Name: Bangladesh

	render_tiles((90.64886,21.9911,90.60303,22.02722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.64886,21.9911,90.60303,22.02722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.60303,22.02722,90.64636,22.02888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.64636,22.02888,90.60303,22.02722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.77303,22.0736,90.64636,22.02888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.68359,22.375,90.87526,22.41194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.87526,22.41194,90.68359,22.375), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.8647,22.49444,90.64276,22.55305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.64276,22.55305,90.55692,22.60527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.55692,22.60527,90.71082,22.65527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.71082,22.65527,90.55692,22.60527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.57359,22.74749,90.67998,22.77527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.67998,22.77527,90.59387,22.77555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.59387,22.77555,90.67998,22.77527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.31026,20.7486,92.33192,20.76749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.33192,20.76749,92.31026,20.7486), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.2372,20.8786,92.26331,20.91916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.26331,20.91916,92.2372,20.8786), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.2632,21.05447,92.05386,21.15249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.05386,21.15249,92.1933,21.17916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.1933,21.17916,92.05386,21.15249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.60747,21.25055,92.58525,21.26333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.58525,21.26333,92.60747,21.25055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.66942,21.29694,92.58525,21.26333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.20164,21.34249,92.46747,21.35999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.46747,21.35999,92.55357,21.3736), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.55357,21.3736,92.42052,21.38222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.42052,21.38222,92.55357,21.3736), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.63692,21.3911,92.42052,21.38222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.41608,21.43472,91.9622,21.44833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.9622,21.44833,92.41608,21.43472), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.37691,21.47416,91.9622,21.44833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.99193,21.55083,92.02136,21.60249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.02136,21.60249,89.22859,21.64083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.22859,21.64083,91.97914,21.65194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.97914,21.65194,89.20164,21.65583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.20164,21.65583,91.97914,21.65194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.01498,21.66166,89.29276,21.66666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.29276,21.66666,92.01498,21.66166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.96109,21.68222,89.29276,21.66666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.59497,21.70277,89.41275,21.71028), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.41275,21.71028,89.21803,21.71055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.21803,21.71055,89.41275,21.71028), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.55664,21.71138,89.21803,21.71055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.38025,21.71805,89.55664,21.71138), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.94748,21.74027,89.53943,21.75333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.53943,21.75333,89.68303,21.75638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.68303,21.75638,89.59137,21.75833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.59137,21.75833,89.68303,21.75638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.9847,21.76194,89.59137,21.75833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.36304,21.76944,89.46609,21.77555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.46609,21.77555,91.89525,21.77833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.89525,21.77833,89.70164,21.77944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.70164,21.77944,91.89525,21.77833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.14941,21.78388,89.70164,21.77944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.1897,21.79888,89.28053,21.80555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.28053,21.80555,90.1897,21.79888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.18025,21.81444,90.10942,21.81555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.10942,21.81555,89.18025,21.81444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.10889,21.82418,89.78358,21.82583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.78358,21.82583,89.10889,21.82418), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.66887,21.84277,90.05721,21.845), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.05721,21.845,89.62637,21.84722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.62637,21.84722,90.08636,21.84888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.08636,21.84888,90.27248,21.84916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.27248,21.84916,90.08636,21.84888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.02193,21.86694,91.9097,21.86722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.9097,21.86722,90.02193,21.86694), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.88164,21.88444,89.3558,21.88861), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.3558,21.88861,89.48415,21.89055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.48415,21.89055,89.3558,21.88861), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.38193,21.90527,89.51637,21.9075), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.51637,21.9075,89.38193,21.90527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.64192,21.91777,89.51637,21.9075), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.12581,21.9286,89.64192,21.91777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.3497,21.96722,89.97803,21.97166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.97803,21.97166,89.3497,21.96722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.94859,21.97666,90.04526,21.98), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.04526,21.98,92.6013,21.98217), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.6013,21.98217,90.04526,21.98), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.53886,21.98583,92.6013,21.98217), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.99664,22,89.52263,22.00095), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.52263,22.00095,89.99664,22), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.85193,22.02694,90.11693,22.04901), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.11693,22.04901,89.91109,22.05111), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.91109,22.05111,90.11693,22.04901), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.85164,22.06694,90.03497,22.06833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.03497,22.06833,89.85164,22.06694), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.43164,22.07055,90.03497,22.06833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.79526,22.07444,90.43164,22.07055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.53497,22.07972,91.86192,22.08027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.86192,22.08027,89.53497,22.07972), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.51804,22.08305,91.86192,22.08027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.8708,22.09555,89.05984,22.09876), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.38914,22.09555,89.05984,22.09876), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.05984,22.09876,89.8708,22.09555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.58026,22.11361,89.06444,22.11711), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.06444,22.11711,89.58026,22.11361), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.02776,22.1225,89.60136,22.12749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.60136,22.12749,90.22775,22.13027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.22775,22.13027,89.60136,22.12749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.56247,22.13805,90.22775,22.13027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.09636,22.14861,92.60442,22.15166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.60442,22.15166,89.09636,22.14861), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.23219,22.18861,89.58672,22.19907), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.58672,22.19907,90.55998,22.20388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.55998,22.20388,89.58672,22.19907), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.46332,22.21083,90.55998,22.20388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.40498,22.22389,89.46332,22.21083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.84109,22.26722,89.57719,22.26999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.57719,22.26999,89.84109,22.26722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.0072,22.27638,90.39442,22.28166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.39442,22.28166,89.87581,22.28388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.87581,22.28388,90.39442,22.28166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.47581,22.29139,90.61081,22.29499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.61081,22.29499,89.47581,22.29139), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.80193,22.30139,90.61081,22.29499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.62219,22.32944,91.80193,22.30139), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.93997,22.42972,89.9847,22.45027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.9847,22.45027,90.60359,22.46444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.60359,22.46444,88.99774,22.46555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.99774,22.46555,90.60359,22.46444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.97714,22.47684,88.99774,22.46555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.69247,22.49889,89.97714,22.47684), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.95108,22.55583,90.46692,22.57583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.46692,22.57583,90.94609,22.58), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.94609,22.58,91.09998,22.58083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.09998,22.58083,90.94609,22.58), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.52359,22.58194,91.09998,22.58083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.03442,22.58861,90.52359,22.58194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.50998,22.59638,91.24275,22.59944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.24275,22.59944,90.50998,22.59638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.0432,22.61475,91.24275,22.59944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.9425,22.63409,91.26219,22.63777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.26219,22.63777,88.9425,22.63409), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.00803,22.68027,90.49525,22.69444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.49525,22.69444,91.00803,22.68027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.18442,22.71583,91.32442,22.71777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.32442,22.71777,91.18442,22.71583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.51469,22.72194,91.21164,22.72499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.21164,22.72499,92.51469,22.72194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.37219,22.74083,91.47914,22.74833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.47914,22.74833,90.45609,22.74944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.45609,22.74944,91.47914,22.74833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.78026,22.75916,92.46858,22.76167), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.46858,22.76167,90.78026,22.75916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.39609,22.76527,92.46858,22.76167), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.44803,22.83,88.9733,22.83333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.9733,22.83333,90.44803,22.83), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.47887,22.84278,88.9733,22.83333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.4597,22.85332,91.47887,22.84278), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.51089,22.89075,92.39442,22.91777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.45619,22.89075,92.39442,22.91777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.39442,22.91777,90.50443,22.93999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.50443,22.93999,91.61359,22.94305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.61359,22.94305,90.50443,22.93999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.86469,22.96083,91.61359,22.94305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.54663,23.00305,88.86469,22.96083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.4472,23.04833,90.62387,23.05861), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.62387,23.05861,90.4472,23.04833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.45026,23.07055,90.55803,23.07222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.55803,23.07222,90.45026,23.07055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.40358,23.07444,90.55803,23.07222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.80997,23.07444,90.55803,23.07222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.86885,23.08277,91.40358,23.07444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.33997,23.10499,90.59859,23.11749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.59859,23.11749,91.33997,23.10499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.50026,23.14749,90.59859,23.11749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.79524,23.19499,88.98663,23.19583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.98663,23.19583,91.79524,23.19499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.37746,23.20694,88.98663,23.19583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.61247,23.22222,92.34859,23.22361), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.34859,23.22361,90.61247,23.22222), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.8472,23.24277,88.73386,23.24333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.73386,23.24333,88.8472,23.24277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.7672,23.25666,91.42607,23.26194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.42607,23.26194,91.39275,23.26333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.39275,23.26333,91.42607,23.26194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.6622,23.27417,90.58026,23.28389), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.58026,23.28389,92.38359,23.28472), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.38359,23.28472,90.58026,23.28389), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.71136,23.30861,90.62415,23.32083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.62415,23.32083,88.71136,23.30861), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.2822,23.37333,90.59303,23.3786), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.59303,23.3786,91.2822,23.37333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.55026,23.38388,90.59303,23.3786), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.33971,23.39167,90.55026,23.38388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.84497,23.41027,90.33971,23.39167), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.25165,23.46,88.78996,23.46027), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.78996,23.46027,90.25165,23.46), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.6097,23.4761,91.95247,23.47999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.95247,23.47999,90.70137,23.48277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.70137,23.48277,91.95247,23.47999), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.59859,23.49027,90.70137,23.48277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.69109,23.50722,91.96747,23.51277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.96747,23.51277,90.57915,23.51583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.57915,23.51583,91.96747,23.51277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.3119,23.52638,90.57915,23.51583), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.57359,23.54666,90.58664,23.54667), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.58664,23.54667,90.57359,23.54666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.55914,23.57972,90.62886,23.59889), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.62886,23.59889,90.55914,23.57972), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.56804,23.64111,92.03053,23.64527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.03053,23.64527,88.56804,23.64111), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.15913,23.65305,92.20692,23.65444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.20692,23.65444,91.15913,23.65305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.22719,23.65971,92.20692,23.65444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.00497,23.67833,92.28886,23.69611), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.28886,23.69611,92.00497,23.67833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.2522,23.72249,91.9458,23.72277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.9458,23.72277,92.2522,23.72249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.17441,23.73628,91.9458,23.72277), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.59802,23.84721,88.61414,23.87249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.61414,23.87249,88.59802,23.84721), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.73303,23.91583,88.61414,23.87249), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.27386,23.97444,91.36108,23.99527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.36108,23.99527,91.27386,23.97444), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.37607,24.03388,88.72719,24.0536), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.72719,24.0536,91.37607,24.03388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.48219,24.09055,91.3708,24.09777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.3708,24.09777,91.48219,24.09055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.70775,24.10805,91.40469,24.11055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.40469,24.11055,88.70775,24.10805), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.63164,24.11305,91.40469,24.11055), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.71469,24.14888,91.89998,24.15694), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.89998,24.15694,91.75525,24.15833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.75525,24.15833,91.89998,24.15694), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.70831,24.17138,91.84636,24.18138), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.84636,24.18138,88.74385,24.18749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.74385,24.18749,91.84636,24.18138), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.6572,24.22416,91.78914,24.22805), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.78914,24.22805,91.6572,24.22416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.75775,24.23916,91.78914,24.22805), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.73747,24.25083,91.75775,24.23916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.93246,24.27555,88.73747,24.25083), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.55469,24.30944,88.69107,24.31666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.69107,24.31666,88.55469,24.30944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.99774,24.32611,91.91774,24.32666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.91774,24.32666,91.99774,24.32611), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.94662,24.34944,91.91774,24.32666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.97469,24.37888,91.94662,24.34944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.09802,24.37888,91.94662,24.34944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.13525,24.41416,91.97469,24.37888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.12552,24.50861,92.13525,24.41416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.07478,24.64052,88.0433,24.68416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.0433,24.68416,88.07478,24.64052), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.24858,24.74249,88.0433,24.68416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.38942,24.85777,88.3322,24.86749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.3322,24.86749,92.38942,24.85777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.1633,24.88194,92.49661,24.88993), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.49661,24.88993,92.24802,24.89638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.24802,24.89638,92.49661,24.88993), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.28165,24.90722,92.24802,24.89638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.13525,24.91832,92.28165,24.90722), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.21526,24.94888,88.13525,24.91832), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.43802,25.02305,92.40219,25.03333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.40219,25.03333,88.43802,25.02305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.4758,25.13943,90.41248,25.14888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.41248,25.14888,90.82164,25.14972), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.82164,25.14972,90.41248,25.14888), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.77608,25.1761,92.09358,25.17749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.09358,25.17749,90.77608,25.1761), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((92.00304,25.18193,88.55885,25.18305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.55885,25.18305,92.00304,25.18193), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((90.32776,25.18499,88.55885,25.18305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.45081,25.18777,90.32776,25.18499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.94469,25.19694,88.45081,25.18777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((91.25775,25.20777,88.94469,25.19694), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.00636,25.27805,89.83775,25.2961), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.83775,25.2961,89.00581,25.30527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.00581,25.30527,89.83775,25.2961), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.88246,25.32638,89.00581,25.30527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.81441,25.37388,88.82164,25.40166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.82164,25.40166,89.81441,25.37388), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.84108,25.45416,88.70108,25.46805), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.70108,25.46805,88.84108,25.45416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.77248,25.51639,88.52109,25.53194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.52109,25.53194,88.77248,25.51639), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.8633,25.64749,89.82025,25.73749), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.82025,25.73749,89.82655,25.7674), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.82655,25.7674,88.15831,25.77833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.15831,25.77833,89.82655,25.7674), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.28748,25.79416,88.15831,25.77833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.25748,25.815,88.28748,25.79416), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.10524,25.86916,89.85344,25.89475), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.85344,25.89475,88.10524,25.86916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.8633,25.94138,89.86165,25.94386), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.86165,25.94386,89.8633,25.94138), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.85364,25.95596,89.55302,25.96221), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.55302,25.96221,89.85364,25.95596), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.13107,25.98916,89.43274,25.99833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.43274,25.99833,89.51276,26.0036), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.51276,26.0036,89.43274,25.99833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.1833,26.03389,89.51276,26.0036), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.63637,26.07083,88.1833,26.03389), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.14941,26.14194,88.17969,26.14333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.17969,26.14333,89.14941,26.14194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.58914,26.1536,88.17969,26.14333), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.62996,26.22333,89.67358,26.22833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.67358,26.22833,89.00798,26.23017), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.00798,26.23017,88.84941,26.23166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.00798,26.23017,88.84941,26.23166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.84941,26.23166,89.00798,26.23017), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.05524,26.24249,88.84941,26.23166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.6994,26.26944,88.88359,26.27833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.88359,26.27833,88.92775,26.27944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.92775,26.27944,88.88359,26.27833), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.67163,26.28944,88.92775,26.27944), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.7636,26.3,88.37386,26.30305), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.37386,26.30305,88.7636,26.3), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.74135,26.3236,88.99469,26.33555), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.99469,26.33555,88.74135,26.3236), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.52248,26.35583,88.91803,26.36777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.91803,26.36777,88.45747,26.36916), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.45747,26.36916,88.91803,26.36777), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((89.07469,26.38499,88.69412,26.39194), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.69412,26.39194,89.07469,26.38499), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.95386,26.44611,88.49469,26.45), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.49469,26.45,88.35358,26.45166), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.35358,26.45166,88.49469,26.45), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.3347,26.48666,88.37802,26.48943), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.37802,26.48943,88.3347,26.48666), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.42914,26.555,88.41942,26.59638), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.41942,26.59638,88.40108,26.62527), mapfile, tile_dir, 0, 11, "bd-bangladesh")
	render_tiles((88.40108,26.62527,88.41942,26.59638), mapfile, tile_dir, 0, 11, "bd-bangladesh")