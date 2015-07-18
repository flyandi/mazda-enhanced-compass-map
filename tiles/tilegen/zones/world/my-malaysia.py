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
    # Region: MY
    # Region Name: Malaysia

	render_tiles((117.0875,7.13028,117.05,7.17416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.0875,7.13028,117.05,7.17416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.05,7.17416,117.2191,7.17556), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2191,7.17556,117.05,7.17416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2794,7.24194,117.0889,7.30055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.0889,7.30055,117.2889,7.33028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2889,7.33028,117.2233,7.35667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2233,7.35667,117.2889,7.33028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.74275,6.24917,99.87415,6.28917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.74275,6.24917,99.87415,6.28917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.87415,6.28917,99.82442,6.31083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.82442,6.31083,99.87415,6.28917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.92165,6.34194,99.82442,6.31083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.64693,6.385,99.66969,6.41166), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.66969,6.41166,99.64693,6.385), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((99.85329,6.46396,99.66969,6.41166), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.5125,1.26889,104.1872,1.33833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.1872,1.33833,103.5861,1.34472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.5861,1.34472,104.1872,1.33833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.2758,1.36556,104.103,1.36917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.103,1.36917,104.2758,1.36556), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.5428,1.37972,104.103,1.36917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.9653,1.41806,104.0036,1.44083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.0036,1.44083,103.9653,1.41806), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.0478,1.46889,103.8003,1.47194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.8003,1.47194,104.0478,1.46889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.0447,1.50056,104.2819,1.51222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.2819,1.51222,104.1247,1.51472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.1247,1.51472,104.2819,1.51222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.3708,1.53667,104.0108,1.53805), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.0108,1.53805,103.3708,1.53667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.125,1.54555,104.0108,1.53805), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.2355,1.59639,103.9736,1.64028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.9736,1.64028,103.9511,1.64167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.9511,1.64167,103.9736,1.64028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.1391,1.83611,104.1675,1.83861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.1675,1.83861,104.1391,1.83611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.7261,1.84583,104.1675,1.83861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.8117,1.85306,102.7261,1.84583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.6869,1.86889,102.8117,1.85306), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.5592,2.03899,104.0197,2.13667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((104.0197,2.13667,102.3314,2.15222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.3314,2.15222,104.0197,2.13667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.9714,2.2,102.3314,2.15222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.9533,2.32444,101.858,2.39667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.858,2.39667,101.9314,2.41528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.9314,2.41528,101.858,2.39667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.8416,2.44667,101.9314,2.41528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.7747,2.58306,103.7672,2.64639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.7672,2.64639,103.6411,2.66139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.6411,2.66139,103.7672,2.64639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.5047,2.68083,103.6411,2.66139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.4014,2.81083,103.4825,2.83667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4825,2.83667,101.285,2.84111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.285,2.84111,103.4825,2.83667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.2819,2.91222,103.4372,2.92833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4372,2.92833,101.2819,2.91222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.3716,2.99444,101.37,3.04556), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.37,3.04556,101.3716,2.99444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.2944,3.26889,101.2561,3.30639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.2561,3.30639,101.2944,3.26889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4264,3.3925,101.2561,3.30639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4502,3.50401,103.4644,3.52972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4644,3.52972,103.4502,3.50401), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.0247,3.63139,103.3528,3.69222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.3528,3.69222,101.0247,3.63139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.8069,3.7875,103.348,3.79222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.348,3.79222,100.8069,3.7875), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.3816,3.805,103.348,3.79222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.76,3.84167,100.8408,3.84944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.8408,3.84944,100.76,3.84167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7191,3.87028,100.8408,3.84944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.3792,3.90278,100.6969,3.91694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.6969,3.91694,103.3792,3.90278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4391,3.95056,103.4022,3.95722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4022,3.95722,103.4391,3.95056), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7841,3.985,100.7147,3.99722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7147,3.99722,100.7841,3.985), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7772,4.01833,100.8647,4.03139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.8647,4.03139,100.7772,4.01833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.3941,4.085,100.7578,4.10222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7578,4.10222,103.3941,4.085), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.6205,4.1625,100.7578,4.10222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4928,4.29916,100.5619,4.3225), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5619,4.3225,103.4928,4.29916), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.6428,4.55555,100.5966,4.56694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5966,4.56694,100.6428,4.55555), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5922,4.61111,100.5966,4.56694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5989,4.65861,100.6608,4.67361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.6608,4.67361,100.5989,4.65861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5908,4.75055,100.6292,4.76417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.6292,4.76417,100.5908,4.75055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5739,4.79361,100.6292,4.76417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.4205,4.84111,100.5539,4.85694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5539,4.85694,103.4205,4.84111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.5133,4.9075,100.4355,4.92111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.4355,4.92111,100.5133,4.9075), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.3611,5.08611,100.4164,5.14833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.4164,5.14833,100.3611,5.08611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.1844,5.27861,100.4164,5.14833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((103.058,5.45555,101.1397,5.63194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.1397,5.63194,100.3411,5.66111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.3411,5.66111,100.385,5.67333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.385,5.67333,100.3411,5.66111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.2489,5.69916,101.0841,5.71417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.0841,5.71417,101.2489,5.69916), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.0244,5.73278,101.8241,5.73944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.8241,5.73944,102.658,5.74083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.658,5.74083,101.8241,5.73944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.685,5.77083,102.658,5.74083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.2819,5.80444,100.9936,5.80611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.9936,5.80611,101.2819,5.80444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.6589,5.86056,101.9438,5.86194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.9438,5.86194,101.6589,5.86056), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.57,5.91667,101.9461,5.96472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.9461,5.96472,101.1202,5.99055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.1202,5.99055,100.348,5.99722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.348,5.99722,101.1202,5.99055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.0916,6.11055,102.3733,6.13194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.3733,6.13194,102.0916,6.11055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.0791,6.16083,101.1216,6.18722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.1216,6.18722,102.313,6.18944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.313,6.18944,101.1216,6.18722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.2508,6.20833,102.313,6.18944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((102.0944,6.23618,100.9458,6.23861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.9458,6.23861,102.0944,6.23618), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.8522,6.24278,100.9458,6.23861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((101.1155,6.24889,100.8522,6.24278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.9897,6.2775,100.1767,6.27778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.1767,6.27778,100.9897,6.2775), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.8541,6.32555,100.1767,6.27778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.1286,6.41779,100.655,6.44833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.1286,6.41779,100.655,6.44833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.655,6.44833,100.1714,6.47667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.1714,6.47667,100.7486,6.50361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.7486,6.50361,100.1714,6.47667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.3711,6.5475,100.7486,6.50361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.19,6.6875,100.2989,6.70167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.2989,6.70167,100.2086,6.71056), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((100.2086,6.71056,100.2989,6.70167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.5875,0.85333,110.5197,0.86639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.5197,0.86639,110.5875,0.85333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.43,0.94111,111.5339,0.96083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.5339,0.96083,110.8444,0.96444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.8444,0.96444,111.5339,0.96083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.3491,0.99028,111.7977,0.99389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.7977,0.99389,110.3491,0.99028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.3947,1.01167,110.9041,1.01444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.9041,1.01444,111.8566,1.01694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.8566,1.01694,110.9041,1.01444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.5025,1.02528,111.8566,1.01694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.6503,1.035,111.5025,1.02528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2364,1.08639,110.2333,1.11444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.2333,1.11444,111.9416,1.12472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.9416,1.12472,110.2333,1.11444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.1272,1.14444,111.9416,1.12472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.068,1.22333,113.6425,1.22944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.6425,1.22944,110.068,1.22333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.4333,1.28639,113.7822,1.29944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.7822,1.29944,113.4333,1.28639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.5514,1.31639,111.3194,1.33306), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.3194,1.33306,111.3797,1.34806), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.3797,1.34806,111.3194,1.33306), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1316,1.3775,111.253,1.38972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.253,1.38972,111.1694,1.39528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1694,1.39528,111.253,1.38972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.9372,1.40556,109.8503,1.41444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.8503,1.41444,111.2625,1.41583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2625,1.41583,109.8503,1.41444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.9805,1.42278,114.1941,1.42417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.1941,1.42417,112.1847,1.42528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.1847,1.42528,114.1941,1.42417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.5436,1.43222,113.0989,1.43833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.0989,1.43833,113.9255,1.44389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.9255,1.44389,110.6875,1.44472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.6875,1.44472,113.9255,1.44389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.5875,1.45,110.6875,1.44472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.75,1.46444,111.0541,1.465), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.0541,1.465,110.75,1.46444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.1344,1.46778,111.0541,1.465), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.0441,1.49667,112.3097,1.49944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.3097,1.49944,113.0441,1.49667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.3894,1.50833,112.3097,1.49944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.7411,1.52,114.3894,1.50833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.7225,1.54944,113.0427,1.55417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.0427,1.55417,110.7225,1.54944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.8369,1.5625,110.7855,1.56861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.7855,1.56861,112.4844,1.57), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.4844,1.57,110.7855,1.56861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111,1.57555,112.4844,1.57), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.4664,1.61722,109.6677,1.61778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.6677,1.61778,110.4664,1.61722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.6661,1.63028,109.6677,1.61778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1761,1.65083,111.0308,1.66778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.0308,1.66778,110.2894,1.67055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.2894,1.67055,111.0308,1.66778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.0822,1.685,109.9339,1.68778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.9339,1.68778,110.4072,1.68972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.4072,1.68972,109.9339,1.68778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.175,1.70139,110.4072,1.68972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.3628,1.71389,110.5155,1.72), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.5155,1.72,110.3628,1.71389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.5005,1.73917,111.1014,1.74805), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1014,1.74805,110.5005,1.73917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.7108,1.77056,109.67,1.78444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.67,1.78444,109.8428,1.78833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.8428,1.78833,109.67,1.78444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.3455,1.79389,110.3189,1.79611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((110.3189,1.79611,110.3455,1.79389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.5855,1.80083,110.3189,1.79611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.6939,1.82925,109.5547,1.85361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.5547,1.85361,109.7008,1.85889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.7008,1.85889,109.5547,1.85361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.853,1.89722,109.6522,1.93361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.6522,1.93361,114.853,1.89722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8102,2.03417,114.8683,2.04389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8683,2.04389,114.8102,2.03417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((109.6501,2.06426,114.8683,2.04389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2428,2.11861,111.3489,2.11917), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.3489,2.11917,111.2428,2.11861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1783,2.14111,114.7764,2.14667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.7764,2.14667,111.1783,2.14111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1694,2.15833,111.368,2.16389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.368,2.16389,111.1694,2.15833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2069,2.21639,111.2378,2.21722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2378,2.21722,111.2069,2.21639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8047,2.24889,111.1822,2.25611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.1822,2.25611,114.8047,2.24889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.9477,2.29111,111.1822,2.25611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.3639,2.33972,111.4947,2.35278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.4947,2.35278,114.9497,2.36055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.9497,2.36055,111.4947,2.35278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2166,2.41361,115.0872,2.41889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0872,2.41889,111.2166,2.41361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.2475,2.42861,115.0872,2.41889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.148,2.48083,111.4073,2.48154), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.4073,2.48154,115.148,2.48083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.5161,2.48694,115.2136,2.49139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2136,2.49139,111.5161,2.48694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.453,2.50333,115.2136,2.49139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2372,2.5225,111.453,2.50333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.4147,2.55417,115.2372,2.5225), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0983,2.5975,115.1472,2.60861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.1472,2.60861,115.0816,2.61639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0816,2.61639,115.1472,2.60861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.4447,2.69167,115.0816,2.61639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.1294,2.77194,111.6717,2.84833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.6717,2.84833,111.9533,2.88055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((111.9533,2.88055,115.135,2.8825), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.135,2.8825,111.9533,2.88055), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.1555,2.92555,115.135,2.8825), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.32,2.97167,115.3208,2.98639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3208,2.98639,112.32,2.97167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.4873,3.02784,115.2708,3.045), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2708,3.045,115.4873,3.02784), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((112.8647,3.10083,115.2708,3.045), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.0358,3.18305,112.8647,3.10083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.1711,3.37722,115.5708,3.41778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5708,3.41778,115.6266,3.42667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.6266,3.42667,115.5708,3.41778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5714,3.61278,113.4511,3.765), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.4511,3.765,115.6164,3.85417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.6164,3.85417,115.5644,3.90639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5644,3.90639,115.6164,3.85417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.728,3.98389,114.61,4.02778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.61,4.02778,114.67,4.02972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.67,4.02972,114.61,4.02778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.7964,4.13055,117.5881,4.17124), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.5881,4.17124,117.4433,4.19278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.4433,4.19278,117.5881,4.17124), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.9739,4.23,115.758,4.23639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.758,4.23639,117.9739,4.23), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.3752,4.25694,114.4447,4.26583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.4447,4.26583,117.6519,4.26694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6519,4.26694,114.4447,4.26583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.848,4.27222,114.8036,4.27305), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8036,4.27305,114.848,4.27222), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.0541,4.27722,115.8519,4.27944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.8519,4.27944,114.3236,4.28028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.3236,4.28028,115.8519,4.27944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.4514,4.29528,113.9583,4.30778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.9583,4.30778,115.3364,4.31139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3364,4.31139,113.9583,4.30778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.5258,4.3175,115.3364,4.31139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3602,4.32916,116.8305,4.33083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8305,4.33083,116.6944,4.33194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.6944,4.33194,116.8305,4.33083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.6419,4.33472,116.6944,4.33194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.8566,4.34305,114.3278,4.34472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.3278,4.34472,115.8566,4.34305), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.468,4.35083,114.3278,4.34472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8689,4.35861,117.2333,4.35916), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2333,4.35916,114.8689,4.35861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.5216,4.36611,115.8864,4.36805), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.8864,4.36805,116.5216,4.36611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.3497,4.37083,116.7427,4.37305), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.7427,4.37305,116.3497,4.37083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.1669,4.37583,118.5736,4.37694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.5736,4.37694,116.1669,4.37583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6011,4.38028,118.5736,4.37694), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.5703,4.38611,115.0986,4.38639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0986,4.38639,116.5703,4.38611), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6547,4.4225,118.6422,4.43028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.6422,4.43028,114.8766,4.43278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8766,4.43278,118.6422,4.43028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8408,4.4375,114.8766,4.43278), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.9947,4.45333,114.8408,4.4375), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.5072,4.49972,118.5928,4.52361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.5928,4.52361,115.2664,4.52944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2664,4.52944,114.2358,4.53194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.2358,4.53194,115.2664,4.52944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4675,4.53528,114.2358,4.53194), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.963,4.57305,118.4905,4.58444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4905,4.58444,114.091,4.5899), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.091,4.5899,118.4172,4.59333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4172,4.59333,114.091,4.5899), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((113.9919,4.59889,118.4172,4.59333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4378,4.61389,113.9919,4.59889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0266,4.65666,118.4378,4.61389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.7778,4.72083,115.2441,4.725), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2441,4.725,114.7778,4.72083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.8489,4.80083,118.1878,4.81), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.1878,4.81,115.0296,4.81832), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0296,4.81832,115.0284,4.82529), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0296,4.81832,115.0284,4.82529), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0284,4.82529,114.9794,4.83167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((114.9794,4.83167,115.0284,4.82529), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.133,4.85083,114.9794,4.83167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0189,4.89052,115.0135,4.89099), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.0135,4.89099,115.0189,4.89052), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3233,4.89444,115.0135,4.89099), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3666,4.90333,115.1459,4.90336), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.1459,4.90336,115.3666,4.90333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4914,4.93083,115.2233,4.9575), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.2233,4.9575,118.2055,4.96), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.2055,4.96,115.2233,4.9575), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4453,5.00111,118.3914,5.03583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.3914,5.03583,118.9716,5.04472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.9716,5.04472,118.3914,5.03583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5577,5.06861,118.9716,5.04472), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.1936,5.12389,115.5577,5.06861), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.2683,5.21417,115.603,5.2175), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.603,5.2175,119.2683,5.21417), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3583,5.31167,119.2758,5.345), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.2758,5.345,115.3583,5.31167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.9277,5.39,118.9697,5.39083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.9697,5.39083,118.9277,5.39), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.3816,5.40305,118.9697,5.39083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.023,5.41806,118.9347,5.42972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.9347,5.42972,119.2025,5.43583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.2025,5.43583,119.1341,5.43639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((119.1341,5.43639,119.2025,5.43583), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.9736,5.43722,119.1341,5.43639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.448,5.44666,118.9736,5.43722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.8319,5.50472,118.5647,5.52111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.5647,5.52111,115.5961,5.52167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5961,5.52167,118.5647,5.52111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.675,5.52722,115.5961,5.52167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.8175,5.53389,115.675,5.52722), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.583,5.54972,115.8175,5.53389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.8572,5.57639,115.583,5.54972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.603,5.61778,115.6166,5.61916), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.6166,5.61916,118.603,5.61778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.5953,5.62805,115.6166,5.61916), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.575,5.64028,118.6394,5.64166), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.6394,5.64166,118.575,5.64028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.5975,5.65889,118.6394,5.64166), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.9603,5.68139,118.1486,5.69639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.1486,5.69639,117.9603,5.68139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((115.9189,5.7475,118.4919,5.74805), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.4919,5.74805,115.9189,5.7475), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.9069,5.79028,118.1767,5.80389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.1767,5.80389,118.3736,5.8075), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.3736,5.8075,118.0803,5.81028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.0803,5.81028,118.3736,5.8075), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.9314,5.81361,118.0803,5.81028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.0511,5.84083,118.1247,5.86139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.1247,5.86139,117.4641,5.86666), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.4641,5.86666,118.1247,5.86139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.8828,5.95,117.6678,5.97167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6678,5.97167,117.8828,5.95), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.9205,5.99416,117.6705,6.01333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6705,6.01333,117.9205,5.99416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6341,6.03444,117.6705,6.01333), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((118.013,6.05889,116.1416,6.06972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.1416,6.06972,118.013,6.05889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.1211,6.09583,116.0922,6.11139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.0922,6.11139,117.6361,6.12361), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6361,6.12361,116.0922,6.11139), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.5997,6.18972,116.2916,6.23389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.2916,6.23389,116.2203,6.23666), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.2203,6.23666,116.2916,6.23389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.7178,6.25167,117.6717,6.25528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6717,6.25528,117.7178,6.25167), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.2758,6.29611,117.6717,6.25528), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.3258,6.35944,117.7397,6.38389), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.7397,6.38389,116.3258,6.35944), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.6505,6.51333,117.5622,6.53444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.5622,6.53444,117.4744,6.54111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.4744,6.54111,117.5622,6.53444), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8,6.57666,116.7794,6.59028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.7794,6.59028,116.8625,6.59639), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8625,6.59639,116.7794,6.59028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.5291,6.62833,117.2975,6.62889), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2975,6.62889,117.5291,6.62833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.6439,6.70111,116.8097,6.70305), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8097,6.70305,116.6439,6.70111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8555,6.76639,117.2241,6.82083), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.2241,6.82083,117.0544,6.84), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.0544,6.84,116.8461,6.84416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8461,6.84416,117.0544,6.84), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8097,6.85694,116.8461,6.84416), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8503,6.88972,116.8167,6.9025), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.8167,6.9025,116.8503,6.88972), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.253,6.92833,117.0311,6.93667), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.0311,6.93667,117.253,6.92833), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.835,6.96111,116.7158,6.96778), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.7158,6.96778,116.835,6.96111), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.1783,6.99028,117.0894,6.99305), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((117.0894,6.99305,117.1783,6.99028), mapfile, tile_dir, 0, 11, "my-malaysia")
	render_tiles((116.7728,7.02167,117.0894,6.99305), mapfile, tile_dir, 0, 11, "my-malaysia")