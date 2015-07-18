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
    # Region: VN
    # Region Name: Vietnam

	render_tiles((104.9433,8.57416,104.7258,8.59694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7258,8.59694,104.7208,8.61389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7208,8.61389,105.1247,8.62583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1247,8.62583,104.7208,8.61389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8211,8.65166,105.1247,8.62583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8664,8.69583,104.8472,8.72305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8472,8.72305,104.9025,8.7275), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9025,8.7275,104.8472,8.72305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8175,8.7675,104.7972,8.79555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7972,8.79555,105.3314,8.80555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.3314,8.80555,104.7972,8.79555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9319,8.82111,105.3314,8.80555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8136,8.87472,104.9319,8.82111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.4644,9.06027,105.5969,9.15306), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.5969,9.15306,104.814,9.18712), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.814,9.18712,104.8164,9.20472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8164,9.20472,104.814,9.18712), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8852,9.25833,104.8164,9.20472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1767,9.35611,106.1927,9.40027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1927,9.40027,106.1641,9.41222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1641,9.41222,106.1927,9.40027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2066,9.46778,106.2097,9.51444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2097,9.51444,106.3864,9.53833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3864,9.53833,106.4997,9.54805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4997,9.54805,106.3864,9.53833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8369,9.56,106.4997,9.54805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5719,9.635,104.8369,9.56), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1614,9.71055,106.5772,9.72778), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5772,9.72778,106.1614,9.71055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0597,9.75694,106.5772,9.72778), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6122,9.80889,104.9014,9.81555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9014,9.81555,106.5825,9.82194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5825,9.82194,104.9014,9.81555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6816,9.84916,106.5825,9.82194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6947,9.88028,105.0997,9.88888), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0997,9.88888,106.6947,9.88028), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.9714,9.9175,106.3861,9.93027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3861,9.93027,105.045,9.93139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.045,9.93139,106.3861,9.93027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.085,9.93444,105.045,9.93139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8655,9.95722,106.6383,9.97055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6383,9.97055,106.5669,9.97472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5669,9.97472,106.6383,9.97055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3639,9.99833,105.8255,10.00555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8255,10.00555,106.4908,10.00777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4908,10.00777,105.8255,10.00555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0733,10.01166,106.4908,10.00777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4739,10.04639,106.7764,10.08), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7764,10.08,104.8858,10.09916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8858,10.09916,104.9772,10.10833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9772,10.10833,104.8858,10.09916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1861,10.12555,106.8039,10.12889), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8039,10.12889,106.1861,10.12555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6069,10.14666,106.8039,10.12889), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6719,10.17111,106.7211,10.19555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7211,10.19555,106.1717,10.20111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1717,10.20111,106.7211,10.19555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3636,10.21083,104.7914,10.21444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7914,10.21444,106.3636,10.21083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.715,10.22916,106.1142,10.22944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1142,10.22944,104.715,10.22916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3233,10.235,106.1142,10.22944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7772,10.27278,104.5761,10.27722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5761,10.27722,104.538,10.28111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.538,10.28111,106.7261,10.28139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7261,10.28139,104.538,10.28111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4322,10.30472,106.7261,10.28139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.4516,10.37027,104.5019,10.37555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5019,10.37555,107.2528,10.37611), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2528,10.37611,104.5019,10.37555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7936,10.37972,107.2528,10.37611), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.4484,10.4225,106.5883,10.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2172,10.4225,106.5883,10.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5883,10.42944,104.4484,10.4225), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5891,10.43972,106.5883,10.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.9664,10.46611,106.7386,10.46944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7386,10.46944,106.9664,10.46611), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1711,10.47694,106.5799,10.47762), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5799,10.47762,107.1711,10.47694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.028,10.47833,106.5799,10.47762), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5775,10.48142,107.028,10.47833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6641,10.49166,107.5136,10.50083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5136,10.50083,106.6641,10.49166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7342,10.51111,104.8214,10.52), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8214,10.52,106.7342,10.51111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5986,10.53416,104.8905,10.54), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8905,10.54,107.0378,10.54028), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0378,10.54028,104.8905,10.54), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0216,10.54555,107.0378,10.54028), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.938,10.60028,107.0175,10.61639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0175,10.61639,107.6869,10.61972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6869,10.61972,107.0175,10.61639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.755,10.67361,106.7714,10.68444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7714,10.68444,106.755,10.67361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9978,10.69916,107.9697,10.70389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9697,10.70389,107.9978,10.69916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0997,10.72639,107.9697,10.70389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2033,10.77055,105.0664,10.78139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0664,10.78139,106.2033,10.77055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.933,10.83361,105.3555,10.84666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.3555,10.84666,105.8702,10.85166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8702,10.85166,105.3555,10.84666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.0747,10.88194,105.9575,10.9), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.9575,10.9,105.0436,10.90139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0436,10.90139,105.9575,10.9), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1458,10.91555,105.0436,10.90139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2055,10.94194,108.2647,10.94639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2647,10.94639,108.3411,10.94916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.3411,10.94916,108.2647,10.94639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.4672,10.955,105.1091,10.95555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1091,10.95555,105.4672,10.955), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.4297,10.96611,106.2144,10.97555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2144,10.97555,106.1552,10.97583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1552,10.97583,106.2144,10.97555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7916,11.00722,108.3716,11.01972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.3716,11.01972,105.7583,11.02139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7583,11.02139,108.3716,11.01972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.4786,11.04889,105.7583,11.02139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1591,11.09389,106.0747,11.10666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0747,11.10666,106.1591,11.09389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.513,11.13222,106.0747,11.10666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.7153,11.16694,108.6372,11.18416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.6372,11.18416,108.7153,11.16694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8697,11.29694,108.9575,11.30805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.9575,11.30805,108.805,11.31417), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.805,11.31417,108.9155,11.31639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.9155,11.31639,108.805,11.31417), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.0241,11.35778,108.9155,11.31639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8858,11.39974,109.0241,11.35778), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8991,11.44805,105.8858,11.39974), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.133,11.56694,105.8182,11.59644), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8182,11.59644,109.0194,11.61583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.0194,11.61583,105.8214,11.62083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8214,11.62083,109.0194,11.61583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.9666,11.64639,106.4585,11.66456), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4585,11.66456,105.9666,11.64639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2455,11.72472,106.19,11.74916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.19,11.74916,106.4236,11.7675), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4236,11.7675,106.0391,11.77639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0391,11.77639,106.4236,11.7675), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1536,11.81833,109.1266,11.83667), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1266,11.83667,109.1536,11.81833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2766,11.86111,109.2014,11.86944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2014,11.86944,106.4713,11.86946), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4713,11.86946,109.2014,11.86944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2155,11.88472,109.1364,11.89333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1364,11.89333,109.2155,11.88472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1891,11.92555,109.1364,11.89333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2828,11.96277,106.4766,11.97083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4766,11.97083,106.4202,11.97361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4202,11.97361,106.7303,11.97583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7303,11.97583,106.4202,11.97361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1959,12.00383,106.7303,11.97583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2222,12.04444,106.7819,12.06583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7819,12.06583,106.9789,12.08444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.9789,12.08444,109.1905,12.09555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1905,12.09555,106.9789,12.08444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2066,12.24472,107.4408,12.25361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4408,12.25361,109.2066,12.24472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1669,12.27778,107.4408,12.25361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3189,12.32944,109.2947,12.34666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2947,12.34666,107.5489,12.35639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5489,12.35639,109.2947,12.34666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2066,12.38778,109.3469,12.39055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3469,12.39055,109.2066,12.38778), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2058,12.44028,109.1591,12.44527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1591,12.44527,109.2058,12.44028), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.583,12.5,109.2353,12.52555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2353,12.52555,107.583,12.5), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.4414,12.59194,109.1972,12.62972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1972,12.62972,109.3766,12.66361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3766,12.66361,109.4136,12.66694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.4136,12.66694,109.3766,12.66361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.425,12.69278,109.3869,12.71805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3869,12.71805,109.425,12.69278), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3447,12.79111,107.5677,12.80222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5677,12.80222,109.3447,12.79111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3705,12.82139,107.5677,12.80222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.4591,12.85305,107.5097,12.8825), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5097,12.8825,109.4591,12.85305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.4403,12.945,107.4958,13.00555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4958,13.00555,109.4403,12.945), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3248,13.07759,107.4958,13.00555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3111,13.24083,109.2894,13.2725), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2894,13.2725,109.3128,13.2925), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3128,13.2925,109.2894,13.2725), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2875,13.32972,109.3128,13.2925), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6364,13.38166,109.3036,13.40916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3036,13.40916,107.6364,13.38166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2308,13.43861,109.2955,13.45833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2955,13.45833,109.3405,13.46083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3405,13.46083,109.2955,13.45833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2489,13.49083,109.3405,13.46083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6289,13.54278,109.2889,13.57306), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2889,13.57306,107.6289,13.54278), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2402,13.63,109.2111,13.63722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2111,13.63722,109.2402,13.63), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2983,13.74583,109.2675,13.77083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2675,13.77083,109.2983,13.74583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4624,13.79621,109.2675,13.77083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.3089,13.88361,109.24,13.88833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.24,13.88833,109.3089,13.88361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2728,13.89972,109.24,13.88833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4743,13.93001,109.2728,13.89972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3852,14.00278,107.4455,14.005), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4455,14.005,107.3852,14.00278), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.368,14.03389,107.4455,14.005), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.2297,14.12916,109.1697,14.16083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1697,14.16083,109.2297,14.12916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3839,14.26083,109.1697,14.16083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.1308,14.38472,107.4161,14.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4161,14.42944,107.4783,14.43083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4783,14.43083,107.4161,14.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5355,14.55694,109.0805,14.56889), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.0805,14.56889,107.5355,14.55694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5459,14.70496,109.083,14.70972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((109.083,14.70972,107.5459,14.70496), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5136,14.80361,107.5458,14.83694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5458,14.83694,107.5136,14.80361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5847,14.89944,108.9616,14.92111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.9616,14.92111,107.5847,14.89944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.478,14.97361,108.9616,14.92111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4797,15.03778,107.58,15.04416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.58,15.04416,107.4797,15.03778), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.8994,15.155,108.9464,15.24472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.8622,15.155,108.9464,15.24472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.9464,15.24472,107.6952,15.27083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6952,15.27083,108.8861,15.27667), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.8861,15.27667,107.6952,15.27083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.62,15.34277,108.7878,15.36361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.7878,15.36361,107.62,15.34277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.8119,15.40639,108.668,15.44), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.668,15.44,108.628,15.45222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.628,15.45222,108.668,15.44), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.6266,15.48472,108.6914,15.48611), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.6914,15.48611,108.6266,15.48472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.6439,15.50861,107.4591,15.51111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4591,15.51111,108.6439,15.50861), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.6791,15.51472,107.4591,15.51111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2616,15.65027,108.493,15.6825), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.493,15.6825,107.2616,15.65027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2536,15.72639,107.1877,15.75944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1877,15.75944,107.2536,15.72639), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.175,15.795,107.1877,15.75944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2066,15.86139,108.3847,15.895), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.3847,15.895,107.3983,15.91389), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3983,15.91389,108.3847,15.895), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4666,16.00889,108.2708,16.01278), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2708,16.01278,107.4666,16.00889), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2492,16.0325,108.2708,16.01278), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3341,16.05694,108.2492,16.0325), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4605,16.08249,108.2591,16.08555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2591,16.08555,107.4605,16.08249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.1519,16.10333,108.2591,16.08555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.3158,16.13138,108.1383,16.14694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.1383,16.14694,108.2439,16.15527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2439,16.15527,108.1383,16.14694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1544,16.19777,108.2008,16.20333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.2008,16.20333,107.1544,16.19777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1489,16.26083,108.063,16.28166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.063,16.28166,107.8469,16.29138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.8469,16.29138,107.9289,16.29527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9289,16.29527,106.9891,16.29777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.9891,16.29777,107.9289,16.29527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0911,16.30194,106.9891,16.29777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9978,16.31666,107.0911,16.30194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.043,16.34083,107.933,16.35527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.933,16.35527,107.7916,16.35805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7916,16.35805,107.933,16.35527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.8383,16.36583,107.7916,16.35805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.9072,16.39249,107.8383,16.36583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7347,16.43055,106.8983,16.44999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8983,16.44999,106.7842,16.45356), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7842,16.45356,106.8983,16.44999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6744,16.4786,106.7842,16.45356), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7008,16.51333,107.6472,16.51472), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6472,16.51472,107.7008,16.51333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8852,16.53249,106.848,16.53722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.848,16.53722,106.8852,16.53249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.543,16.59805,106.633,16.60499), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.633,16.60499,107.543,16.59805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5868,16.62709,107.555,16.63), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.555,16.63,106.5868,16.62709), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4472,16.63916,107.555,16.63), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4566,16.65583,107.4472,16.63916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.325,16.80444,106.5563,16.89998), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5563,16.89998,107.2178,16.90249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2178,16.90249,106.5563,16.89998), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4711,16.98249,106.5611,16.99694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5611,16.99694,106.4711,16.98249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1172,17.01176,107.13,17.01194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.13,17.01194,107.1172,17.01176), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.1247,17.06944,107.13,17.01194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3614,17.13555,107.1247,17.06944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3355,17.23416,106.89,17.24083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.89,17.24083,106.2536,17.24582), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2536,17.24582,106.89,17.24083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2116,17.26166,106.2536,17.24582), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2875,17.29389,106.2116,17.26166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0436,17.40277,106.675,17.42944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.675,17.42944,106.0436,17.40277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8586,17.62055,105.785,17.65944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.785,17.65944,106.5108,17.66527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5108,17.66527,105.785,17.65944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.403,17.67778,106.5108,17.66527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4711,17.70083,106.3702,17.70222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3702,17.70222,106.4711,17.70083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4941,17.71222,106.4219,17.7136), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4219,17.7136,106.4941,17.71222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7364,17.71749,106.4219,17.7136), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4303,17.72444,106.435,17.73111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.435,17.73111,106.4303,17.72444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4472,17.83138,105.615,17.87138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.615,17.87138,106.4472,17.83138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4828,17.91861,105.6247,17.94777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.6247,17.94777,106.5108,17.95333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5108,17.95333,105.6247,17.94777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4319,18.06667,106.3511,18.11305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3511,18.11305,106.435,18.11972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.435,18.11972,106.3511,18.11305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.383,18.16027,105.4997,18.17999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.4997,18.17999,105.383,18.16027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.4336,18.20777,105.4997,18.17999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1983,18.24194,106.1464,18.265), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1464,18.265,106.1983,18.24194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.088,18.29083,106.1464,18.265), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.185,18.31833,106.088,18.29083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1916,18.37833,105.1319,18.40999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1319,18.40999,105.1916,18.37833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1033,18.46693,105.9039,18.47833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.9039,18.47833,105.1033,18.46693), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1494,18.59972,105.1897,18.60249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1897,18.60249,105.1494,18.59972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8172,18.6125,105.1897,18.60249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1938,18.64249,105.8172,18.6125), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.1344,18.70638,105.7639,18.70749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7639,18.70749,105.1344,18.70638), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7452,18.72222,105.7639,18.70749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9575,18.73888,105.7452,18.72222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7552,18.77,104.9575,18.73888), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6722,18.83694,105.7552,18.77), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.6297,18.91638,104.5208,18.97943), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5208,18.97943,104.4536,18.9811), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.4536,18.9811,104.5208,18.97943), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.6075,19.00416,104.4536,18.9811), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7322,19.09972,104.2239,19.13249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.2239,19.13249,105.7322,19.09972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.733,19.22833,103.9647,19.25361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.9647,19.25361,105.733,19.22833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.8791,19.29343,105.8125,19.29777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8125,19.29777,103.8791,19.29343), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.9877,19.39916,105.7675,19.40388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7675,19.40388,103.9877,19.39916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0698,19.40983,105.7675,19.40388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8094,19.42166,104.0698,19.40983), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.1225,19.49722,105.8094,19.42166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.523,19.60471,104.0366,19.6111), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0366,19.6111,104.645,19.61555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.645,19.61555,105.8236,19.61749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8236,19.61749,104.645,19.61555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.1391,19.65999,104.3206,19.6615), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.3206,19.6615,104.1391,19.65999), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6616,19.66888,104.3206,19.6615), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.3086,19.68694,104.1641,19.68832), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.1641,19.68832,104.3086,19.68694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.4136,19.69333,104.0404,19.69444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0404,19.69444,104.4136,19.69333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0404,19.69444,104.4136,19.69333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8622,19.70972,104.0404,19.69444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.93,19.7675,104.8364,19.79138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8364,19.79138,105.93,19.7675), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.93,19.82471,104.8461,19.85332), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8461,19.85332,104.7788,19.87416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7788,19.87416,104.8461,19.85332), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.9541,19.92083,104.8525,19.94527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8525,19.94527,106.0719,19.96), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0719,19.96,104.8525,19.94527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1697,19.98305,104.96,19.98693), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.96,19.98693,106.1697,19.98305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.0286,19.99583,104.96,19.98693), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.9808,20.0186,106.0286,19.99583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3269,20.14111,104.935,20.18555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.935,20.18555,106.4561,20.20722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4561,20.20722,106.3928,20.21444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3928,20.21444,104.6805,20.21888), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6805,20.21888,106.3928,20.21444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5569,20.26722,104.6788,20.28055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6788,20.28055,106.5667,20.28388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5667,20.28388,104.6788,20.28055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7139,20.29027,106.5667,20.28388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.4998,20.30212,104.7139,20.29027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7069,20.34555,106.4998,20.30212), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5105,20.40805,104.6025,20.41971), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6025,20.41971,104.5105,20.40805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5953,20.43777,104.3886,20.43861), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.3886,20.43861,106.5953,20.43777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5619,20.45833,104.3813,20.46055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.3813,20.46055,106.5619,20.45833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5511,20.51638,104.4708,20.53722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.4708,20.53722,106.5803,20.54361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5803,20.54361,104.4708,20.53722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6278,20.61416,103.6872,20.65833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.6872,20.65833,104.6436,20.66027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.6436,20.66027,103.6872,20.65833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7064,20.66277,104.6436,20.66027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.7383,20.66999,104.5853,20.67388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5853,20.67388,106.8047,20.67444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8047,20.67444,104.5853,20.67388), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7533,20.70555,103.7294,20.72416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.7294,20.72416,106.7533,20.70555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.7894,20.7486,103.7294,20.72416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.4169,20.79416,103.4486,20.82777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.4486,20.82777,104.348,20.83444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.348,20.83444,103.4486,20.82777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.8036,20.84583,103.1664,20.85083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.1664,20.85083,103.8036,20.84583), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7003,20.86861,106.8583,20.87166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8583,20.87166,106.7003,20.86861), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.9411,20.90055,106.85,20.91222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.85,20.91222,106.9411,20.90055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.195,20.93139,106.7597,20.9325), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7597,20.9325,107.195,20.93139), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7747,20.93583,106.7597,20.9325), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0072,20.94777,107.0691,20.95194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0691,20.95194,107.0072,20.94777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0769,20.95832,107.0691,20.95194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7741,20.96749,106.8286,20.97194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8286,20.97194,106.7741,20.96749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.1091,20.97721,106.8858,20.97833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8858,20.97833,104.1091,20.97721), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0128,20.98944,107.2139,20.9911), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2139,20.9911,107.0128,20.98944), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6661,21.00222,106.8069,21.00638), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8069,21.00638,107.3508,21.00777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3508,21.00777,107.093,21.00888), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.093,21.00888,107.3508,21.00777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.0308,21.06,102.9669,21.07861), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9669,21.07861,103.0308,21.06), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3869,21.17555,107.3594,21.22749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3594,21.22749,102.9166,21.23444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9166,21.23444,107.3594,21.22749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.823,21.26083,107.3725,21.27138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3725,21.27138,102.823,21.26083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.445,21.28611,107.5555,21.29222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5555,21.29222,107.445,21.28611), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9005,21.30277,107.5555,21.29222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.6092,21.32305,107.5403,21.32555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.5403,21.32555,107.6092,21.32305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9653,21.43083,102.875,21.43277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.875,21.43277,107.9653,21.43083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7608,21.43722,102.875,21.43277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9186,21.44444,107.7608,21.43722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9438,21.45471,107.9186,21.44444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9647,21.47416,107.7944,21.47721), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7944,21.47721,107.9647,21.47416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((108.0775,21.48166,107.7944,21.47721), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9141,21.52277,107.8214,21.52361), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.8214,21.52361,107.9141,21.52277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9861,21.54264,102.9619,21.54416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9619,21.54416,107.9861,21.54264), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.9166,21.58888,107.4858,21.59777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4858,21.59777,107.3633,21.60499), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3633,21.60499,107.4858,21.59777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4028,21.61777,107.7039,21.62499), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7039,21.62499,107.4028,21.61777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4872,21.64166,107.4539,21.65805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.4539,21.65805,102.6711,21.66249), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.6711,21.66249,102.7469,21.66638), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.7469,21.66638,107.7828,21.66714), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.7828,21.66714,102.7469,21.66638), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9889,21.68277,107.7828,21.66714), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.2119,21.71194,102.8614,21.71832), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8614,21.71832,107.2119,21.71194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9394,21.72471,107.3014,21.7311), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.3014,21.7311,102.8175,21.7336), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8175,21.7336,107.3014,21.7311), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.965,21.74888,102.8175,21.7336), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8583,21.82694,102.8072,21.82805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8072,21.82805,102.8583,21.82694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.013,21.83499,102.8072,21.82805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8289,21.84777,107.013,21.83499), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.6405,21.86305,102.8289,21.84777), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0483,21.91444,107.0228,21.9386), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((107.0228,21.9386,102.5055,21.96221), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.5055,21.96221,106.6922,21.96582), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6922,21.96582,102.5055,21.96221), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6752,21.98582,106.6922,21.96582), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.7466,22.01527,106.6752,21.98582), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6952,22.05722,106.7466,22.01527), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.4194,22.12055,106.6952,22.05722), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.2675,22.21777,102.4194,22.12055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6536,22.32861,106.578,22.3375), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.578,22.3375,106.6536,22.32861), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.5614,22.36416,106.578,22.3375), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.1405,22.39589,102.2508,22.41305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.2508,22.41305,102.1797,22.42833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.1797,22.42833,103.0305,22.43555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.0305,22.43555,102.1797,22.42833), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.0708,22.44527,103.0305,22.43555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.9286,22.48193,102.2644,22.48416), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.2644,22.48416,102.9286,22.48193), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.0694,22.49277,103.9647,22.4988), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.9647,22.4988,103.0694,22.49277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.163,22.53805,104.0169,22.54721), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0169,22.54721,103.163,22.53805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6803,22.57277,103.8566,22.5811), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.8566,22.5811,106.6803,22.57277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.8677,22.59777,103.5494,22.60332), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.5494,22.60332,106.6072,22.60694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6072,22.60694,103.5494,22.60332), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6355,22.61555,106.6072,22.60694), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.18,22.62444,102.415,22.63277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.415,22.63277,103.18,22.62444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.585,22.64721,102.415,22.63277), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.2755,22.66916,102.3816,22.67083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.3816,22.67083,103.2755,22.66916), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.3622,22.68666,102.6836,22.69083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.6836,22.69083,104.0352,22.6911), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.0352,22.6911,102.6836,22.69083), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.5602,22.69749,103.563,22.69805), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.563,22.69805,102.5602,22.69749), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.2619,22.73777,102.5214,22.765), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.5214,22.765,102.4694,22.77138), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((102.4694,22.77138,102.5214,22.765), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.3808,22.78194,103.6528,22.784), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.6528,22.784,103.3808,22.78194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.1025,22.79222,103.3269,22.79305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((103.3269,22.79305,104.1025,22.79222), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.8216,22.80027,103.3269,22.79305), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.7241,22.81499,104.5913,22.82166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.5913,22.82166,104.7241,22.81499), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.2555,22.83194,104.5913,22.82166), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.3369,22.85332,106.6425,22.8561), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6425,22.8561,106.3369,22.85332), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2516,22.87194,106.6794,22.87555), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6794,22.87555,106.2516,22.87194), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.6061,22.90221,105.8725,22.91027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.8725,22.91027,106.6061,22.90221), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.8611,22.93666,106.2272,22.95304), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.2272,22.95304,104.8611,22.93666), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((106.1464,22.98333,106.2272,22.95304), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.7619,23.02388,106.1464,22.98333), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.5703,23.06444,104.823,23.09972), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((104.823,23.09972,105.5703,23.06444), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.5544,23.16027,105.5175,23.18721), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.5175,23.18721,105.5544,23.16027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.0644,23.23027,105.2405,23.26055), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.2405,23.26055,105.0644,23.23027), mapfile, tile_dir, 0, 11, "vn-vietnam")
	render_tiles((105.3233,23.37582,105.2405,23.26055), mapfile, tile_dir, 0, 11, "vn-vietnam")