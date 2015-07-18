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
    # Region: BA
    # Region Name: Bosnia and Herzegovina

	render_tiles((18.45529,42.56452,18.4119,42.6064), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4119,42.6064,18.2686,42.6183), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.2686,42.6183,18.5539,42.6211), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5539,42.6211,18.2686,42.6183), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.3625,42.6267,18.5539,42.6211), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5775,42.655,18.3625,42.6267), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5603,42.7122,18.5158,42.7311), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5158,42.7311,18.5603,42.7122), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.1003,42.7508,17.9989,42.7614), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.9989,42.7614,18.1003,42.7508), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.9225,42.8111,17.8828,42.8192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.8828,42.8192,17.9225,42.8111), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4547,42.8286,17.8828,42.8192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.8453,42.8597,18.4908,42.8617), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4908,42.8617,17.8453,42.8597), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.65554,42.89129,17.8425,42.9036), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.8425,42.9036,17.7864,42.9039), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.7864,42.9039,17.8425,42.9036), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.8214,42.9203,17.6981,42.9272), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.6981,42.9272,17.8214,42.9203), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.58333,42.94249,17.57956,42.94401), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.57956,42.94401,17.58333,42.94249), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.57956,42.94401,17.58333,42.94249), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.6767,42.9633,18.4839,42.9719), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4839,42.9719,17.6767,42.9633), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.6842,42.9822,18.4839,42.9719), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5356,43.0172,18.65,43.0442), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.65,43.0442,18.5356,43.0172), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.6303,43.0769,18.6592,43.0803), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6592,43.0803,17.6303,43.0769), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6439,43.1392,17.4533,43.1611), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4533,43.1611,18.6439,43.1392), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7075,43.2144,17.3944,43.2303), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.3944,43.2303,19.0481,43.2325), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0481,43.2325,17.3944,43.2303), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0167,43.2392,19.0481,43.2325), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0722,43.2481,18.6978,43.2525), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6978,43.2525,19.0722,43.2481), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2981,43.2808,18.9814,43.2842), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9814,43.2842,17.2981,43.2808), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9542,43.2933,18.9814,43.2842), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0911,43.3164,18.9603,43.3228), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9603,43.3228,19.0911,43.3164), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.8558,43.3328,18.9603,43.3228), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.8475,43.3489,18.9194,43.3569), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9194,43.3569,18.8475,43.3489), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0206,43.3919,17.2561,43.4144), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2561,43.4144,19.0231,43.4356), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0231,43.4356,17.2719,43.4444), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2719,43.4444,19.0231,43.4356), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9567,43.4544,17.2617,43.4597), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2617,43.4597,18.9567,43.4544), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.07,43.5086,18.9494,43.5094), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9494,43.5094,19.07,43.5086), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2292,43.5133,17.0983,43.5136), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.0983,43.5136,19.2292,43.5133), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.9869,43.5222,19.1767,43.5294), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1767,43.5294,19.0539,43.5356), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0539,43.5356,19.1767,43.5294), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2875,43.5442,19.0539,43.5356), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0117,43.5561,19.2875,43.5442), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5111,43.5733,19.4086,43.5844), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4086,43.5844,16.9764,43.5861), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9764,43.5861,19.4086,43.5844), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4917,43.5908,19.3158,43.5933), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3158,43.5933,19.4917,43.5908), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3717,43.6164,19.3158,43.5933), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5108,43.6797,16.8408,43.7192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.8408,43.7192,19.5108,43.6797), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4875,43.7633,16.7228,43.7861), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.7228,43.7861,19.4875,43.7633), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.7014,43.8506,16.7228,43.7861), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5081,43.9581,19.2506,43.9622), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2506,43.9622,19.3736,43.9656), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3736,43.9656,19.2506,43.9622), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.5372,44.0136,19.6192,44.0186), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2386,44.0136,19.6192,44.0186), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.6192,44.0186,16.5372,44.0136), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.4389,44.0319,19.6192,44.0186), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5678,44.0514,19.6197,44.0519), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.6197,44.0519,19.5678,44.0514), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5958,44.0678,19.5583,44.0703), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5583,44.0703,19.5958,44.0678), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.3547,44.0817,19.5039,44.0894), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.5039,44.0894,16.3547,44.0817), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4697,44.1281,19.4425,44.1422), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4425,44.1422,19.4767,44.1519), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.4767,44.1519,16.3022,44.1572), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.3022,44.1572,19.4767,44.1519), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3583,44.1844,16.2494,44.1967), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.2494,44.1967,16.1431,44.1994), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1431,44.1994,16.2494,44.1967), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1783,44.2147,16.1431,44.1994), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2394,44.2647,19.3189,44.2725), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3189,44.2725,19.2394,44.2647), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1653,44.2844,19.2019,44.2925), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2019,44.2925,19.1653,44.2844), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1839,44.3058,19.2019,44.2925), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.225,44.3358,19.1042,44.3558), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1042,44.3558,16.225,44.3358), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1592,44.3939,19.11,44.3992), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.11,44.3992,16.1592,44.3939), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1389,44.4139,19.11,44.3992), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1467,44.4383,19.1389,44.4139), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1214,44.5047,19.1253,44.5183), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1253,44.5183,16.1214,44.5047), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1908,44.5483,16.0425,44.5539), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.0425,44.5539,19.1908,44.5483), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1861,44.5764,19.2172,44.59), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2172,44.59,19.1861,44.5764), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2703,44.68,15.9569,44.7), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.9569,44.7,19.3136,44.7044), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3136,44.7044,15.9569,44.7), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.8547,44.7144,19.3136,44.7044), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3458,44.7692,15.7675,44.7761), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7675,44.7761,19.3353,44.7797), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3353,44.7797,15.7675,44.7761), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7364,44.8169,19.3608,44.8314), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3608,44.8314,15.7364,44.8169), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3553,44.8528,15.7983,44.8553), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7983,44.8553,19.3553,44.8528), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.0381,44.8606,19.3822,44.8628), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.3822,44.8628,18.8408,44.8631), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.8408,44.8631,19.3822,44.8628), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.35459,44.8935,19.2086,44.9014), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2086,44.9014,19.35459,44.8935), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7678,44.9158,15.7697,44.9192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.2503,44.9158,15.7697,44.9192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7697,44.9192,18.7678,44.9158), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((19.1767,44.9233,15.7697,44.9192), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7633,44.9447,18.8008,44.9494), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.8008,44.9494,18.7633,44.9447), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7875,44.9953,18.7939,44.9969), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7939,44.9969,15.7875,44.9953), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7311,44.9994,16.2925,45.0006), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.2925,45.0006,18.7311,44.9994), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.3536,45.0058,16.2925,45.0006), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.7314,45.0219,16.2097,45.0344), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.2097,45.0344,18.7314,45.0219), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5272,45.0472,17.8517,45.0492), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.8517,45.0492,18.5272,45.0472), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6553,45.0578,18.6692,45.0617), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6692,45.0617,18.6122,45.0625), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6122,45.0625,18.6692,45.0617), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4739,45.065,18.6122,45.0625), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5747,45.0689,18.4739,45.065), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7647,45.0736,18.5494,45.0761), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5494,45.0761,18.6869,45.0772), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6869,45.0772,18.5494,45.0761), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.9328,45.08,18.6869,45.0772), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.1194,45.0831,18.6097,45.0842), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6097,45.0842,18.2053,45.0847), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.2053,45.0847,18.6097,45.0842), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.16,45.0856,18.2053,45.0847), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.66,45.0878,16.16,45.0856), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.4,45.0903,18.66,45.0878), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6756,45.0942,18.5731,45.0944), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.5731,45.0944,18.6756,45.0942), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.6386,45.0947,16.1206,45.095), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1206,45.095,18.6386,45.0947), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.535,45.0956,16.1206,45.095), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.3231,45.1031,18.0694,45.1044), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.0694,45.1044,18.3231,45.1031), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.4297,45.1064,18.0694,45.1044), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.9339,45.1089,17.5558,45.1108), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.5961,45.1089,17.5558,45.1108), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.5558,45.1108,17.5281,45.1111), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4833,45.1108,17.5281,45.1111), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.5281,45.1111,17.5558,45.1108), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.9639,45.1128,17.5281,45.1111), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.395,45.1183,15.7844,45.1203), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7844,45.1203,16.395,45.1183), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.1119,45.1281,18.0322,45.1289), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4508,45.1281,18.0322,45.1289), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.0322,45.1289,16.1119,45.1281), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.2164,45.1297,18.0322,45.1289), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.5497,45.1317,18.2164,45.1297), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4944,45.1364,17.6703,45.1367), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.6703,45.1367,17.4944,45.1364), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.2517,45.1386,18.07,45.1392), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.07,45.1392,18.2517,45.1386), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.3722,45.1406,17.4228,45.1419), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4228,45.1419,17.3722,45.1406), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.4594,45.1444,17.4228,45.1419), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.1769,45.1483,16.4594,45.1444), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2403,45.1483,16.4594,45.1444), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((18.0017,45.1522,17.1769,45.1483), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.4494,45.1592,16.0519,45.1594), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.0519,45.1594,17.4494,45.1592), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.7811,45.1619,16.0519,45.1594), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.4761,45.1856,17.2672,45.1869), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.2672,45.1869,16.8239,45.1872), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.8239,45.1872,17.2672,45.1869), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.0706,45.1881,16.8239,45.1872), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.8342,45.2119,16.0231,45.215), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.0231,45.215,15.9517,45.2158), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.9517,45.2158,17.0083,45.2161), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.0083,45.2161,15.9517,45.2158), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.5258,45.2244,17.0356,45.2267), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.0356,45.2267,16.9903,45.2269), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9903,45.2269,17.0356,45.2267), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((15.9367,45.2286,16.9903,45.2269), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.595,45.2306,15.9367,45.2286), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9686,45.2336,16.9322,45.2344), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9322,45.2344,16.9686,45.2336), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((17.0106,45.2392,16.9322,45.2344), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9722,45.2494,17.0106,45.2392), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9397,45.2656,16.9136,45.2739), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")
	render_tiles((16.9136,45.2739,16.9397,45.2656), mapfile, tile_dir, 0, 11, "ba-bosnia-and-herzegovina")