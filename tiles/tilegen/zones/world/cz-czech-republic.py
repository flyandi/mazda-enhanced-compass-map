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
    # Region: CZ
    # Region Name: Czech Republic

	render_tiles((14.38111,48.57555,14.70028,48.58138), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.70028,48.58138,14.38111,48.57555), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.04167,48.61582,16.94486,48.6165), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.94486,48.6165,14.04167,48.61582), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.9528,48.6281,16.9703,48.6344), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.9703,48.6344,16.9758,48.64), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.9758,48.64,16.9703,48.6344), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.45861,48.64832,16.9758,48.64), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.04527,48.67693,16.9881,48.6814), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.9881,48.6814,14.04527,48.67693), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.9936,48.6878,16.9881,48.6814), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0036,48.6947,17.0056,48.6981), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0056,48.6981,17.0036,48.6947), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0031,48.7089,17.0047,48.7167), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0047,48.7167,17.0106,48.7219), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0106,48.7219,16.77388,48.72387), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.77388,48.72387,17.0106,48.7219), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.8866,48.73032,16.37055,48.73387), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.37055,48.73387,16.8866,48.73032), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0267,48.75,17.0347,48.7581), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0347,48.7581,16.06055,48.76027), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.06055,48.76027,17.0347,48.7581), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0383,48.765,14.96111,48.76665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.96111,48.76665,17.0383,48.765), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0439,48.7711,14.96111,48.76665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0553,48.7769,14.8125,48.78137), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.8125,48.78137,17.0553,48.7769), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0686,48.7864,13.81463,48.78714), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.81463,48.78714,16.65583,48.78777), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.65583,48.78777,13.81463,48.78714), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.0922,48.7928,16.65583,48.78777), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1,48.8031,16.5275,48.81081), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.5275,48.81081,17.5294,48.8153), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.5294,48.8153,17.5225,48.8175), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.5225,48.8175,17.55,48.8178), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.55,48.8178,17.5225,48.8175), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4039,48.8228,17.4186,48.8267), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.3903,48.8228,17.4186,48.8267), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4186,48.8267,17.1081,48.8269), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1081,48.8269,17.4186,48.8267), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.3764,48.8275,17.1081,48.8269), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.95417,48.82832,17.3764,48.8275), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.5733,48.8297,15.95417,48.82832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.5092,48.8328,17.1139,48.8331), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1139,48.8331,17.5092,48.8328), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4333,48.8336,17.5972,48.8339), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.5972,48.8339,17.4333,48.8336), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1264,48.8378,17.6067,48.8408), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6067,48.8408,17.1461,48.8422), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1461,48.8422,17.6067,48.8408), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4453,48.8436,17.1461,48.8422), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4933,48.8461,17.6331,48.8464), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6331,48.8464,17.4933,48.8461), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.3436,48.8481,17.1628,48.8492), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1628,48.8492,17.3436,48.8481), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6736,48.8511,17.4592,48.8519), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4592,48.8519,17.3069,48.8525), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6456,48.8519,17.3069,48.8525), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.3069,48.8525,17.4592,48.8519), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.4767,48.8525,17.4592,48.8519), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7122,48.8561,17.6861,48.8567), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6861,48.8567,17.7122,48.8561), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6942,48.8578,17.6861,48.8567), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.1889,48.8692,17.2056,48.8697), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.2056,48.8697,17.1889,48.8692), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.2528,48.8708,17.7361,48.8714), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7361,48.8714,17.2528,48.8708), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.2303,48.8731,17.7361,48.8714), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7547,48.8758,15.78528,48.87748), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.78528,48.87748,17.7547,48.8758), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7631,48.8797,13.74083,48.88165), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.74083,48.88165,17.7631,48.8797), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7675,48.8842,13.74083,48.88165), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.66194,48.89638,17.7758,48.9006), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7758,48.9006,13.66194,48.89638), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7822,48.9067,17.7758,48.9006), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.7933,48.9133,17.7822,48.9067), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8011,48.9228,17.8842,48.9244), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8842,48.9244,17.8011,48.9228), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8064,48.9264,17.8903,48.9269), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8903,48.9269,17.8064,48.9264), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8136,48.9278,17.8508,48.9281), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8508,48.9281,17.8136,48.9278), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8964,48.9344,17.8508,48.9281), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.15972,48.9447,13.50167,48.94498), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.50167,48.94498,15.15972,48.9447), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.8975,48.9475,13.50167,48.94498), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9,48.9528,17.8975,48.9475), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9106,48.9636,13.58444,48.96887), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.58444,48.96887,17.9122,48.9697), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9122,48.9697,13.58444,48.96887), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.51611,48.97776,17.9111,48.985), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9111,48.985,15.34055,48.98582), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.34055,48.98582,17.9111,48.985), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.15222,49.00138,13.40144,49.00749), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.40144,49.00749,15.15222,49.00138), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.99416,49.01582,17.9283,49.0169), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9283,49.0169,14.99416,49.01582), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.94,49.0228,18.0175,49.0247), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.0175,49.0247,18.0303,49.0256), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.0303,49.0256,18.0175,49.0247), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.9675,49.0275,17.99,49.0289), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.99,49.0289,17.9675,49.0275), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.0653,49.0358,18.0753,49.0419), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.0753,49.0419,18.0653,49.0358), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.39528,49.05026,18.0753,49.0419), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.0997,49.0694,18.1219,49.0828), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1219,49.0828,18.0997,49.0694), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1303,49.1028,18.1478,49.1156), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1478,49.1156,13.20361,49.11943), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.20361,49.11943,18.1478,49.1156), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1514,49.1244,13.20361,49.11943), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1508,49.13,18.1514,49.1244), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.145,49.1397,18.1453,49.145), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1453,49.145,18.145,49.1397), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1589,49.1653,13.15111,49.17748), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.15111,49.17748,18.1589,49.1653), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1664,49.1964,18.1742,49.2103), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1742,49.2103,18.185,49.2211), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.185,49.2211,18.1881,49.2264), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1881,49.2264,18.1881,49.2294), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1881,49.2294,18.1881,49.2264), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.185,49.2342,18.1881,49.2294), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.1747,49.2417,18.185,49.2342), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.2081,49.2842,18.2128,49.2881), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.2128,49.2881,18.2197,49.29), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.2197,49.29,18.2128,49.2881), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.2522,49.2944,13.02694,49.29832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.02694,49.29832,18.2814,49.3019), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.2814,49.3019,18.3072,49.3039), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3072,49.3039,18.2814,49.3019), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3178,49.3075,18.3072,49.3039), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3333,49.3178,18.3508,49.3231), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3508,49.3231,18.3661,49.3256), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3661,49.3256,12.87861,49.32804), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.87861,49.32804,18.3794,49.3303), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3794,49.3303,12.87861,49.32804), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.3869,49.3358,12.93528,49.34026), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.93528,49.34026,18.3869,49.3358), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4083,49.3611,18.4203,49.3711), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4203,49.3711,18.4203,49.3742), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4203,49.3742,18.4203,49.3711), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4172,49.3783,18.4203,49.3742), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4064,49.3892,18.4369,49.3906), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4369,49.3906,18.4281,49.3908), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4281,49.3908,18.4369,49.3906), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4436,49.3928,18.4047,49.3942), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4047,49.3942,18.4436,49.3928), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4144,49.3961,18.4058,49.3969), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4058,49.3969,18.4144,49.3961), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4692,49.4072,18.4756,49.4131), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4756,49.4131,18.4692,49.4072), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4825,49.4231,18.4756,49.4131), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.66166,49.43387,18.4964,49.4347), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.4964,49.4347,12.66166,49.43387), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5172,49.4442,18.5353,49.4503), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5353,49.4503,18.5172,49.4442), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5461,49.4575,18.5483,49.4636), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5483,49.4636,18.5461,49.4575), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5483,49.4817,18.7447,49.4831), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.7447,49.4831,18.5483,49.4817), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.7306,49.4853,18.5514,49.4869), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5514,49.4869,18.7306,49.4853), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.7631,49.4914,18.6053,49.4917), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6053,49.4917,18.7631,49.4914), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6231,49.4922,18.6053,49.4917), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5622,49.4939,18.6303,49.4942), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6303,49.4942,18.5622,49.4939), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6817,49.4958,18.705,49.4967), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.705,49.4967,18.5864,49.4975), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5864,49.4975,18.5811,49.4978), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.5811,49.4978,18.5864,49.4975), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6481,49.5033,18.6556,49.5042), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.6556,49.5042,18.7794,49.505), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.7794,49.505,18.6556,49.5042), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.8447,49.5158,18.7986,49.5161), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.7986,49.5161,18.8447,49.5158), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.85333,49.51778,18.8144,49.5189), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.8144,49.5189,18.85333,49.51778), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.525,49.63721,18.81166,49.67221), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.81166,49.67221,12.44194,49.70026), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.44194,49.70026,18.63277,49.72248), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.63277,49.72248,12.44194,49.70026), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.40305,49.75999,18.63277,49.72248), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.49944,49.83276,12.40305,49.75999), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.54667,49.91331,18.44722,49.91971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.44722,49.91971,12.5425,49.92027), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.5425,49.92027,18.44722,49.91971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.47444,49.94304,12.5425,49.92027), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.49277,49.97498,17.88778,49.97665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.88778,49.97665,12.49277,49.97498), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.42889,49.98443,17.88778,49.97665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.16027,49.99276,12.42889,49.98443), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.00722,50.01054,17.79889,50.0136), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.79889,50.0136,18.00722,50.01054), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.09111,50.02971,17.79889,50.0136), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((18.04722,50.05859,18.09111,50.02971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.22889,50.09637,16.72527,50.09971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.72527,50.09971,17.75,50.10221), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.75,50.10221,16.72527,50.09971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.62555,50.13526,16.59055,50.13832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.59055,50.13832,17.62555,50.13526), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.59777,50.15832,12.32972,50.16971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.32972,50.16971,12.205,50.17416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.205,50.17416,12.32972,50.16971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.28055,50.1861,16.82138,50.18693), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.82138,50.18693,12.28055,50.1861), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.76249,50.20554,16.82138,50.18693), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.02805,50.23471,12.09694,50.24971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.09694,50.24971,12.26,50.26166), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.26,50.26166,12.09694,50.24971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.66555,50.2747,17.35278,50.27637), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.35278,50.27637,17.66555,50.2747), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.13583,50.27832,12.36472,50.27915), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.36472,50.27915,12.13583,50.27832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.45861,50.3036,17.73333,50.31248), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.73333,50.31248,16.45861,50.3036), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.6961,50.32193,17.35389,50.32249), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.35389,50.32249,17.6961,50.32193), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.09347,50.3242,17.35389,50.32249), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.09347,50.3242,17.35389,50.32249), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.24694,50.33665,12.09347,50.3242), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.48639,50.35138,17.24694,50.33665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.8675,50.41054,16.21055,50.41971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.21055,50.41971,12.99444,50.42304), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.99444,50.42304,12.90389,50.42332), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.90389,50.42332,12.99444,50.42304), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((17.00417,50.42416,12.90389,50.42332), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.89027,50.43942,12.785,50.44637), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((12.785,50.44637,16.89027,50.43942), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.03361,50.50277,13.19111,50.5036), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.19111,50.5036,16.31083,50.50416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.31083,50.50416,13.19111,50.5036), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.39666,50.51888,16.31083,50.50416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.44916,50.57582,13.23944,50.57971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.23944,50.57971,13.32083,50.5811), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.32083,50.5811,13.23944,50.57971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.00972,50.60249,13.47,50.60332), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.47,50.60332,16.00972,50.60249), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.0625,50.61915,13.47,50.60332), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.22138,50.63665,13.38,50.64137), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.38,50.64137,16.22138,50.63665), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.34777,50.65776,15.87055,50.66998), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.87055,50.66998,16.2375,50.67054), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((16.2375,50.67054,15.87055,50.66998), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.99722,50.67971,15.92083,50.68332), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.92083,50.68332,15.99722,50.67971), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.56667,50.7111,13.86194,50.72581), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.86194,50.72581,13.56667,50.7111), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.81833,50.74776,13.86194,50.72581), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.49305,50.78582,13.90778,50.79054), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((13.90778,50.79054,15.49305,50.78582), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.04667,50.80693,14.80166,50.81888), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.80166,50.81888,14.04667,50.80693), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.62527,50.85416,15.31056,50.85832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.31056,50.85832,14.62527,50.85416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.82885,50.86603,15.00528,50.8672), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.00528,50.8672,14.82885,50.86603), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.39361,50.89471,14.56972,50.91609), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.56972,50.91609,15.27083,50.92137), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.27083,50.92137,14.65083,50.92416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.65083,50.92416,15.27083,50.92137), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.40361,50.93249,14.65083,50.92416), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.01917,50.9536,15.2775,50.96998), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.2775,50.96998,14.59889,50.9772), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.59889,50.9772,15.2775,50.96998), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.26083,50.99665,14.99083,51.00694), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.99083,51.00694,15.05111,51.00832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.05111,51.00832,14.99083,51.00694), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((15.17278,51.01888,15.05111,51.00832), mapfile, tile_dir, 0, 11, "cz-czech-republic")
	render_tiles((14.47222,51.03137,15.17278,51.01888), mapfile, tile_dir, 0, 11, "cz-czech-republic")