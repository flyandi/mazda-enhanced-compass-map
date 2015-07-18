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
    # Region: AF
    # Region Name: Afghanistan

	render_tiles((64.13135,29.39416,62.48443,29.4061), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.48443,29.4061,64.13135,29.39416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.78693,29.43388,62.48443,29.4061), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.19302,29.48749,63.5872,29.50388), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.5872,29.50388,64.19302,29.48749), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.27386,29.52389,65.03413,29.5411), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.03413,29.5411,64.27386,29.52389), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.69551,29.58638,61.7436,29.61583), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.7436,29.61583,65.41747,29.64055), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.41747,29.64055,61.7436,29.61583), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.99387,29.8261,66.25664,29.85194), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.25664,29.85194,60.86687,29.86243), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.86687,29.86243,66.25664,29.85194), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.36136,29.9661,60.86687,29.86243), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.2383,30.07138,66.2597,30.11416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.2597,30.11416,66.2383,30.07138), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.25304,30.25999,66.2597,30.11416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.35025,30.45055,66.28192,30.57527), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.28192,30.57527,66.35025,30.45055), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.81276,30.84361,61.80387,30.94582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.80387,30.94582,66.40497,30.94611), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.40497,30.94611,61.80387,30.94582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.56636,30.97777,66.40497,30.94611), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.84721,31.04888,66.68413,31.08611), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.68413,31.08611,61.84721,31.04888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.72302,31.21221,67.28802,31.2136), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.28802,31.2136,66.72302,31.21221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.25636,31.22249,67.28802,31.2136), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.0697,31.23916,61.76582,31.24527), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.76582,31.24527,67.0697,31.23916), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.0347,31.25444,61.76582,31.24527), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.89165,31.2961,67.05164,31.29778), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.05164,31.29778,66.89165,31.2961), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.7711,31.31833,67.03691,31.31861), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.03691,31.31861,61.7711,31.31833), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.79192,31.3411,67.03691,31.31861), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.7997,31.38249,61.7136,31.38333), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.7136,31.38333,67.7997,31.38249), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.61943,31.39582,61.7136,31.38333), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.64636,31.40999,67.76692,31.4111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.76692,31.4111,67.64636,31.40999), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.84387,31.49833,67.73802,31.53083), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.73802,31.53083,67.58109,31.5336), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.58109,31.5336,67.73802,31.53083), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.83304,31.60388,67.98053,31.63583), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.98053,31.63583,67.88748,31.63999), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.88748,31.63999,67.98053,31.63583), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.02969,31.64555,67.88748,31.63999), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.73192,31.69944,68.53775,31.72666), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.53775,31.72666,68.73192,31.69944), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.57164,31.76527,68.44774,31.77277), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.44774,31.77277,68.71248,31.77888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.71248,31.77888,68.44774,31.77277), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.44412,31.79472,68.71248,31.77888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.22163,31.81559,68.54747,31.82916), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.54747,31.82916,68.16608,31.83305), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.16608,31.83305,68.54747,31.82916), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.81026,31.8736,68.16608,31.83305), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.33386,31.94389,60.81026,31.8736), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.28748,32.06915,69.33386,31.94389), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.28304,32.21777,60.85777,32.23472), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.85777,32.23472,69.28304,32.21777), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.24802,32.44387,69.28748,32.52638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.28748,32.52638,60.74165,32.57887), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.74165,32.57887,69.39775,32.58776), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.39775,32.58776,60.74165,32.57887), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.45692,32.68221,69.39497,32.77387), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.39497,32.77387,69.45692,32.68221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.51526,32.87388,69.39497,32.77387), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.49246,33.0086,60.58193,33.07166), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.58193,33.07166,69.56108,33.08193), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.56108,33.08193,69.88107,33.08998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.88107,33.08998,69.56108,33.08193), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.79135,33.12694,70.03358,33.13943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.03358,33.13943,69.79135,33.12694), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.59109,33.16304,70.03358,33.13943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.14497,33.20249,70.06775,33.20499), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.06775,33.20499,70.14497,33.20249), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.32692,33.33194,70.30608,33.3961), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.30608,33.3961,60.85165,33.41805), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.85165,33.41805,70.30608,33.3961), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.19774,33.48582,60.85832,33.49387), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.85832,33.49387,70.19774,33.48582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.9286,33.50443,60.85832,33.49387), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.90165,33.55443,60.64915,33.57499), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.64915,33.57499,60.90165,33.55443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.19662,33.64082,60.52276,33.65304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.52276,33.65304,70.19662,33.64082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.13274,33.73554,60.50526,33.73915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.50526,33.73915,70.13274,33.73554), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.98552,33.75304,60.50526,33.73915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.55415,33.81332,69.98552,33.75304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.90747,33.88193,70.49135,33.94304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.49135,33.94304,70.90053,33.97359), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.90053,33.97359,60.52137,33.99915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.52137,33.99915,70.98108,34.00888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.98108,34.00888,70.90553,34.01332), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.90553,34.01332,70.98108,34.00888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.90276,34.0311,70.90553,34.01332), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.5136,34.15082,71.13553,34.16609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.13553,34.16609,60.5136,34.15082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.67165,34.3136,60.91109,34.31638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.91109,34.31638,60.67165,34.3136), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.89777,34.34582,71.15331,34.36137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.15331,34.36137,60.89777,34.34582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.07303,34.39415,71.15331,34.36137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.75665,34.48332,70.97803,34.51082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.97803,34.51082,60.75665,34.48332), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.7336,34.54166,70.9958,34.55859), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.9958,34.55859,71.09497,34.56805), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.09497,34.56805,60.8636,34.57639), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((60.8636,34.57639,71.09497,34.56805), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.09581,34.67665,71.22552,34.74443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.22552,34.74443,71.09581,34.67665), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.06526,34.81276,71.22552,34.74443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.31386,34.88693,71.49608,34.95943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.49608,34.95943,71.31386,34.88693), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.5433,35.0947,71.61803,35.13137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.61803,35.13137,62.30554,35.14554), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.30554,35.14554,71.61803,35.13137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.11388,35.20165,71.65913,35.20749), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.65913,35.20749,61.11388,35.20165), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.62331,35.22498,71.65913,35.20749), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.10443,35.27915,62.45943,35.28638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.45943,35.28638,71.55357,35.28915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.55357,35.28915,62.45943,35.28638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.1861,35.29694,62.25916,35.29777), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.25916,35.29777,61.1861,35.29694), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.54913,35.32832,62.25916,35.29777), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((62.10082,35.39471,61.80248,35.41109), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.80248,35.41109,62.10082,35.39471), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.6472,35.43694,61.57665,35.45082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.57665,35.45082,61.97221,35.45998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.97221,35.45998,61.57665,35.45082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.23915,35.48109,63.11054,35.48137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.11054,35.48137,61.23915,35.48109), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.60692,35.48193,63.11054,35.48137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.27776,35.52026,61.46499,35.52721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.46499,35.52721,61.27776,35.52026), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.61386,35.56193,61.46499,35.52721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.27872,35.60675,63.09526,35.62609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.09526,35.62609,71.50859,35.62665), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.50859,35.62665,63.09526,35.62609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((61.36582,35.63971,71.50859,35.62665), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.23888,35.69553,71.54524,35.7111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.54524,35.7111,63.23888,35.69553), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.10416,35.82582,63.31721,35.85221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.31721,35.85221,63.11943,35.86193), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.11943,35.86193,63.31721,35.85221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.53888,35.90971,71.38052,35.94609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.38052,35.94609,63.59554,35.9622), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.59554,35.9622,71.29108,35.96859), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.29108,35.96859,63.59554,35.9622), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.06386,36.00027,71.29108,35.96859), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((63.93387,36.03915,71.18802,36.04721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.18802,36.04721,63.93387,36.03915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.05969,36.08804,71.18802,36.04721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.24858,36.13304,64.28247,36.15192), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.28247,36.15192,64.16942,36.16749), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.16942,36.16749,64.28247,36.15192), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.32164,36.21638,64.45886,36.24721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.45886,36.24721,64.32164,36.21638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.58275,36.33582,64.57053,36.35638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.57053,36.35638,71.56302,36.37248), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.56302,36.37248,64.57053,36.35638), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.75304,36.40749,71.81775,36.41666), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.81775,36.41666,71.75304,36.40749), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.62775,36.45998,71.64664,36.46804), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.64664,36.46804,64.62775,36.45998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.79581,36.49193,71.64664,36.46804), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.07469,36.58942,72.0733,36.62887), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.0733,36.62887,64.61525,36.62943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.61525,36.62943,72.0733,36.62887), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.19164,36.6572,71.69524,36.67221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.69524,36.67221,72.19164,36.6572), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.84274,36.69248,71.69524,36.67221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.18164,36.71471,71.84274,36.69248), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.56831,36.74137,72.18164,36.71471), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.49246,36.77193,71.56831,36.74137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.01137,36.8122,74.06218,36.82166), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.06218,36.82166,72.01137,36.8122), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.96303,36.83776,74.06218,36.82166), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.11302,36.87387,74.25192,36.89943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.25192,36.89943,73.77969,36.90109), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.77969,36.90109,74.25192,36.89943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.15886,36.90665,73.77969,36.90109), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.79524,36.92304,68.03552,36.92471), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.03552,36.92471,64.79524,36.92304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.33691,36.95888,74.55801,36.96526), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.55801,36.96526,74.33691,36.95888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.34663,36.98998,74.48219,37.0111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.48219,37.0111,67.91052,37.01443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.91052,37.01443,74.48219,37.0111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.28331,37.01998,67.91052,37.01443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.66359,37.02609,74.56775,37.02647), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.56775,37.02647,72.66359,37.02609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.43219,37.05859,67.88553,37.06137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.88553,37.06137,71.43219,37.05859), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.27831,37.08693,67.79164,37.08832), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.79164,37.08832,68.27831,37.08693), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.24663,37.09415,64.77914,37.09582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.77914,37.09582,69.24663,37.09415), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.41302,37.10443,68.30164,37.1111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.30164,37.1111,68.41302,37.10443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((64.79802,37.12498,68.30164,37.1111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.33304,37.12498,68.30164,37.1111), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.40053,37.13915,68.41219,37.14804), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.41219,37.14804,74.40053,37.13915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.52498,37.16193,74.3922,37.17526), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.3922,37.17526,67.26637,37.18526), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.26637,37.18526,67.77715,37.1858), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.77715,37.1858,67.26637,37.18526), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.22942,37.19193,67.77715,37.1858), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.77414,37.20609,71.44691,37.20776), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.44691,37.20776,67.77414,37.20609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.55746,37.21554,74.81693,37.21915), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.81693,37.21915,67.55746,37.21554), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.79247,37.22942,69.45137,37.22998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.45137,37.22998,73.79247,37.22942), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((72.79886,37.22998,73.79247,37.22942), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.65108,37.23471,67.42441,37.23499), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.42441,37.23499,74.65108,37.23471), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.63441,37.23998,74.89883,37.2403), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.89883,37.2403,73.63441,37.23998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.53719,37.24304,65.07275,37.24443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.07275,37.24443,74.53719,37.24304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.64941,37.24609,67.20026,37.24665), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.20026,37.24665,68.65469,37.24693), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.65469,37.24693,67.20026,37.24665), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.82581,37.24776,68.65469,37.24693), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.53081,37.2486,68.82581,37.24776), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.61996,37.26276,74.66942,37.2661), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.66942,37.2661,73.61996,37.26276), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.90497,37.27221,67.52164,37.27248), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.52164,37.27248,68.90497,37.27221), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.49025,37.28416,68.92441,37.28471), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.92441,37.28471,71.49025,37.28416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.76331,37.30109,73.64941,37.30443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.64941,37.30443,68.99969,37.30776), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.99969,37.30776,73.64941,37.30443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.89165,37.31749,73.08136,37.32054), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.08136,37.32054,68.8094,37.32249), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.8094,37.32249,66.30275,37.3236), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.30275,37.3236,68.8094,37.32249), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((68.85025,37.32471,66.30275,37.3236), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.75276,37.33138,65.6273,37.3332), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.6273,37.3332,73.75276,37.33138), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.6273,37.3332,73.75276,37.33138), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.66525,37.33832,65.6273,37.3332), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.38135,37.34415,66.66525,37.33832), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.53876,37.36051,66.74442,37.36137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.74442,37.36137,66.53876,37.36051), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((66.58691,37.36804,66.74442,37.36137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((67.02164,37.3772,66.58691,37.36804), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.69969,37.39193,74.4008,37.39943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.4008,37.39943,73.15053,37.40082), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.15053,37.40082,74.4008,37.39943), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((74.36386,37.4286,73.77664,37.43443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.77664,37.43443,74.36386,37.4286), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.38274,37.45582,65.64693,37.45888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.64693,37.45888,69.38274,37.45582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((73.31441,37.46332,65.64693,37.45888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.13498,37.52915,65.76608,37.53416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.76608,37.53416,65.70137,37.53693), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.70137,37.53693,65.76608,37.53416), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.95859,37.56499,65.78552,37.56888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((65.78552,37.56888,69.95859,37.56499), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.51581,37.58082,65.78552,37.56888), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((69.91803,37.61193,70.25554,37.62109), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.25554,37.62109,69.91803,37.61193), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.27692,37.74137,71.52885,37.76443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.52885,37.76443,70.27692,37.74137), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.58858,37.81609,71.52885,37.76443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.16553,37.87221,71.59192,37.90304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.59192,37.90304,71.37468,37.90582), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.37468,37.90582,71.59192,37.90304), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.26414,37.91805,70.20386,37.92054), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.20386,37.92054,71.26414,37.91805), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.17024,37.94165,71.54636,37.94248), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.54636,37.94248,70.17024,37.94165), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.2847,38.01998,70.3783,38.05776), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.3783,38.05776,71.2847,38.01998), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.49191,38.16721,71.36386,38.18803), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.36386,38.18803,70.49191,38.16721), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.36302,38.24887,70.58441,38.27859), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.58441,38.27859,71.36302,38.24887), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.59636,38.33415,70.68552,38.37526), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.68552,38.37526,71.10191,38.40609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((71.10191,38.40609,70.6722,38.41443), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.6722,38.41443,71.10191,38.40609), mapfile, tile_dir, 0, 11, "af-afghanistan")
	render_tiles((70.88052,38.45304,70.6722,38.41443), mapfile, tile_dir, 0, 11, "af-afghanistan")