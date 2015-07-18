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
    # Region: SK
    # Region Name: Slovakia

	render_tiles((18.3375,47.74082,17.78416,47.74693), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.78416,47.74693,18.3375,47.74082), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.8125,47.81666,18.84941,47.81844), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.84941,47.81844,18.8125,47.81666), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.60333,47.82804,18.84941,47.81844), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.76694,47.87776,17.60333,47.82804), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.77028,47.95609,18.80833,47.99387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.80833,47.99387,17.1799,48.00182), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1799,48.00182,18.80833,47.99387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.25166,48.02499,18.8275,48.03582), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.8275,48.03582,17.25166,48.02499), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.00125,48.06878,17.08083,48.07999), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.08083,48.07999,19.47638,48.08915), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.47638,48.08915,17.08083,48.07999), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.9186,48.12998,17.0681,48.14415), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0681,48.14415,19.9186,48.12998), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.97668,48.17418,20.06555,48.18027), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.06555,48.18027,16.97668,48.17418), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.535,48.2122,19.66221,48.23193), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.66221,48.23193,19.535,48.2122), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.29194,48.25471,20.15027,48.26027), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.15027,48.26027,20.29194,48.25471), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.94972,48.27804,20.15027,48.26027), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.36472,48.30582,16.94972,48.27804), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.73804,48.35082,21.86277,48.36554), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.86277,48.36554,16.84083,48.36859), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.84083,48.36859,21.86277,48.36554), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.10166,48.3772,16.84083,48.36859), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.15145,48.41206,16.85611,48.41915), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.85611,48.41915,22.15145,48.41206), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.62805,48.44942,16.85611,48.41915), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.89639,48.48721,21.12555,48.49249), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.12555,48.49249,16.89639,48.48721), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.61416,48.49832,21.12555,48.49249), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.50999,48.53777,21.44277,48.57526), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.44277,48.57526,20.82471,48.57582), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.82471,48.57582,21.44277,48.57526), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.15722,48.57665,20.82471,48.57582), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.94486,48.6165,16.9528,48.6281), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.94486,48.6165,16.9528,48.6281), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.9528,48.6281,16.9703,48.6344), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.9703,48.6344,16.9758,48.64), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.9758,48.64,16.9703,48.6344), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.9881,48.6814,22.32639,48.68387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.32639,48.68387,16.9881,48.6814), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((16.9936,48.6878,22.32639,48.68387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0036,48.6947,17.0056,48.6981), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0056,48.6981,17.0036,48.6947), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0031,48.7089,17.0047,48.7167), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0047,48.7167,17.0106,48.7219), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0106,48.7219,17.0047,48.7167), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0267,48.75,17.0347,48.7581), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0347,48.7581,17.0383,48.765), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0383,48.765,17.0439,48.7711), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0439,48.7711,17.0553,48.7769), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0553,48.7769,17.0439,48.7711), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0686,48.7864,17.0922,48.7928), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.0922,48.7928,17.0686,48.7864), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1,48.8031,17.0922,48.7928), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.5294,48.8153,17.5225,48.8175), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.5225,48.8175,17.55,48.8178), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.55,48.8178,17.5225,48.8175), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4039,48.8228,17.4186,48.8267), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.3903,48.8228,17.4186,48.8267), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4186,48.8267,17.1081,48.8269), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1081,48.8269,17.4186,48.8267), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.3764,48.8275,17.1081,48.8269), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.5733,48.8297,17.3764,48.8275), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.5092,48.8328,17.1139,48.8331), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1139,48.8331,17.5092,48.8328), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4333,48.8336,17.5972,48.8339), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.5972,48.8339,17.4333,48.8336), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1264,48.8378,17.6067,48.8408), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6067,48.8408,17.1461,48.8422), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1461,48.8422,17.6067,48.8408), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4453,48.8436,17.1461,48.8422), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4933,48.8461,17.6331,48.8464), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6331,48.8464,17.4933,48.8461), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.3436,48.8481,17.1628,48.8492), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1628,48.8492,17.3436,48.8481), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6736,48.8511,17.6456,48.8519), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6456,48.8519,17.4767,48.8525), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4592,48.8519,17.4767,48.8525), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.4767,48.8525,17.6456,48.8519), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.3069,48.8525,17.6456,48.8519), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7122,48.8561,17.6861,48.8567), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6861,48.8567,17.7122,48.8561), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.6942,48.8578,17.6861,48.8567), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.1889,48.8692,17.2056,48.8697), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.2056,48.8697,17.1889,48.8692), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.2528,48.8708,17.7361,48.8714), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7361,48.8714,17.2528,48.8708), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.2303,48.8731,17.7361,48.8714), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7547,48.8758,17.2303,48.8731), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7631,48.8797,17.7547,48.8758), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7675,48.8842,17.7631,48.8797), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7758,48.9006,17.7822,48.9067), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7822,48.9067,17.7758,48.9006), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.7933,48.9133,17.7822,48.9067), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8011,48.9228,17.8842,48.9244), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8842,48.9244,17.8011,48.9228), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8064,48.9264,17.8903,48.9269), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8903,48.9269,17.8064,48.9264), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8136,48.9278,17.8508,48.9281), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8508,48.9281,17.8136,48.9278), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8964,48.9344,17.8508,48.9281), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.8975,48.9475,17.9,48.9528), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9,48.9528,17.8975,48.9475), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9106,48.9636,17.9122,48.9697), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9122,48.9697,22.46527,48.97554), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.46527,48.97554,17.9122,48.9697), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9111,48.985,22.46527,48.97554), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9283,49.0169,17.94,49.0228), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.94,49.0228,18.0175,49.0247), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.0175,49.0247,18.0303,49.0256), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.0303,49.0256,18.0175,49.0247), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.9675,49.0275,17.99,49.0289), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((17.99,49.0289,17.9675,49.0275), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.54749,49.0322,17.99,49.0289), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.0653,49.0358,22.54749,49.0322), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.0753,49.0419,18.0653,49.0358), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.0997,49.0694,22.558,49.07942), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.558,49.07942,18.1219,49.0828), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1219,49.0828,22.558,49.07942), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.47499,49.09082,18.1219,49.0828), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1303,49.1028,22.47499,49.09082), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1478,49.1156,18.1514,49.1244), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1514,49.1244,18.1508,49.13), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1508,49.13,18.1514,49.1244), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.145,49.1397,18.1453,49.145), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1453,49.145,18.145,49.1397), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1589,49.1653,20.06888,49.17638), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.06888,49.17638,18.1589,49.1653), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.79444,49.19637,18.1664,49.1964), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1664,49.1964,19.79444,49.19637), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1742,49.2103,19.76611,49.21304), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.76611,49.21304,20.0026,49.21377), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.0026,49.21377,19.76611,49.21304), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((22.03027,49.21499,20.0026,49.21377), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.185,49.2211,18.1881,49.2264), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1881,49.2264,20.09944,49.22803), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.09944,49.22803,18.1881,49.2294), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1881,49.2294,20.09944,49.22803), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.93638,49.23109,18.1881,49.2294), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.185,49.2342,19.93638,49.23109), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.1747,49.2417,18.185,49.2342), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.2081,49.2842,18.2128,49.2881), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.2128,49.2881,18.2197,49.29), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.2197,49.29,18.2128,49.2881), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.2522,49.2944,20.91083,49.2961), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.91083,49.2961,18.2522,49.2944), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.2814,49.3019,18.3072,49.3039), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3072,49.3039,18.2814,49.3019), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3178,49.3075,18.3072,49.3039), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.98027,49.31165,18.3178,49.3075), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3333,49.3178,19.80889,49.31971), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.80889,49.31971,18.3333,49.3178), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3508,49.3231,20.18166,49.32443), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.18166,49.32443,18.3661,49.3256), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3661,49.3256,20.18166,49.32443), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3794,49.3303,18.3661,49.3256), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.3869,49.3358,18.3794,49.3303), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4083,49.3611,21.0975,49.36638), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.0975,49.36638,18.4203,49.3711), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4203,49.3711,18.4203,49.3742), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4203,49.3742,18.4203,49.3711), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.56722,49.37776,18.4172,49.3783), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4172,49.3783,20.56722,49.37776), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4064,49.3892,18.4369,49.3906), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4369,49.3906,18.4281,49.3908), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4281,49.3908,18.4369,49.3906), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4436,49.3928,18.4047,49.3942), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4047,49.3942,18.4436,49.3928), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4144,49.3961,18.4058,49.3969), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.98055,49.3961,18.4058,49.3969), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4058,49.3969,18.4144,49.3961), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.64111,49.40192,19.13889,49.40221), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.13889,49.40221,19.64111,49.40192), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.19749,49.40387,19.79166,49.40443), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.79166,49.40443,21.19749,49.40387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.04944,49.40526,19.79166,49.40443), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4692,49.4072,21.04944,49.40526), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.44083,49.40915,18.4692,49.4072), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4756,49.4131,21.51305,49.41637), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.51305,49.41637,20.695,49.41749), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((20.695,49.41749,21.51305,49.41637), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4825,49.4231,20.695,49.41749), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.12583,49.43166,19.19694,49.43387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.19694,49.43387,18.4964,49.4347), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.4964,49.4347,19.19694,49.43387), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.61277,49.43693,18.4964,49.4347), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5172,49.4442,18.5353,49.4503), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5353,49.4503,19.65194,49.45054), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.65194,49.45054,18.5353,49.4503), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.58527,49.4536,21.27888,49.45665), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((21.27888,49.45665,18.5461,49.4575), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5461,49.4575,21.27888,49.45665), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5483,49.4636,18.5461,49.4575), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5483,49.4817,18.7447,49.4831), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.7447,49.4831,18.5483,49.4817), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.7306,49.4853,18.5514,49.4869), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5514,49.4869,18.7306,49.4853), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.7631,49.4914,18.6053,49.4917), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6053,49.4917,18.7631,49.4914), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6231,49.4922,18.6053,49.4917), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5622,49.4939,18.6303,49.4942), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6303,49.4942,18.5622,49.4939), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6817,49.4958,18.705,49.4967), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.705,49.4967,18.5864,49.4975), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5864,49.4975,18.5811,49.4978), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.5811,49.4978,18.5864,49.4975), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.97944,49.49999,18.5811,49.4978), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6481,49.5033,18.6556,49.5042), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.6556,49.5042,18.7794,49.505), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.7794,49.505,18.6556,49.5042), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.85416,49.51471,18.8447,49.5158), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.8447,49.5158,18.7986,49.5161), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.7986,49.5161,18.8447,49.5158), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.85333,49.51778,18.8144,49.5189), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((18.8144,49.5189,18.85333,49.51778), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.33861,49.52943,19.27639,49.53054), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.27639,49.53054,19.33861,49.52943), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.39027,49.56971,19.47555,49.60526), mapfile, tile_dir, 0, 11, "sk-slovakia")
	render_tiles((19.47555,49.60526,19.39027,49.56971), mapfile, tile_dir, 0, 11, "sk-slovakia")