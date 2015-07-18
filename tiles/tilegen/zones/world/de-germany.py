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
    # Region: DE
    # Region Name: Germany

	render_tiles((13.93944,53.84304,13.825,53.85832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.825,53.85832,14.21742,53.86866), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.21742,53.86866,14.18722,53.87498), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.18722,53.87498,14.21742,53.86866), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.20333,53.90942,13.94055,53.91277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.94055,53.91277,14.20333,53.90942), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.22598,53.92825,14.04889,53.94193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.22598,53.92825,14.04889,53.94193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.04889,53.94193,14.22598,53.92825), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.90306,53.98832,14.0525,53.99749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.0525,53.99749,13.90306,53.98832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.89055,54.00749,14.0525,53.99749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.77555,54.02165,13.89055,54.00749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.97664,54.04259,13.92,54.06277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.92,54.06277,13.97664,54.04259), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.91139,54.08443,13.81722,54.10082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.81722,54.10082,13.91139,54.08443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.77555,54.1336,13.81722,54.10082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.34083,54.23499,13.42944,54.23804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.34083,54.23499,13.42944,54.23804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.42944,54.23804,13.34083,54.23499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.42,54.26138,13.20361,54.27249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.365,54.26138,13.20361,54.27249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.20361,54.27249,13.33555,54.27943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.33555,54.27943,13.20361,54.27249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.69639,54.29276,13.33555,54.27943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.44972,54.31638,13.69055,54.32721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.69055,54.32721,13.11833,54.33388), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.11833,54.33388,13.69055,54.32721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.76778,54.34082,13.11833,54.33388), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.68444,54.34915,13.76778,54.34082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.13083,54.37082,13.26528,54.38026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.26528,54.38026,13.13083,54.37082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.6175,54.40415,13.15472,54.42443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.15472,54.42443,13.6175,54.40415), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.57778,54.45388,13.27222,54.47582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.27222,54.47582,13.49861,54.47943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.49861,54.47943,13.58528,54.48277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.58528,54.48277,13.49861,54.47943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.30694,54.51332,13.34972,54.51943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.34972,54.51943,13.30694,54.51332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.67528,54.52637,13.34972,54.51943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.14722,54.54082,13.24166,54.55276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.24166,54.55276,13.37528,54.55777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.37528,54.55777,13.51833,54.56248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.24611,54.55777,13.51833,54.56248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.51833,54.56248,13.28639,54.56332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.28639,54.56332,13.51833,54.56248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.67667,54.56527,13.28639,54.56332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.455,54.57277,13.40167,54.57304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.40167,54.57304,13.455,54.57277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.36611,54.57526,13.40167,54.57304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.63389,54.58582,13.40028,54.59415), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.40028,54.59415,13.63389,54.58582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.36528,54.61526,13.22666,54.6286), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.22666,54.6286,13.38305,54.63888), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.38305,54.63888,13.28889,54.64471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.28889,54.64471,13.23055,54.64777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.23055,54.64777,13.28889,54.64471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.41444,54.68193,13.23055,54.64777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.16972,47.2811,10.27444,47.28888), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.27444,47.28888,10.16972,47.2811), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.21416,47.31526,10.27444,47.28888), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.15444,47.36915,10.21139,47.38638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.21139,47.38638,10.08722,47.38721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.08722,47.38721,10.21139,47.38638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.10694,47.39638,11.2275,47.40054), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.2275,47.40054,11.10694,47.39638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.97361,47.40054,11.10694,47.39638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.10444,47.42887,11.23666,47.43304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.23666,47.43304,11.20305,47.43526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.20305,47.43526,10.47333,47.43554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.47333,47.43554,11.20305,47.43526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.40555,47.45387,10.08444,47.46027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.08444,47.46027,11.40555,47.45387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.95028,47.46027,11.40555,47.45387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.005,47.46943,10.08444,47.46027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.00333,47.48387,10.86583,47.49304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.86583,47.49304,13.05305,47.49638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.05305,47.49638,10.86583,47.49304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.42194,47.50888,11.57444,47.51998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.57444,47.51998,10.90972,47.52193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.90972,47.52193,11.57444,47.51998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.60555,47.52915,9.72729,47.53626), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.72729,47.53626,10.84861,47.53638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.84861,47.53638,9.72729,47.53626), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.55444,47.53693,10.84861,47.53638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.85416,47.53888,10.55444,47.53693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.69722,47.54332,9.56761,47.54392), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.56761,47.54392,7.69722,47.54332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.96333,47.54777,9.56761,47.54392), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.80972,47.55221,7.94333,47.5536), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.94333,47.5536,12.80972,47.55221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.68555,47.55859,7.61833,47.5611), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.46527,47.55859,7.61833,47.5611), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.61833,47.5611,10.68555,47.55859), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.34055,47.57416,10.42694,47.57693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.42694,47.57693,8.34055,47.57416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.49303,47.58456,9.76305,47.58471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.5888,47.58456,9.76305,47.58471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.76305,47.58471,8.49303,47.58456), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.81944,47.58832,12.78778,47.58942), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.78778,47.58942,7.81944,47.58832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.48277,47.59054,12.78778,47.58942), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.81361,47.5936,11.63333,47.59526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.63333,47.59526,9.81361,47.5936), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.67444,47.60638,11.87861,47.60665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.87861,47.60665,7.67444,47.60638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.19611,47.60943,11.87861,47.60665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.07417,47.61693,12.83083,47.61887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.83083,47.61887,13.07417,47.61693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.20555,47.62165,12.83083,47.61887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.50833,47.62832,8.20555,47.62165), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.505,47.63749,8.61889,47.63971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.61889,47.63971,13.10028,47.64082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.10028,47.64082,12.20639,47.64137), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.20639,47.64137,13.10028,47.64082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.87861,47.65582,8.62166,47.66026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.62166,47.66026,9.26167,47.66304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.26167,47.66304,8.62166,47.66026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.41333,47.6711,12.77389,47.67416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.77389,47.67416,8.41333,47.6711), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.7975,47.68304,7.51333,47.68693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.51333,47.68693,8.7975,47.68304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.24389,47.6947,8.725,47.69776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.725,47.69776,12.44167,47.69859), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.44167,47.69859,8.725,47.69776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.17722,47.70165,8.40667,47.70387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.40667,47.70387,12.17722,47.70165), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.05833,47.70609,8.40667,47.70387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.9175,47.71554,13.05833,47.70609), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.8,47.73526,12.25722,47.74304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.25722,47.74304,8.8,47.73526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.72694,47.76499,12.93972,47.78471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.93972,47.78471,8.72694,47.76499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.55972,47.80637,12.93972,47.78471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.00889,47.85416,8.55972,47.80637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.61583,48.00277,7.57167,48.03721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.57167,48.03721,7.61583,48.00277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.75667,48.12054,7.605,48.15693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.605,48.15693,12.75667,48.12054), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.92952,48.2093,7.605,48.15693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.75083,48.33665,13.36861,48.35193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.36861,48.35193,7.75083,48.33665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.43389,48.41998,13.36861,48.35193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.77167,48.49165,7.8075,48.51332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.8075,48.51332,13.72666,48.51776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.72666,48.51776,7.8075,48.51332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.44833,48.5686,13.50528,48.58305), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.50528,48.58305,7.80194,48.59248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.80194,48.59248,13.50528,48.58305), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.92151,48.69003,13.83333,48.69887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.83333,48.69887,7.92151,48.69003), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.79305,48.72609,13.83333,48.69887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.81463,48.78714,13.79305,48.72609), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.74083,48.88165,8.13333,48.88554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.13333,48.88554,13.74083,48.88165), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.66194,48.89638,8.13333,48.88554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.50167,48.94498,8.22739,48.96371), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.22739,48.96371,13.58444,48.96887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.58444,48.96887,8.22739,48.96371), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.19222,48.96887,8.22739,48.96371), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.51611,48.97776,13.58444,48.96887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.40144,49.00749,13.51611,48.97776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.74111,49.04166,7.93861,49.04887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.93861,49.04887,13.39528,49.05026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.39528,49.05026,7.93861,49.04887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.54,49.08887,7.03833,49.11832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.03833,49.11832,13.20361,49.11943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.20361,49.11943,7.03833,49.11832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.08833,49.12526,13.20361,49.11943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.36139,49.14777,6.83889,49.15498), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.83889,49.15498,7.36139,49.14777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.78722,49.16248,7.48694,49.16415), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.48694,49.16415,6.78722,49.16248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.37528,49.17193,13.15111,49.17748), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.15111,49.17748,7.37528,49.17193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.02667,49.18887,13.15111,49.17748), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.8475,49.21526,7.02667,49.18887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.02694,49.29832,6.58972,49.32027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.58972,49.32027,12.87861,49.32804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.87861,49.32804,6.58972,49.32027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.93528,49.34026,12.87861,49.32804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.59472,49.36304,12.93528,49.34026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.66166,49.43387,6.49389,49.4472), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.49389,49.4472,6.36222,49.45998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.36222,49.45998,6.49389,49.4472), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.3725,49.59026,12.525,49.63721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.525,49.63721,6.3725,49.59026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.44194,49.70026,6.51055,49.70638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.51055,49.70638,12.44194,49.70026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.40305,49.75999,6.52222,49.8111), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.52222,49.8111,12.49944,49.83276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.49944,49.83276,6.32667,49.83971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.32667,49.83971,12.49944,49.83276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.5425,49.92027,12.47444,49.94304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.47444,49.94304,12.5425,49.92027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.49277,49.97498,12.42889,49.98443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.42889,49.98443,12.49277,49.97498), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.13972,49.99665,12.42889,49.98443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.22889,50.09637,6.13183,50.12553), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.13183,50.12553,12.22889,50.09637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.32972,50.16971,12.205,50.17416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.205,50.17416,12.32972,50.16971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.28055,50.1861,12.205,50.17416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.17333,50.23248,12.09694,50.24971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.09694,50.24971,12.26,50.26166), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.26,50.26166,12.09694,50.24971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.13583,50.27832,12.36472,50.27915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.36472,50.27915,12.13583,50.27832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.09347,50.3242,6.40028,50.32915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.40028,50.32915,12.09347,50.3242), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.48639,50.35138,6.40028,50.32915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.99444,50.42304,12.90389,50.42332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.90389,50.42332,12.99444,50.42304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.785,50.44637,6.36639,50.45221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.36639,50.45221,12.785,50.44637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.03361,50.50277,13.19111,50.5036), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.19111,50.5036,13.03361,50.50277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.20083,50.51638,13.19111,50.5036), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.23944,50.57971,13.32083,50.5811), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.32083,50.5811,13.23944,50.57971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.47,50.60332,6.26861,50.6236), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.26861,50.6236,6.17139,50.62387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.17139,50.62387,6.26861,50.6236), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.38,50.64137,6.17139,50.62387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.56667,50.7111,6.02861,50.71582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.02861,50.71582,13.56667,50.7111), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.10833,50.72331,13.86194,50.72581), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.86194,50.72581,6.10833,50.72331), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.00841,50.75607,13.86194,50.72581), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.90778,50.79054,5.98167,50.80276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.98167,50.80276,14.04667,50.80693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.04667,50.80693,5.98167,50.80276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.80166,50.81888,14.04667,50.80693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.62527,50.85416,14.82885,50.86603), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.82885,50.86603,6.08472,50.8736), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.08472,50.8736,14.82885,50.86603), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.39361,50.89471,6.08472,50.8736), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.56972,50.91609,14.65083,50.92416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.65083,50.92416,14.56972,50.91609), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.40361,50.93249,14.65083,50.92416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.01083,50.9436,14.40361,50.93249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.02833,50.97665,14.59889,50.9772), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.59889,50.9772,6.02833,50.97665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.96583,50.97832,14.59889,50.9772), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.26083,50.99665,5.96583,50.97832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.86944,51.01888,14.47222,51.03137), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.47222,51.03137,5.95222,51.03665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.95222,51.03665,14.47222,51.03137), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.87389,51.05026,5.95222,51.03665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.97528,51.10638,5.87389,51.05026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.16722,51.16276,6.07944,51.17582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.07944,51.17582,6.16722,51.16276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.07389,51.22054,6.07944,51.17582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((15.03805,51.26804,6.07389,51.22054), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.22222,51.36166,15.03805,51.26804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.2175,51.47637,14.91083,51.48304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.91083,51.48304,6.2175,51.47637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.7175,51.55276,6.09305,51.60721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.09305,51.60721,6.11611,51.65192), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.11611,51.65192,14.7575,51.65942), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.7575,51.65942,6.11611,51.65192), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.02944,51.6786,14.7575,51.65942), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.03917,51.71693,5.955,51.73859), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.955,51.73859,6.03917,51.71693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.96889,51.7911,14.60194,51.8136), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.60194,51.8136,6.38028,51.82999), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.38028,51.82999,5.96194,51.83027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((5.96194,51.83027,6.38028,51.82999), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.16972,51.84193,5.96194,51.83027), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.61611,51.85387,6.16972,51.84193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.13778,51.87693,6.54889,51.88526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.54889,51.88526,6.13778,51.87693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.72778,51.89943,6.15972,51.90554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.15972,51.90554,6.72778,51.89943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.71639,51.94109,6.83083,51.97137), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.83083,51.97137,14.71639,51.94109), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.80055,52.00721,6.68805,52.03888), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.68805,52.03888,14.74722,52.05637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.74722,52.05637,6.6975,52.06998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.6975,52.06998,14.76353,52.07081), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.76353,52.07081,6.6975,52.06998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.69305,52.10471,6.86056,52.12026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.86056,52.12026,14.69305,52.10471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.87972,52.15359,6.86056,52.12026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.0525,52.23582,14.71333,52.23915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.71333,52.23915,7.0525,52.23582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.56694,52.32804,7.06556,52.38582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.06556,52.38582,14.53444,52.39471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.53444,52.39471,7.06556,52.38582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.94611,52.43443,6.9875,52.4611), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.9875,52.4611,6.70555,52.48582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.70555,52.48582,6.9875,52.4611), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.69028,52.55193,14.64111,52.56666), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.64111,52.56666,6.76083,52.56721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.76083,52.56721,14.64111,52.56666), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.55111,52.62859,6.72083,52.62943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.72083,52.62943,14.55111,52.62859), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.03583,52.63276,6.72083,52.62943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.05528,52.65192,6.78167,52.65415), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((6.78167,52.65415,7.05528,52.65192), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.2575,52.79054,7.06972,52.81499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.06972,52.81499,14.13333,52.83332), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.13333,52.83332,7.06972,52.81499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.19889,52.96776,14.16472,52.96887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.16472,52.96887,7.19889,52.96776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.34222,53.04499,14.16472,52.96887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.39083,53.14165,14.40861,53.21582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.40861,53.21582,7.20944,53.24276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.20944,53.24276,7.21111,53.24416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.21111,53.24416,7.20944,53.24276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.4453,53.27259,7.21111,53.24416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.25278,53.31693,7.05056,53.33971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.05056,53.33971,7.25278,53.31693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.01583,53.3836,8.22305,53.40083), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.22305,53.40083,8.48528,53.4061), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.48528,53.4061,8.50278,53.40887), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.50278,53.40887,8.48528,53.4061), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.28417,53.4186,14.37555,53.42304), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.37555,53.42304,8.28417,53.4186), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.08028,53.45805,8.31389,53.45943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.31389,53.45943,8.08028,53.45805), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.48833,53.47916,7.035,53.48749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.035,53.48749,8.48833,53.47916), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.06139,53.50054,7.035,53.48749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.16333,53.52055,8.23194,53.52221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.23194,53.52221,8.16333,53.52055), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.31194,53.52415,8.23194,53.52221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.54889,53.52943,8.56833,53.53333), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.56833,53.53333,9.80222,53.53471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.80222,53.53471,8.56833,53.53333), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.14722,53.53721,9.80222,53.53471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.82472,53.55027,8.15944,53.56248), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.15944,53.56248,7.08889,53.5711), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.08889,53.5711,9.67417,53.57443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.67417,53.57443,7.08889,53.5711), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.24306,53.5861,7.09611,53.59193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.58278,53.5861,7.09611,53.59193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.09611,53.59193,8.24306,53.5861), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.51972,53.60304,8.34139,53.6136), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.34139,53.6136,8.28333,53.61388), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.28333,53.61388,8.34139,53.6136), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.05055,53.63221,9.54667,53.6336), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.54667,53.6336,8.05055,53.63221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.28639,53.66915,7.2525,53.67387), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.2525,53.67387,14.28639,53.66915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.39083,53.68638,14.27782,53.69392), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.27782,53.69392,14.2375,53.6986), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.2375,53.6986,8.48778,53.70193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.48778,53.70193,8.02639,53.7036), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.02639,53.7036,8.48778,53.70193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.52972,53.70776,8.02639,53.7036), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((7.84861,53.71416,9.52972,53.70776), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.14083,53.7386,9.43778,53.73943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.43778,53.73943,14.14083,53.7386), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.25771,53.74318,9.43778,53.73943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((14.23639,53.75916,14.25771,53.74318), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.36,53.78638,13.90361,53.80276), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.90361,53.80276,9.36,53.78638), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.9075,53.82804,9.37639,53.83138), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.37639,53.83138,8.9075,53.82804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.29522,53.83569,9.37639,53.83138), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.8075,53.85526,8.72805,53.85777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.72805,53.85777,13.8075,53.85526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.12889,53.8661,8.72805,53.85777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.60417,53.87943,9.12889,53.8661), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.68056,53.89471,8.96194,53.8986), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.96194,53.8986,11.45417,53.9011), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.45417,53.9011,8.96194,53.8986), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.91306,53.91749,11.27694,53.93166), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.27694,53.93166,11.24222,53.94471), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.24222,53.94471,11.33805,53.95721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.33805,53.95721,10.90263,53.95998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.90263,53.95998,11.33805,53.95721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.47139,53.96777,10.90263,53.95998), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.87055,53.99194,10.79,53.99721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.79,53.99721,10.87055,53.99194), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.85694,54.00277,11.04583,54.00665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.04583,54.00665,8.85694,54.00277), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.19222,54.01054,11.04583,54.00665), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.00305,54.02805,13.75056,54.0286), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.75056,54.0286,9.00305,54.02805), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.75639,54.03443,13.75056,54.0286), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.85778,54.04221,11.58639,54.04249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.58639,54.04249,8.85778,54.04221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.49972,54.0861,10.87389,54.08777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.87389,54.08777,13.49972,54.0861), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.01778,54.0911,10.87389,54.08777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.80222,54.09554,13.80805,54.09693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.80805,54.09693,10.80222,54.09554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.62444,54.11027,13.80805,54.09693), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.84472,54.13304,8.97028,54.14555), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.97028,54.14555,13.38833,54.14721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.38833,54.14721,8.97028,54.14555), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.71167,54.17138,12.02,54.17971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.02,54.17971,12.1,54.18166), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.1,54.18166,12.02,54.17971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.09305,54.19749,8.82333,54.20721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.82333,54.20721,11.09305,54.19749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.41805,54.26082,13.16083,54.26249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.16083,54.26249,12.41805,54.26082), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.36972,54.26499,13.16083,54.26249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.91611,54.27082,12.36972,54.26499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.63167,54.27915,8.79389,54.28416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.79389,54.28416,12.42056,54.28526), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.42056,54.28526,8.79389,54.28416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.34361,54.29777,12.46194,54.30249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.46194,54.30249,12.34361,54.29777), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.78,54.30777,12.46194,54.30249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.95805,54.31693,10.665,54.32443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.665,54.32443,8.59944,54.32749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.59944,54.32749,10.665,54.32443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.07889,54.33166,8.59944,54.32749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.84889,54.34193,8.60917,54.34554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.60917,54.34554,12.81611,54.34637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.81611,54.34637,8.60917,54.34554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.06472,54.34999,12.81611,54.34637), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.69389,54.35749,10.15167,54.3636), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.15167,54.3636,13.09222,54.36943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((13.09222,54.36943,10.15167,54.3636), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.12917,54.37554,8.65417,54.37582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.65417,54.37582,11.12917,54.37554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.74722,54.37916,8.65417,54.37582), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.46,54.39221,11.12222,54.39249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((11.12222,54.39249,12.46,54.39221), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.19806,54.39582,11.12222,54.39249), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.60583,54.4061,12.7125,54.40943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.7125,54.40943,12.60583,54.4061), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.77583,54.41666,10.23805,54.41721), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.23805,54.41721,12.77583,54.41666), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.90167,54.41943,12.9175,54.41999), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.9175,54.41999,8.90167,54.41943), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.3725,54.43416,12.99806,54.43443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.99806,54.43443,10.3725,54.43416), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.91166,54.43971,12.99806,54.43443), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((12.48722,54.45499,10.20278,54.45805), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.20278,54.45805,12.48722,54.45499), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.86583,54.47749,9.01472,54.48026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.01472,54.48026,9.86583,54.47749), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.12694,54.48971,9.01472,54.48026), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.97496,54.51433,10.12694,54.48971), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.02778,54.5511,8.97496,54.51433), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.85083,54.62082,9.93093,54.66756), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.93093,54.66756,10.03778,54.66832), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.03778,54.66832,9.93093,54.66756), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.80562,54.68522,10.01583,54.69554), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((10.01583,54.69554,8.80562,54.68522), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.67611,54.77915,9.91667,54.7911), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.91667,54.7911,8.67611,54.77915), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.45167,54.80693,9.91667,54.7911), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.44517,54.82478,9.57833,54.82555), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.44517,54.82478,9.57833,54.82555), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.57833,54.82555,9.44517,54.82478), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.64222,54.82638,9.57833,54.82555), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.50312,54.84825,9.57694,54.86193), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((9.57694,54.86193,9.50312,54.84825), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.71944,54.8911,8.92,54.90804), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.92,54.90804,8.6563,54.9174), mapfile, tile_dir, 0, 11, "de-germany")
	render_tiles((8.6563,54.9174,8.92,54.90804), mapfile, tile_dir, 0, 11, "de-germany")