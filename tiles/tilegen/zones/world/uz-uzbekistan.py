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
    # Region: UZ
    # Region Name: Uzbekistan

	render_tiles((67.26637,37.18526,67.77715,37.1858), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.77715,37.1858,67.26637,37.18526), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.22942,37.19193,67.77715,37.1858), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.77414,37.20609,67.55746,37.21554), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.55746,37.21554,67.77414,37.20609), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.42441,37.23499,67.64941,37.24609), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.64941,37.24609,67.20026,37.24665), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.20026,37.24665,67.64941,37.24609), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8381,37.2619,67.52164,37.27248), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.52164,37.27248,67.8381,37.2619), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8439,37.3306,66.66525,37.33832), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.66525,37.33832,67.8439,37.3306), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.53876,37.36051,66.74442,37.36137), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.74442,37.36137,66.53876,37.36051), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.58691,37.36804,66.74442,37.36137), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.02164,37.3772,66.58691,37.36804), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5167,37.4047,67.02164,37.3772), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8175,37.4461,66.5717,37.4686), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5717,37.4686,67.8175,37.4461), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5169,37.5217,67.8564,37.5353), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8564,37.5353,66.5169,37.5217), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.9172,37.6172,66.5478,37.6639), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5478,37.6639,67.9172,37.6172), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0731,37.7653,66.5383,37.7728), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5383,37.7728,68.0731,37.7653), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5578,37.8239,66.5383,37.7728), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1506,37.9281,66.6664,37.9367), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.6664,37.9367,68.1506,37.9281), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.2739,37.9544,66.6664,37.9367), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.6703,37.9733,68.2739,37.9544), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.6439,38.0031,68.2964,38.0181), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.2964,38.0181,66.6439,38.0031), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5556,38.0383,66.4239,38.0436), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.4239,38.0436,66.5556,38.0383), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.3194,38.0847,68.3686,38.1122), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.3686,38.1122,66.3194,38.0847), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.2536,38.155,66.1628,38.1739), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.1628,38.1739,66.2536,38.155), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.3842,38.1956,66.1628,38.1739), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.7486,38.2264,66.0717,38.2361), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.0717,38.2361,65.99,38.2428), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.99,38.2428,66.0717,38.2361), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.5972,38.2539,65.99,38.2428), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.3314,38.2733,65.8403,38.2736), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.8403,38.2736,68.3314,38.2733), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.8953,38.2808,65.8403,38.2736), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.2156,38.3322,65.8953,38.2808), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1694,38.395,65.2922,38.4108), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.2922,38.4108,68.1272,38.4244), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1272,38.4244,65.2922,38.4108), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1111,38.4983,68.0719,38.5414), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0719,38.5414,68.1111,38.4983), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0806,38.6092,64.9872,38.6267), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.9872,38.6267,68.0806,38.6092), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0578,38.6969,68.1008,38.7392), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1008,38.7392,64.6653,38.7419), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.6653,38.7419,68.1008,38.7392), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0831,38.7944,68.155,38.8086), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.155,38.8086,68.0831,38.7944), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.4972,38.8542,68.1964,38.8544), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1964,38.8544,64.4972,38.8542), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1839,38.9008,68.1964,38.8544), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.2147,38.9536,64.1739,38.955), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.1739,38.955,64.2147,38.9536), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8633,38.9767,68.0369,38.9842), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0369,38.9842,64.3403,38.9911), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.3403,38.9911,67.7094,38.9967), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.7094,38.9967,68.1133,38.9975), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1133,38.9975,67.7094,38.9967), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.9689,39.0089,68.1133,38.9975), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.6872,39.0494,67.9689,39.0089), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.6947,39.1347,67.6539,39.14), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.6539,39.14,67.6947,39.1347), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.5178,39.1678,67.6175,39.1719), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.6175,39.1719,67.5178,39.1678), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((63.7128,39.2061,67.3819,39.2097), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.3819,39.2097,63.7128,39.2061), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.3675,39.2253,67.3819,39.2097), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.3667,39.2942,67.4106,39.2992), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.4106,39.2992,67.3667,39.2942), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((63.5264,39.3936,67.4425,39.4856), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.4425,39.4856,67.5136,39.4956), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.5136,39.4956,67.4425,39.4856), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.3533,39.5367,68.4733,39.5375), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.4733,39.5375,68.3533,39.5367), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.5097,39.5547,67.6047,39.5664), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5403,39.5547,67.6047,39.5664), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.6047,39.5664,68.0825,39.5672), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0825,39.5672,67.6047,39.5664), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5367,39.5872,68.0825,39.5672), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5925,39.6136,67.8275,39.6214), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8275,39.6214,67.7097,39.6258), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.7097,39.6258,67.8275,39.6214), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((63.0386,39.6444,68.63,39.6561), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.63,39.6561,63.0386,39.6444), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.8194,39.7844,68.7608,39.8275), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7608,39.8275,68.7794,39.8389), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7794,39.8389,68.6739,39.85), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6739,39.85,68.6397,39.8558), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6397,39.8558,68.6739,39.85), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7744,39.8619,68.6397,39.8558), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7222,39.8714,68.8533,39.8742), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8533,39.8742,68.7222,39.8714), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8125,39.8783,68.8533,39.8742), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.9092,39.8914,68.8156,39.9003), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8156,39.9003,68.9092,39.8914), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.5844,39.9097,68.8156,39.9003), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8058,39.9717,68.7753,39.9869), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7753,39.9869,68.8153,39.9939), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8153,39.9939,68.7753,39.9869), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.4411,40.0311,68.8386,40.0408), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8386,40.0408,68.8083,40.0433), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8083,40.0433,68.8386,40.0408), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.9975,40.07,68.7853,40.0706), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7853,40.0706,68.9975,40.07), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.9006,40.0775,68.8086,40.0831), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8086,40.0831,68.9006,40.0775), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0094,40.1011,62.4181,40.1047), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.4181,40.1047,69.0094,40.1011), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7058,40.1169,62.4181,40.1047), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8636,40.1417,71.7106,40.1458), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7106,40.1458,68.9872,40.1492), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.9872,40.1492,71.7106,40.1458), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6014,40.1628,71.6875,40.1689), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6875,40.1689,68.6014,40.1628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6008,40.1783,68.64444,40.18328), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.64444,40.18328,71.7972,40.1839), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7972,40.1839,68.64444,40.18328), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.4408,40.1839,68.64444,40.18328), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2353,40.1897,68.8358,40.1947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8358,40.1947,69.2353,40.1897), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7161,40.2039,71.5628,40.2061), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.5628,40.2061,71.6217,40.2064), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6217,40.2064,71.5628,40.2061), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7683,40.2081,71.6217,40.2064), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6258,40.2103,69.3214,40.2119), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3214,40.2119,70.6258,40.2103), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8761,40.2186,71.6881,40.2208), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6881,40.2208,68.8761,40.2186), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0978,40.2244,71.6881,40.2208), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.4067,40.2292,71.4903,40.2328), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.4903,40.2328,62.4067,40.2292), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0322,40.2386,71.9572,40.2389), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.9572,40.2389,69.0322,40.2386), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8706,40.2428,70.98216,40.24466), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.98216,40.24466,70.8706,40.2428), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.0419,40.2469,70.98216,40.24466), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.9831,40.2528,70.5894,40.2553), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5894,40.2553,71.9831,40.2528), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6347,40.2553,71.9831,40.2528), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6656,40.2611,72.0494,40.2633), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.0494,40.2633,71.8633,40.2642), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.8633,40.2642,72.0494,40.2633), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2172,40.2692,70.9992,40.2703), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.9992,40.2703,71.2172,40.2692), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.4211,40.2728,70.9992,40.2703), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.4644,40.2753,71.0642,40.2775), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.0642,40.2775,71.4644,40.2753), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2317,40.2817,71.0642,40.2775), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3075,40.2864,69.2317,40.2817), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.9803,40.2925,69.3075,40.2864), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.39,40.3019,71.3089,40.3033), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.3089,40.3033,71.39,40.3019), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.24,40.3117,71.9744,40.3183), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.9744,40.3183,62.3939,40.3242), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.3939,40.3242,69.3392,40.3278), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3392,40.3278,62.3939,40.3242), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2736,40.3319,69.3392,40.3278), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5581,40.3425,70.4567,40.3511), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4567,40.3511,70.5581,40.3425), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5158,40.3603,70.4567,40.3511), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.3533,40.3725,70.375,40.3767), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.0983,40.3725,70.375,40.3767), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.375,40.3767,62.3533,40.3725), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.4236,40.3889,70.375,40.3767), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3783,40.4022,72.4236,40.3889), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.3553,40.4203,72.2406,40.4264), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2406,40.4264,70.3464,40.4297), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3464,40.4297,72.1103,40.4308), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.1103,40.4308,72.2956,40.4311), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2956,40.4311,72.1103,40.4308), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2375,40.4397,72.2764,40.4458), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2764,40.4458,72.2375,40.4397), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.1736,40.4603,70.3508,40.4617), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3508,40.4617,72.2825,40.4625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2825,40.4625,70.3508,40.4617), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.453,40.4644,72.2825,40.4625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.275,40.4692,72.453,40.4644), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.2014,40.4861,69.2597,40.5028), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2597,40.5028,70.485,40.5067), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.485,40.5067,69.2597,40.5028), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.6486,40.5169,72.5972,40.5186), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.5972,40.5186,72.6486,40.5169), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3811,40.5211,72.5972,40.5186), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.6725,40.5328,72.3811,40.5211), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.515,40.5497,72.5225,40.5572), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.5225,40.5572,72.4736,40.5578), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.4736,40.5578,72.5225,40.5572), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2106,40.5589,72.4736,40.5578), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3958,40.56,69.2106,40.5589), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.7681,40.57,70.5797,40.5747), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5797,40.5747,72.7681,40.57), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.69,40.5833,62.1231,40.5842), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.1231,40.5842,72.69,40.5833), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2514,40.5897,68.4844,40.5911), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.4844,40.5911,69.2514,40.5897), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.4553,40.5978,72.3694,40.5986), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3694,40.5986,68.4553,40.5978), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6081,40.5997,72.3694,40.5986), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3322,40.6022,68.6081,40.5997), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6317,40.6075,69.3322,40.6022), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3844,40.615,68.6317,40.6075), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5375,40.6256,68.6483,40.6261), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6483,40.6261,68.5375,40.6256), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6239,40.6364,69.7325,40.6386), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.7325,40.6386,68.6239,40.6364), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.6803,40.6467,62.0972,40.6539), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.0972,40.6539,69.6803,40.6467), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6519,40.6717,72.8045,40.6747), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.8045,40.6747,68.6519,40.6717), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.2333,40.6914,72.9167,40.7), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.9167,40.7,68.2333,40.6914), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.8231,40.7156,68.6322,40.7222), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6322,40.7222,62.0425,40.7236), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.0425,40.7236,68.6322,40.7222), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.79546,40.72551,62.0425,40.7236), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5897,40.7303,70.79546,40.72551), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.335,40.7381,68.5897,40.7303), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7767,40.7556,69.3517,40.7689), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3517,40.7689,70.0503,40.77), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.0503,40.77,69.3517,40.7689), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.0961,40.7714,70.6922,40.7722), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6922,40.7722,73.0961,40.7714), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.5344,40.7822,69.4892,40.7856), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4892,40.7856,70.6494,40.7861), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6494,40.7861,69.4892,40.7856), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7011,40.7981,69.4003,40.8003), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4003,40.8003,70.7289,40.8006), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7289,40.8006,69.4003,40.8003), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.075,40.8011,70.7289,40.8006), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4592,40.8111,70.7281,40.8139), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7281,40.8139,69.4592,40.8111), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0156,40.8139,69.4592,40.8111), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.1703,40.8172,70.7281,40.8139), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6458,40.8225,72.8953,40.8228), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.8953,40.8228,70.6458,40.8225), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.1222,40.8267,70.6672,40.8275), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6672,40.8275,70.1222,40.8267), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.1689,40.8322,70.6672,40.8275), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.0547,40.8428,73.1456,40.8486), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.1456,40.8486,61.9958,40.8492), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.9958,40.8492,73.1456,40.8486), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.2481,40.8558,68.5681,40.8614), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5681,40.8614,68.0878,40.8625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0878,40.8625,68.5681,40.8614), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.8994,40.8678,72.7256,40.8692), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.7256,40.8692,72.8994,40.8678), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.0469,40.8731,68.5936,40.8739), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5936,40.8739,73.0469,40.8731), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.8819,40.8767,73.0278,40.8789), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((73.0278,40.8789,72.8819,40.8767), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.6317,40.8853,73.0278,40.8789), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3167,40.8942,72.6317,40.8853), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6119,40.9131,68.5936,40.92), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.5936,40.92,68.6119,40.9131), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.135,40.9356,68.6167,40.9369), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.6167,40.9369,68.135,40.9356), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3747,40.9583,72.5667,40.9647), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.5667,40.9647,70.3747,40.9583), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.5075,40.9756,68.7244,40.9789), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7244,40.9789,70.5442,40.9811), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5442,40.9811,68.7244,40.9789), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1444,40.9978,61.975,40.9997), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.975,40.9997,68.1444,40.9978), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.1945,41.0043,61.975,40.9997), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.5061,41.0153,68.1225,41.02), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1225,41.02,72.5061,41.0153), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3817,41.0278,68.1511,41.0308), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1511,41.0308,72.3192,41.0322), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3192,41.0322,72.405,41.0336), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.405,41.0336,72.3192,41.0322), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4719,41.0372,72.405,41.0336), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0825,41.0436,70.4719,41.0372), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0281,41.0436,70.4719,41.0372), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4305,41.0503,68.1439,41.0514), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1439,41.0514,70.4305,41.0503), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.0592,41.0536,68.1439,41.0514), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.2167,41.06,68.1056,41.0656), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.1056,41.0656,72.2167,41.06), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.7744,41.0772,72.3514,41.0828), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.3514,41.0828,68.7744,41.0772), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8683,41.1092,61.8933,41.1117), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.8933,41.1117,68.8683,41.1092), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.1956,41.1167,71.4181,41.1186), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.4181,41.1186,71.2853,41.1192), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2853,41.1192,71.4181,41.1186), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.9969,41.12,71.2853,41.1192), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.3111,41.1225,67.9969,41.12), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2133,41.1369,71.44,41.1372), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.44,41.1372,71.2133,41.1369), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.0714,41.1436,71.44,41.1372), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.8758,41.15,67.4558,41.1533), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.4558,41.1533,66.8758,41.15), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.0528,41.1589,68.8983,41.1614), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.8983,41.1614,67.8611,41.1628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.2733,41.1614,67.8611,41.1628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.8611,41.1628,68.8983,41.1614), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.1514,41.1642,71.1075,41.1644), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.1075,41.1644,71.1514,41.1642), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.35,41.1658,71.1075,41.1644), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.72,41.175,71.2714,41.1756), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2714,41.1756,68.9839,41.1758), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((68.9839,41.1758,71.2714,41.1756), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2025,41.1822,61.3553,41.1872), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.3553,41.1872,71.2025,41.1822), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.1836,41.1925,71.8933,41.1944), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.755,41.1925,71.8933,41.1944), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.8933,41.1944,71.0547,41.1947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((67.9344,41.1944,71.0547,41.1947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.0547,41.1947,71.8933,41.1944), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2433,41.1964,71.0547,41.1947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.1672,41.1989,70.9331,41.1994), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.9331,41.1994,61.1672,41.1989), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((72.1219,41.2019,70.9331,41.1994), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.9994,41.2019,70.9331,41.1994), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.0017,41.2083,72.1219,41.2019), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0631,41.2161,60.4883,41.2194), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.4883,41.2194,61.0469,41.2214), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.0469,41.2214,60.4883,41.2194), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.1419,41.2319,61.38532,41.23426), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.38532,41.23426,61.1419,41.2319), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.0858,41.2372,61.38532,41.23426), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7847,41.2433,61.0331,41.2458), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.0331,41.2458,70.7847,41.2433), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.9558,41.2486,61.0331,41.2458), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8472,41.2564,60.8119,41.2578), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.8119,41.2578,60.705,41.2581), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.705,41.2581,60.8119,41.2578), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.05,41.2628,60.705,41.2581), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.6114,41.2675,57.05,41.2628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.6669,41.2881,61.4033,41.2922), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.4033,41.2922,56.6669,41.2881), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.8275,41.2994,71.5503,41.3008), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.5503,41.3008,56.8275,41.2994), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.4247,41.3025,71.5503,41.3008), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.3386,41.3086,61.4247,41.3025), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6036,41.3192,57.0922,41.3219), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.0922,41.3219,71.6036,41.3192), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((55.99845,41.32547,57.0922,41.3219), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.8872,41.3333,55.99845,41.32547), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.4381,41.3489,69.0806,41.3508), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0806,41.3508,71.4381,41.3489), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7928,41.3575,69.0806,41.3508), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.1883,41.3711,69.0564,41.3794), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.0564,41.3794,57.1883,41.3711), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.0958,41.4014,69.1508,41.4017), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.1508,41.4017,57.0958,41.4014), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.1231,41.4083,60.0911,41.4103), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0911,41.4103,70.4742,41.4122), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4742,41.4122,60.0911,41.4103), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5403,41.4219,69.1567,41.4258), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.1567,41.4258,71.66,41.4289), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.66,41.4289,71.7483,41.4314), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7483,41.4314,71.695,41.4319), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.695,41.4319,71.7483,41.4314), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0769,41.4425,71.695,41.4319), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7253,41.4556,69.2925,41.4586), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2925,41.4586,71.7253,41.4556), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7639,41.4617,69.2925,41.4586), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.0428,41.4669,69.2478,41.4706), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.2478,41.4706,70.7119,41.4708), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7119,41.4708,69.2478,41.4706), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4217,41.4731,69.4194,41.475), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4194,41.475,70.6694,41.4764), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6694,41.4764,69.4194,41.475), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.3464,41.4842,70.6694,41.4764), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3767,41.4969,71.6206,41.5075), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6206,41.5075,70.3767,41.4969), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.7164,41.5225,70.1917,41.525), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.1917,41.525,69.4036,41.5253), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4036,41.5253,70.1917,41.525), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0994,41.5375,69.4036,41.5253), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6914,41.5564,69.4431,41.5678), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.6461,41.5564,69.4431,41.5678), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.4431,41.5678,57.0358,41.575), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.0358,41.575,70.1811,41.5775), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.1811,41.5775,57.0358,41.575), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.5067,41.5828,70.1811,41.5775), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.1864,41.5944,69.5067,41.5828), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.9761,41.6647,69.6422,41.6733), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.6422,41.6733,56.9761,41.6647), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.8453,41.7064,69.9308,41.7117), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((69.9308,41.7117,70.47,41.7131), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.47,41.7131,69.9308,41.7117), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0803,41.7242,66.5586,41.7328), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5586,41.7328,60.0803,41.7242), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.9947,41.7497,60.0697,41.7578), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0697,41.7578,56.9947,41.7497), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.2569,41.7725,70.0581,41.7803), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.0581,41.7803,60.2569,41.7725), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.0092,41.7803,60.2569,41.7725), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5261,41.7967,60.275,41.7978), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.275,41.7978,70.5261,41.7967), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.1225,41.805,70.0869,41.8089), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.0869,41.8089,60.1225,41.805), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.2561,41.8228,70.1403,41.8281), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.1403,41.8281,60.2561,41.8228), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.9694,41.8433,70.1403,41.8281), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.1778,41.8622,56.9694,41.8433), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((56.9783,41.885,70.6853,41.905), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6853,41.905,60.1228,41.9217), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.1228,41.9217,70.8442,41.9253), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8442,41.9253,60.1228,41.9217), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.2256,41.9414,57.1197,41.9417), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.1197,41.9417,70.2256,41.9414), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9342,41.9622,70.3022,41.9772), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3022,41.9772,59.9256,41.9803), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9256,41.9803,70.3022,41.9772), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9372,41.9947,66.0292,42.0031), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.0292,42.0031,59.9372,41.9947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.5264,42.0031,59.9372,41.9947), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0247,42.015,70.8533,42.0164), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8533,42.0164,60.0247,42.015), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6244,42.0197,70.8533,42.0164), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5392,42.0381,70.3331,42.0394), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3331,42.0394,70.8711,42.04), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8711,42.04,70.3331,42.0394), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.9869,42.0444,70.8711,42.04), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.3733,42.0642,70.5311,42.0689), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.5311,42.0689,70.3733,42.0642), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.2458,42.0953,70.4883,42.1), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.4883,42.1,70.6689,42.1017), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.6689,42.1017,70.4883,42.1), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.1467,42.1319,71.2169,42.1394), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9922,42.1319,71.2169,42.1394), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2169,42.1394,71.1467,42.1319), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.6644,42.1536,57.3869,42.1625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.3869,42.1625,71.2597,42.1703), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2597,42.1703,57.4739,42.1753), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.4739,42.1753,71.2597,42.1703), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.7522,42.1842,60.0644,42.1883), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.0644,42.1883,57.8692,42.1906), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.8692,42.1906,60.0644,42.1883), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.8569,42.1964,70.7528,42.2011), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.7528,42.2011,71.2761,42.2042), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.2761,42.2042,57.875,42.2064), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.875,42.2064,71.2761,42.2042), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.8478,42.2378,59.9567,42.2433), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9567,42.2433,57.8478,42.2378), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((70.9694,42.2528,57.9047,42.2606), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.9047,42.2606,70.9694,42.2528), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.4572,42.2928,71.0278,42.2964), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.0278,42.2964,58.4119,42.2967), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.4119,42.2967,71.0278,42.2964), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.9161,42.2978,58.4119,42.2967), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.5106,42.3011,71.0672,42.3028), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((71.0672,42.3028,58.5106,42.3011), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.8439,42.315,58.5142,42.3181), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.5142,42.3181,59.8439,42.315), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.7358,42.3225,58.5142,42.3181), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.3714,42.3381,59.2847,42.3478), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.2847,42.3478,59.3714,42.3381), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.9636,42.3672,59.2639,42.3722), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.2639,42.3722,57.9636,42.3672), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.4469,42.3778,59.2639,42.3722), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.4114,42.3894,58.4469,42.3778), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.0367,42.4233,66.0708,42.4242), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.0708,42.4242,66.0367,42.4233), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.3381,42.4414,57.9239,42.4419), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.9239,42.4419,58.3381,42.4414), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((57.97,42.45,59.2586,42.4517), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.2586,42.4517,57.97,42.45), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.1467,42.4622,59.2586,42.4517), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.2256,42.4761,58.1467,42.4622), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.1017,42.5039,58.0336,42.5053), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.0336,42.5053,58.1017,42.5039), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.0472,42.5239,58.9517,42.5408), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.9517,42.5408,59.1558,42.5411), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((59.1558,42.5411,58.9517,42.5408), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.315,42.5483,59.1558,42.5411), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.1642,42.6011,58.1431,42.6308), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.1431,42.6308,58.8019,42.6394), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.8019,42.6394,58.1431,42.6308), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.1644,42.6519,58.4225,42.6608), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.4225,42.6608,58.5628,42.6625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.5628,42.6625,58.4225,42.6608), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.2939,42.6975,58.5628,42.6625), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.5978,42.7842,58.6175,42.7975), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.6175,42.7975,58.5978,42.7842), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.8219,42.8772,58.6175,42.7975), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((66.1239,42.9969,65.8219,42.8772), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.5269,43.3172,65.2683,43.4325), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((65.2683,43.4325,62.0239,43.4861), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((62.0239,43.4861,65.2683,43.4325), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.4581,43.5481,63.9972,43.5706), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((63.9972,43.5706,64.4581,43.5481), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((63.2114,43.6364,63.9972,43.5706), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((64.9314,43.7378,63.2114,43.6364), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.20503,44.16928,61.13454,44.20675), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.13454,44.20675,61.1147,44.2173), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.1147,44.2173,61.13671,44.22628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.13671,44.22628,61.1147,44.2173), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.06343,44.24455,61.13671,44.22628), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.00864,44.27367,60.97306,44.29258), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.97306,44.29258,60.97332,44.29721), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.97332,44.29721,60.97306,44.29258), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.95165,44.32221,60.97971,44.3336), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((60.97971,44.3336,61.02721,44.34332), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.02721,44.34332,60.97971,44.3336), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((61.04971,44.38165,61.02721,44.34332), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((55.9972,45.0031,58.67572,45.51377), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.67572,45.51377,58.56907,45.56428), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.56907,45.56428,58.56062,45.56829), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.56062,45.56829,58.57138,45.57066), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.56062,45.56829,58.57138,45.57066), mapfile, tile_dir, 0, 11, "uz-uzbekistan")
	render_tiles((58.57138,45.57066,58.56062,45.56829), mapfile, tile_dir, 0, 11, "uz-uzbekistan")