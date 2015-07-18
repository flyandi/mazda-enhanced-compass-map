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
    # Region: KH
    # Region Name: Cambodia

	render_tiles((104.018,10.00972,104.04,10.03861), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.018,10.00972,104.04,10.03861), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.04,10.03861,104.018,10.00972), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.9075,10.28528,104.085,10.31639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.085,10.31639,103.9075,10.28528), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.8447,10.35278,104.085,10.31639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.0125,10.4392,103.8447,10.35278), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.4484,10.4225,104.3922,10.45527), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.3922,10.45527,104.4484,10.4225), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.6305,10.49083,103.738,10.49639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.738,10.49639,103.6305,10.49083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.7444,10.51944,104.8214,10.52), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.8214,10.52,103.7444,10.51944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.5986,10.53416,104.8905,10.54), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.8905,10.54,103.8236,10.54083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.8236,10.54083,104.8905,10.54), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.2086,10.55222,103.8236,10.54083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.2508,10.56666,103.9925,10.57083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.9925,10.57083,104.2508,10.56666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.5003,10.62139,103.9925,10.57083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.8611,10.69305,105.0997,10.72639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.0997,10.72639,103.5883,10.73111), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.5883,10.73111,105.0997,10.72639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.6844,10.74055,103.5883,10.73111), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.2033,10.77055,105.0664,10.78139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.0664,10.78139,106.2033,10.77055), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.933,10.83361,105.3555,10.84666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.3555,10.84666,105.8702,10.85166), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8702,10.85166,105.3555,10.84666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.7219,10.86611,103.2166,10.87333), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.2166,10.87333,103.7219,10.86611), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.4169,10.88972,103.1352,10.89361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1352,10.89361,103.4169,10.88972), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.9575,10.9,105.0436,10.90139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.0436,10.90139,105.9575,10.9), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.1458,10.91555,103.2755,10.92889), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.2755,10.92889,103.7,10.92916), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.7,10.92916,103.2755,10.92889), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.4672,10.955,105.1091,10.95555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.1091,10.95555,105.4672,10.955), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.4297,10.96611,106.2144,10.97555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.2144,10.97555,106.1552,10.97583), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.1552,10.97583,106.2144,10.97555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.7916,11.00722,105.7583,11.02139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.7583,11.02139,105.7916,11.00722), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.45,11.05166,105.7583,11.02139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.1591,11.09389,106.0747,11.10666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.0747,11.10666,106.1591,11.09389), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.5553,11.15694,103.5,11.16416), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.5,11.16416,103.5553,11.15694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.103,11.17444,103.5,11.16416), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0783,11.28278,105.8697,11.29694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8697,11.29694,103.1461,11.30611), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1461,11.30611,105.8697,11.29694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8858,11.39974,103.1703,11.42417), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1703,11.42417,103.0636,11.44361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0636,11.44361,105.8991,11.44805), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8991,11.44805,103.0636,11.44361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1261,11.46,103.1064,11.46361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1064,11.46361,103.1261,11.46), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.1416,11.49139,103.1064,11.46361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9936,11.53861,103.0336,11.54528), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0336,11.54528,102.9564,11.55139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9564,11.55139,103.0336,11.54528), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9544,11.56972,102.9564,11.55139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8182,11.59644,105.8214,11.62083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8214,11.62083,103.0436,11.62972), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0436,11.62972,102.9142,11.63342), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9142,11.63342,102.9886,11.635), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9886,11.635,102.9142,11.63342), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.9666,11.64639,102.9886,11.635), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4585,11.66456,102.9665,11.66986), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9665,11.66986,106.4585,11.66456), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0653,11.7025,102.9978,11.70861), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9978,11.70861,102.9747,11.71361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9747,11.71361,102.9669,11.71389), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9669,11.71389,102.9747,11.71361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.0827,11.72639,102.9669,11.71389), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9164,11.74277,106.19,11.74916), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.19,11.74916,102.9164,11.74277), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4236,11.7675,106.0391,11.77639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.0391,11.77639,106.4236,11.7675), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4713,11.86946,106.0391,11.77639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4766,11.97083,106.4202,11.97361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4202,11.97361,106.7303,11.97583), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.7303,11.97583,106.4202,11.97361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.7819,11.99972,106.7303,11.97583), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.7819,12.06583,106.9789,12.08444), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.9789,12.08444,106.7819,12.06583), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4408,12.25361,107.1669,12.27778), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.1669,12.27778,107.4408,12.25361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.7369,12.30361,107.3189,12.32944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.3189,12.32944,102.7369,12.30361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5489,12.35639,107.3189,12.32944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.7886,12.42472,107.5489,12.35639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.583,12.5,102.7886,12.42472), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.5925,12.62666,102.5122,12.665), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.5122,12.665,102.5925,12.62666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.525,12.76694,107.5677,12.80222), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5677,12.80222,102.525,12.76694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5097,12.8825,107.5677,12.80222), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4958,13.00555,102.4958,13.01194), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.4958,13.01194,107.4958,13.00555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.3514,13.26722,107.6364,13.38166), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.6364,13.38166,102.3514,13.26722), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.6289,13.54278,102.5272,13.56778), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.5272,13.56778,102.3602,13.57166), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.3602,13.57166,102.5272,13.56778), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.6227,13.60694,102.3602,13.57166), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.5605,13.6525,102.5633,13.68139), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.5633,13.68139,102.5605,13.6525), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.7222,13.76361,107.4624,13.79621), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4624,13.79621,102.7222,13.76361), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.0919,13.92,105.9391,13.92694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.9391,13.92694,107.4743,13.93001), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4743,13.93001,105.9391,13.92694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.79,13.93444,107.4743,13.93001), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.857,14.00024,107.3852,14.00278), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.3852,14.00278,107.4455,14.005), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4455,14.005,105.8492,14.00722), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.8492,14.00722,107.4455,14.005), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.368,14.03389,106.1744,14.05028), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.1744,14.05028,107.368,14.03389), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.9197,14.10472,105.3641,14.10944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.3641,14.10944,105.7324,14.11149), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.7324,14.11149,105.3641,14.10944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.5172,14.14083,105.5983,14.15166), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.5983,14.15166,105.5172,14.14083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.1019,14.18666,102.958,14.20305), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((102.958,14.20305,105.0694,14.21833), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.0694,14.21833,106.0416,14.22472), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.0416,14.22472,105.0694,14.21833), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.1372,14.23972,105.033,14.24777), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.033,14.24777,105.1372,14.23972), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.3839,14.26083,105.033,14.24777), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.21,14.28444,107.3839,14.26083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.8383,14.3125,103.2286,14.33278), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.2286,14.33278,105.9939,14.34333), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.9939,14.34333,105.2104,14.35256), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.2104,14.35256,106.0283,14.36027), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.0283,14.36027,106.9975,14.36388), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.9975,14.36388,103.9736,14.36444), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.9736,14.36444,104.5716,14.36472), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.5716,14.36472,103.9736,14.36444), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.4433,14.37222,106.2097,14.37555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.2097,14.37555,104.4433,14.37222), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((105.0019,14.38333,104.1875,14.38667), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.1875,14.38667,105.0019,14.38333), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.7008,14.39055,104.1875,14.38667), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.5078,14.39666,107.1089,14.40111), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.1089,14.40111,103.5078,14.39666), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.8805,14.4225,107.4161,14.42944), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4161,14.42944,104.7822,14.43055), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.7822,14.43055,107.4783,14.43083), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4783,14.43083,104.7822,14.43055), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((104.715,14.43777,103.6958,14.44), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.6958,14.44,104.715,14.43777), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.7336,14.4425,107.0502,14.44305), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.0502,14.44305,106.7336,14.4425), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.3324,14.44532,106.2469,14.44639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.3324,14.44532,106.2469,14.44639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.2469,14.44639,106.3324,14.44532), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((103.6591,14.44778,106.2469,14.44639), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.6277,14.46694,103.6591,14.44778), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4383,14.4875,106.2511,14.49333), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.2511,14.49333,106.4383,14.4875), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.4333,14.52333,107.4225,14.54555), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4225,14.54555,107.5355,14.55694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5355,14.55694,107.4541,14.55777), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.4541,14.55777,107.5355,14.55694), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.278,14.55972,107.4541,14.55777), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.2961,14.59861,106.5417,14.59898), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((106.5417,14.59898,107.2961,14.59861), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.3266,14.60694,106.5417,14.59898), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5294,14.69361,107.5459,14.70496), mapfile, tile_dir, 0, 11, "kh-cambodia")
	render_tiles((107.5459,14.70496,107.5294,14.69361), mapfile, tile_dir, 0, 11, "kh-cambodia")