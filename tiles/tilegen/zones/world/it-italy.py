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
    # Region: IT
    # Region Name: Italy

	render_tiles((15.09,36.65083,15.13639,36.68138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.09,36.65083,15.13639,36.68138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.13639,36.68138,15.09,36.65083), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.70611,36.71333,14.90083,36.72499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.90083,36.72499,14.70611,36.71333), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.09555,36.7836,14.46139,36.81388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.46139,36.81388,15.09555,36.7836), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.15889,36.92055,14.39778,36.93665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.39778,36.93665,15.15889,36.92055), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.33861,37.01193,14.26889,37.04804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.26889,37.04804,15.33861,37.01193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.89472,37.09776,15.30333,37.10221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.30333,37.10221,13.89472,37.09776), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.10972,37.10888,15.30333,37.10221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.20389,37.15749,14.10972,37.10888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.18889,37.2186,15.26139,37.2461), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.26139,37.2461,15.18889,37.2186), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.56194,37.27915,15.15667,37.29527), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.15667,37.29527,13.43166,37.30082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.43166,37.30082,15.15667,37.29527), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.09194,37.34971,13.43166,37.30082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.09139,37.48999,13.12139,37.49082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.12139,37.49082,15.09139,37.48999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.01972,37.49388,13.12139,37.49082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.97389,37.55054,12.67444,37.55415), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.67444,37.55415,12.97389,37.55054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.17805,37.57555,12.82528,37.57888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.82528,37.57888,15.17805,37.57555), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.46833,37.69916,15.215,37.75888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.215,37.75888,12.46833,37.69916), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.46083,37.83499,15.215,37.75888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.71139,37.97665,13.8825,37.99721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.8825,37.99721,14.28944,38.01276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.28944,38.01276,15.44305,38.01665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.44305,38.01665,14.28944,38.01276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.89667,38.02332,15.44305,38.01665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.02472,38.04555,12.85611,38.05054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.85611,38.05054,12.55139,38.05444), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.55139,38.05444,12.85611,38.05054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.53778,38.0611,12.55139,38.05444), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.62861,38.07138,13.04861,38.07166), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.04861,38.07166,14.62861,38.07138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.07333,38.0936,13.4375,38.09527), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.4375,38.09527,13.07333,38.0936), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.53611,38.10944,12.71333,38.11471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.71333,38.11471,13.53611,38.10944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.36361,38.12471,12.71333,38.11471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.78639,38.13805,15.13139,38.13888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.13139,38.13888,12.78639,38.13805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.37527,38.15388,14.74,38.16554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.74,38.16554,13.07861,38.17138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.07861,38.17138,15.2,38.17554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.2,38.17554,13.07861,38.17138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.71916,38.1811,15.2,38.17554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.24972,38.20888,13.31139,38.21804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.31139,38.21804,15.36639,38.22137), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.36639,38.22137,13.31139,38.21804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.5725,38.23388,15.36639,38.22137), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.62389,38.25638,15.22833,38.26554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.22833,38.26554,15.62389,38.25638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.52361,38.29527,15.22833,38.26554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.85555,38.87749,8.62278,38.89165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.85555,38.87749,8.62278,38.89165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.62278,38.89165,8.85555,38.87749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.72917,38.93471,8.39861,38.9761), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.39861,38.9761,9.02222,38.98971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.02222,38.98971,8.39861,38.9761), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.46805,39.0561,8.46333,39.06165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.46333,39.06165,8.46805,39.0561), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.48611,39.07777,8.51861,39.07832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.51861,39.07832,8.48611,39.07777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.34972,39.09055,9.51944,39.09943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.51944,39.09943,8.42444,39.10777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.42444,39.10777,9.51944,39.09943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.0175,39.14193,8.42444,39.10777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.15028,39.18332,9.08361,39.21665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.08361,39.21665,9.18389,39.21777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.18389,39.21777,8.36778,39.21832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.36778,39.21832,9.18389,39.21777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.23555,39.2236,8.36778,39.21832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.59166,39.27721,8.43278,39.28526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.43278,39.28526,9.59166,39.27721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.63333,39.2986,8.43278,39.28526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.60028,39.33276,9.63333,39.2986), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.62417,39.41221,8.38333,39.4586), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.38333,39.4586,9.62417,39.41221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.4675,39.59304,8.53305,39.70277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.53305,39.70277,8.52111,39.74944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.52111,39.74944,8.44278,39.75665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.44278,39.75665,8.52111,39.74944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.67028,39.77026,8.44278,39.75665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.55694,39.85749,8.45889,39.89526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.45889,39.89526,8.39861,39.89665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.39861,39.89665,8.45889,39.89526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.51805,39.90499,8.39861,39.89665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.37472,40.03194,8.47278,40.06443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.47278,40.06443,9.72917,40.08443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.72917,40.08443,8.48917,40.0936), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.48917,40.0936,9.72917,40.08443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.6925,40.10693,8.48917,40.0936), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.62417,40.20832,8.46139,40.22388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.46139,40.22388,9.62417,40.20832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.48139,40.28082,9.65722,40.30832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.65722,40.30832,8.48139,40.28082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.38444,40.34249,9.65722,40.30832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.77194,40.39471,8.38444,40.34249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.37167,40.48249,9.82555,40.52165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.82555,40.52165,9.80639,40.53805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.80639,40.53805,9.82555,40.52165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.15944,40.56277,9.80639,40.53805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.30111,40.58916,8.15944,40.56277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.14361,40.62166,8.30111,40.58916), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.74694,40.68249,8.19861,40.68471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.19861,40.68471,9.74694,40.68249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.1325,40.72999,8.19861,40.68471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.66778,40.79749,9.68944,40.81194), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.68944,40.81194,8.4575,40.82193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.4575,40.82193,9.68944,40.81194), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.57972,40.83999,8.4575,40.82193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.27806,40.86443,9.65111,40.87804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.65111,40.87804,8.22167,40.87971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.22167,40.87971,9.65111,40.87804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.50361,40.91805,8.17778,40.92971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.17778,40.92971,9.56444,40.93999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.56444,40.93999,8.17778,40.92971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.20278,40.97054,9.56444,40.93999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.6525,41.00332,9.50861,41.01443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.50861,41.01443,9.6525,41.00332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.88944,41.02583,9.50861,41.01443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.44167,41.09026,9.56555,41.10471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.56555,41.10471,9.44167,41.09026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.51333,41.15027,9.15222,41.1561), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.15222,41.1561,9.51333,41.15027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.42139,41.1761,9.31472,41.19221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.31472,41.19221,9.42139,41.1761), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.25639,41.24332,9.21583,41.25416), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.21583,41.25416,9.25639,41.24332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.77583,37.91693,16,37.91805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16,37.91805,15.77583,37.91693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.06249,37.92416,16,37.91805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.67528,37.95221,16.11666,37.97971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.11666,37.97971,15.67528,37.95221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.6325,38.00804,16.11666,37.97971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.16555,38.13915,15.62889,38.2286), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.62889,38.2286,15.79305,38.28054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.79305,38.28054,16.32694,38.2961), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.32694,38.2961,15.79305,38.28054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.49055,38.35805,16.32694,38.2961), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.57139,38.43138,15.90111,38.46221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.90111,38.46221,16.57666,38.47166), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.57666,38.47166,15.90111,38.46221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.92528,38.54137,16.57666,38.47166), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.82833,38.62054,15.85305,38.66026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.85305,38.66026,15.82833,38.62054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.12916,38.71555,16.53888,38.72249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.53888,38.72249,15.99416,38.72276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.99416,38.72276,16.53888,38.72249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.18111,38.74749,15.99416,38.72276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.62111,38.82193,16.21999,38.84304), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.21999,38.84304,16.62111,38.82193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.10361,38.89526,16.83694,38.91749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.83694,38.91749,17.06139,38.92027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.06139,38.92027,16.2211,38.92249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.2211,38.92249,17.06139,38.92027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.10611,39.01443,17.20416,39.02805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.20416,39.02805,16.10611,39.01443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.13499,39.06805,17.20416,39.02805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.14527,39.19527,17.10944,39.26749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.10944,39.26749,17.14527,39.19527), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.02333,39.36277,17.16138,39.40083), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.16138,39.40083,16.02333,39.36277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.93388,39.51193,15.90666,39.53138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.90666,39.53138,16.93388,39.51193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.83972,39.62693,16.54305,39.65027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.54305,39.65027,15.83972,39.62693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.49055,39.74915,18.34944,39.79193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.34944,39.79193,18.39083,39.81443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.39083,39.81443,16.49805,39.82082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.49805,39.82082,18.39083,39.81443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.05249,39.9261,15.73944,39.93443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.73944,39.93443,18.05249,39.9261), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.63305,39.96277,15.41694,39.99026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.41694,39.99026,17.99472,39.99416), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.99472,39.99416,18.41972,39.99582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.41972,39.99582,17.99472,39.99416), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.60749,40.00526,18.02222,40.0136), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.02222,40.0136,16.60749,40.00526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.26583,40.02749,18.02222,40.0136), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.48167,40.04887,15.27444,40.06138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.27444,40.06138,15.63055,40.07027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.63055,40.07027,15.51972,40.07332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.51972,40.07332,15.63055,40.07027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.61222,40.09304,15.51972,40.07332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.51083,40.13888,16.69527,40.15276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.69527,40.15276,18.51083,40.13888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.12166,40.16971,16.69527,40.15276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.96611,40.21999,14.90911,40.23771), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.90911,40.23771,14.96611,40.21999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.89444,40.26027,16.75861,40.26221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.75861,40.26221,17.89444,40.26027), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.68472,40.30332,17.39833,40.32971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.39833,40.32971,18.35944,40.34221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.35944,40.34221,17.39833,40.32971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.98972,40.35832,18.35944,40.34221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.99055,40.39915,17.20444,40.40721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.20444,40.40721,14.99055,40.39915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.94722,40.47083,17.22944,40.47415), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.22944,40.47415,16.94722,40.47083), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.26944,40.48554,17.22944,40.47415), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.08972,40.52082,18.03971,40.5536), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.03971,40.5536,14.33166,40.57277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.33166,40.57277,18.03971,40.5536), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.53111,40.60777,14.44833,40.61832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.44833,40.61832,14.33944,40.61916), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.33944,40.61916,14.44833,40.61832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.59778,40.63221,18.01167,40.64443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((18.01167,40.64443,17.95275,40.64459), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.95275,40.64459,18.01167,40.64443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.77972,40.66971,17.89472,40.68249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.89472,40.68249,14.77972,40.66971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.47,40.69554,17.89472,40.68249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.45805,40.74277,14.08389,40.78221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.08389,40.78221,14.04417,40.79499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.04417,40.79499,14.08389,40.78221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.47527,40.82777,14.28555,40.83693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.28555,40.83693,17.47527,40.82777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.03778,40.88165,14.28555,40.83693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((17.28277,40.97305,14.03778,40.88165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.86694,41.13026,13.82611,41.16249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.82611,41.16249,16.86694,41.13026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.09444,41.22054,13.50389,41.2211), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.50389,41.2211,13.09444,41.22054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.03944,41.22582,13.50389,41.2211), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.56222,41.23471,13.03944,41.22582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.71305,41.25138,13.62083,41.25777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.62083,41.25777,13.71305,41.25138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.43999,41.26665,13.62083,41.25777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.21111,41.28249,13.32306,41.29276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.32306,41.29276,13.21111,41.28249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.95194,41.35304,12.855,41.40999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.855,41.40999,16.03305,41.41888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.03305,41.41888,12.855,41.40999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.62472,41.44638,16.03305,41.41888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.92,41.4936,12.62472,41.44638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.88972,41.5711,15.92,41.4936), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.36,41.68916,12.22722,41.73999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.22722,41.73999,16.19111,41.78499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.19111,41.78499,12.22722,41.73999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.17583,41.8711,16.17666,41.8886), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.17666,41.8886,12.17583,41.8711), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.34666,41.91026,15.83139,41.92332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((15.83139,41.92332,15.34666,41.91026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((16.01805,41.94721,15.83139,41.92332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.88889,42.02165,11.92639,42.02999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.92639,42.02999,14.88889,42.02165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.77722,42.08943,14.72278,42.10055), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.72278,42.10055,11.77722,42.08943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.71556,42.17194,14.62416,42.19999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.62416,42.19999,14.71556,42.17194), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.63527,42.29249,11.15028,42.36388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.15028,42.36388,11.37472,42.40499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.37472,42.40499,11.21139,42.41471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.21139,42.41471,14.30305,42.41499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.30305,42.41499,11.21139,42.41471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.08583,42.42194,14.30305,42.41499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.16833,42.44749,11.08583,42.42194), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.18972,42.50638,11.16833,42.44749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((14.05833,42.61971,11.08027,42.63221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.08027,42.63221,14.05833,42.61971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.75028,42.80749,10.77944,42.88749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.77944,42.88749,10.76111,42.91471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.76111,42.91471,13.89139,42.92666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.89139,42.92666,10.49917,42.93221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.49917,42.93221,13.89139,42.92666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.67361,42.94832,10.49917,42.93221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.48028,42.9836,10.67361,42.94832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.525,43.02527,10.48028,42.9836), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.5425,43.15443,10.51944,43.2561), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.51944,43.2561,13.75972,43.26138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.75972,43.26138,10.51944,43.2561), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.29667,43.53693,13.59944,43.56999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.59944,43.56999,10.29667,43.53693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.42194,43.62444,13.59944,43.56999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.53476,43.78345,7.82861,43.81944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.82861,43.81944,13.075,43.82193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.075,43.82193,7.82861,43.81944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.49222,43.86943,10.21417,43.9061), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.21417,43.9061,8.12111,43.92776), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.12111,43.92776,10.21417,43.9061), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.67805,43.99332,8.15805,43.99443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.15805,43.99443,12.67805,43.99332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.09083,44.0186,9.84,44.03582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.84,44.03582,10.09083,44.0186), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.70417,44.07221,9.83917,44.07249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.83917,44.07249,7.70417,44.07221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.57474,44.08204,9.83917,44.07249), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.84028,44.1086,12.50305,44.12166), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.50305,44.12166,9.84028,44.1086), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.28417,44.14777,7.27944,44.15665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.27944,44.15665,8.28417,44.14777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.39944,44.17832,7.67278,44.18276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.67278,44.18276,8.39944,44.17832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.49667,44.21944,7.67278,44.18276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.21333,44.30054,8.46833,44.30082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.46833,44.30082,9.21333,44.30054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.93583,44.32332,8.46833,44.30082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.23361,44.34637,12.31805,44.3536), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.31805,44.3536,9.23361,44.34637), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.87194,44.40943,6.88611,44.41582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.88611,44.41582,8.87194,44.40943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.76111,44.42944,6.935,44.44304), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.935,44.44304,8.76111,44.42944), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.85722,44.50777,6.85278,44.54082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.85278,44.54082,6.85722,44.50777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.96028,44.62887,6.95083,44.6647), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.95083,44.6647,7.06889,44.68915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.06889,44.68915,7.00083,44.69915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.00083,44.69915,7.06889,44.68915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.24555,44.71582,7.00083,44.69915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.3875,44.78693,12.27917,44.81693), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.27917,44.81693,12.42528,44.81805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.42528,44.81805,12.44917,44.81832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.44917,44.81832,12.42528,44.81805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.01,44.84859,12.39139,44.87276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.39139,44.87276,12.44055,44.89499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.44055,44.89499,12.39139,44.87276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.51,44.91916,12.44055,44.89499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.74333,44.94776,12.51,44.91916), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.74667,45.02026,12.43472,45.02165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.43472,45.02165,6.74667,45.02026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.29361,45.09138,12.32611,45.10805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.32611,45.10805,6.62017,45.11066), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.62017,45.11066,12.32611,45.10805), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.85028,45.1361,6.62017,45.11066), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.76083,45.16832,12.23028,45.19999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.23028,45.19999,12.28417,45.20582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.28417,45.20582,12.23028,45.19999), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.16111,45.26388,12.19917,45.26777), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.19917,45.26777,12.16111,45.26388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.12278,45.30193,12.21833,45.30888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.21833,45.30888,12.15833,45.31388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.15833,45.31388,12.21833,45.30888), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.13305,45.35555,12.15833,45.31388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.18139,45.41165,12.41472,45.43193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.41472,45.43193,12.45639,45.44638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.45639,45.44638,12.26028,45.45666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.26028,45.45666,12.45639,45.44638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.45305,45.49332,12.45,45.50943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.45,45.50943,6.99944,45.51832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.99944,45.51832,12.45,45.50943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.56472,45.5286,6.99944,45.51832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.48,45.56582,13.85944,45.58749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.85944,45.58749,12.86861,45.59666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.86861,45.59666,13.71751,45.59766), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.71751,45.59766,12.86861,45.59666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.80444,45.60999,13.71751,45.59766), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.08611,45.63554,13.91916,45.63749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.91916,45.63749,13.08611,45.63554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.99083,45.63998,13.91916,45.63749), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.43361,45.67832,13.07555,45.69332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.07555,45.69332,13.15,45.69554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.15,45.69554,13.07555,45.69332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.07,45.71471,6.82417,45.71499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.82417,45.71499,13.07,45.71471), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.82194,45.72693,6.82417,45.71499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.34167,45.74026,13.52305,45.74666), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.52305,45.74666,13.34167,45.74026), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.63222,45.76916,13.13111,45.77193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.13111,45.77193,13.63222,45.76916), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.79917,45.78082,13.55,45.78721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.55,45.78721,6.79917,45.78082), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.59805,45.81081,9.03167,45.82388), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.03167,45.82388,6.80889,45.83138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((6.80889,45.83138,8.96305,45.83554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.96305,45.83554,6.80889,45.83138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.57778,45.85416,8.96305,45.83554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.09944,45.8836,9.08555,45.89915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.08555,45.89915,7.09944,45.8836), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.39333,45.91609,8.91724,45.91716), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.91724,45.91716,7.39333,45.91609), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.85278,45.91998,13.62361,45.92221), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.62361,45.92221,7.85278,45.91998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.03875,45.93172,8.90778,45.93277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.90778,45.93277,7.03875,45.93172), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.90717,45.9351,8.90778,45.93277), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.89727,45.95275,8.89555,45.95582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.89555,45.95582,8.89727,45.95275), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.67734,45.96124,8.89555,45.95582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.5425,45.96748,8.99666,45.97331), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.99666,45.97331,13.5425,45.96748), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.57889,45.98332,13.635,45.98859), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.635,45.98859,9.00625,45.98965), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.00625,45.98965,8.78528,45.98998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.78528,45.98998,9.00625,45.98965), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.9025,45.9911,8.78528,45.98998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.59,45.99332,7.9025,45.9911), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((7.99805,46.00221,13.59,45.99332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.01953,46.0123,13.47944,46.01332), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.47944,46.01332,9.01953,46.0123), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.85055,46.07249,8.69194,46.10138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.69194,46.10138,8.71161,46.10142), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.71161,46.10142,8.69194,46.10138), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.76157,46.10153,8.71161,46.10142), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.81305,46.10165,8.76157,46.10153), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.08333,46.12109,8.81305,46.10165), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.64583,46.14249,9.08333,46.12109), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.15889,46.17665,13.66444,46.18304), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.66444,46.18304,8.15889,46.17665), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.55055,46.21804,10.06305,46.22276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.06305,46.22276,13.55055,46.21804), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.44333,46.23026,10.06305,46.22276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.15944,46.24776,8.44444,46.2486), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.44444,46.2486,10.15944,46.24776), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.09027,46.26054,8.44444,46.2486), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.17722,46.27248,8.09027,46.26054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.61944,46.29305,13.38305,46.29721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.38305,46.29721,9.70833,46.29832), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.70833,46.29832,13.38305,46.29721), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.99639,46.31832,9.29194,46.32304), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.29194,46.32304,10.11,46.32638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.11,46.32638,9.29194,46.32304), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.5125,46.33193,10.11,46.32638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.45916,46.33832,9.5125,46.33193), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.7375,46.35721,13.48111,46.36943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.48111,46.36943,9.95,46.37915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.95,46.37915,13.48111,46.36943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.16305,46.41193,9.95,46.37915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.69222,46.45026,8.40111,46.45609), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.40111,46.45609,8.45166,46.45998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((8.45166,46.45998,8.40111,46.45609), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.40194,46.47331,10.05,46.47971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.05,46.47971,9.27222,46.48415), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.27222,46.48415,10.05,46.47971), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.36083,46.5086,9.45444,46.50943), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((9.45444,46.50943,9.36083,46.5086), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.71896,46.52554,10.44972,46.53915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.44972,46.53915,13.71896,46.52554), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.06833,46.55499,10.30166,46.55582), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.30166,46.55582,10.06833,46.55499), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((13.41222,46.57443,10.48528,46.59054), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.48528,46.59054,13.41222,46.57443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.10528,46.61137,12.89583,46.61276), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.89583,46.61276,10.10528,46.61137), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.47389,46.63332,10.23333,46.63998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.23333,46.63998,10.40528,46.64443), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.40528,46.64443,10.23333,46.63998), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.38528,46.68942,12.43361,46.69526), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.43361,46.69526,10.38528,46.68942), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.4475,46.76305,11.01472,46.77248), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.01472,46.77248,12.35583,46.77748), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.35583,46.77748,11.01472,46.77248), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.28361,46.79137,10.73555,46.7986), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.73555,46.7986,12.28361,46.79137), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.76444,46.83415,10.56083,46.84859), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.56083,46.84859,10.76444,46.83415), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.295,46.8636,10.46357,46.86935), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.46357,46.86935,10.67278,46.87498), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.46357,46.86935,10.67278,46.87498), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((10.67278,46.87498,10.46357,46.86935), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.14444,46.91582,10.67278,46.87498), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.16444,46.9622,11.41166,46.97248), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.41166,46.97248,11.75583,46.97748), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.75583,46.97748,11.41166,46.97248), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.33916,46.99554,11.61527,47.01305), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((11.61527,47.01305,12.13111,47.01638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.13111,47.01638,11.61527,47.01305), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.20444,47.03915,12.13111,47.01638), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.21111,47.09054,12.20444,47.03915), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4647,43.8956,12.4592,43.8961), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4592,43.8961,12.4647,43.8956), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4697,43.8961,12.4647,43.8956), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4744,43.8972,12.4542,43.8975), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4542,43.8975,12.4744,43.8972), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4778,43.8994,12.4542,43.8975), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4503,43.8994,12.4542,43.8975), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4811,43.9017,12.4475,43.9019), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4475,43.9019,12.4811,43.9017), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4844,43.9039,12.4453,43.9047), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4453,43.9047,12.4217,43.9053), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4164,43.9047,12.4217,43.9053), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4217,43.9053,12.4878,43.9058), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4878,43.9058,12.4122,43.9061), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4122,43.9061,12.4878,43.9058), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4256,43.9069,12.4428,43.9075), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4428,43.9075,12.4256,43.9069), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4303,43.9081,12.4906,43.9086), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4906,43.9086,12.4303,43.9081), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.41,43.9092,12.4392,43.9094), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4392,43.9094,12.41,43.9092), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4342,43.9097,12.4392,43.9094), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4928,43.9117,12.4083,43.9122), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4083,43.9122,12.4928,43.9117), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4077,43.91379,12.495,43.9147), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4077,43.91379,12.495,43.9147), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.495,43.9147,12.4077,43.91379), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4961,43.9189,12.4058,43.9194), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4058,43.9194,12.4961,43.9189), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4047,43.9231,12.4964,43.9233), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4964,43.9233,12.4047,43.9231), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4039,43.9267,12.4956,43.9272), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4956,43.9272,12.4039,43.9267), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4036,43.9308,12.4967,43.9311), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4967,43.9311,12.4036,43.9308), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4994,43.9339,12.5033,43.9356), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5033,43.9356,12.5072,43.9372), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4039,43.9356,12.5072,43.9372), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5072,43.9372,12.5033,43.9356), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5106,43.9392,12.405,43.9394), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.405,43.9394,12.5106,43.9392), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5117,43.9433,12.4047,43.9436), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4047,43.9436,12.5117,43.9433), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5108,43.9469,12.405,43.9483), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.405,43.9483,12.5108,43.9469), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5097,43.9508,12.4067,43.9519), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4067,43.9519,12.5097,43.9508), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5089,43.9544,12.4089,43.955), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4089,43.955,12.5089,43.9544), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4128,43.9567,12.5078,43.9581), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5078,43.9581,12.4167,43.9583), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4167,43.9583,12.5078,43.9581), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4214,43.9594,12.4167,43.9583), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4258,43.9606,12.4306,43.9617), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4306,43.9617,12.5069,43.9619), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5069,43.9619,12.4306,43.9617), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4344,43.9633,12.5069,43.9619), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4378,43.9656,12.5067,43.9661), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5067,43.9661,12.4378,43.9656), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4411,43.9678,12.5067,43.9661), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4439,43.9703,12.5072,43.9706), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5072,43.9706,12.4439,43.9703), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4456,43.9739,12.5075,43.9753), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5075,43.9753,12.4456,43.9739), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4478,43.9769,12.5075,43.9753), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5086,43.9792,12.4506,43.9797), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4506,43.9797,12.5086,43.9792), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4539,43.9817,12.5103,43.9828), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5103,43.9828,12.4581,43.9833), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4581,43.9833,12.5103,43.9828), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4625,43.9847,12.4669,43.9858), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4669,43.9858,12.4625,43.9847), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4717,43.9869,12.4767,43.9875), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.51,43.9869,12.4767,43.9875), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4767,43.9875,12.4717,43.9869), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4819,43.9883,12.5056,43.9886), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.5056,43.9886,12.4819,43.9883), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4872,43.9889,12.4928,43.9892), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4992,43.9889,12.4928,43.9892), mapfile, tile_dir, 0, 11, "it-italy")
	render_tiles((12.4928,43.9892,12.4872,43.9889), mapfile, tile_dir, 0, 11, "it-italy")