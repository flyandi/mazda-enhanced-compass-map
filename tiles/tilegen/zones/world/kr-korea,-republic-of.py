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
    # Region: KR
    # Region Name: Korea, Republic of

	render_tiles((128.5883,34.69943,128.6463,34.71249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.5883,34.69943,128.6463,34.71249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6463,34.71249,128.5883,34.69943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6638,34.7686,128.743,34.78805), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.743,34.78805,128.52609,34.80582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.52609,34.80582,128.743,34.78805), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6008,34.83054,128.49049,34.84583), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.49049,34.84583,128.6008,34.83054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.63831,34.88416,128.5372,34.90638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.5372,34.90638,128.63831,34.88416), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6022,34.94027,128.7283,34.94321), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.7283,34.94321,128.6022,34.94027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.71629,34.99443,128.7025,35.03027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.7025,35.03027,128.71629,34.99443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2739,33.19027,126.4825,33.22137), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2739,33.19027,126.4825,33.22137), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4825,33.22137,126.3461,33.22778), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3461,33.22778,126.4825,33.22137), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1705,33.26249,126.3461,33.22778), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1583,33.31471,126.8511,33.3161), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8511,33.3161,126.1583,33.31471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3125,33.45027,126.9514,33.46971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.9514,33.46971,126.3125,33.45027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8397,33.53616,126.7472,33.54638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7472,33.54638,126.8397,33.53616), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5197,34.28638,126.6003,34.30027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6003,34.30027,126.5197,34.28638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5241,34.33916,126.4783,34.34527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4783,34.34527,126.5241,34.33916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6292,34.38805,126.5222,34.40971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5222,34.40971,126.8894,34.41249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8894,34.41249,126.5222,34.40971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3186,34.4336,126.9441,34.44471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4722,34.4336,126.9441,34.44471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.9441,34.44471,126.8969,34.44916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8969,34.44916,126.8094,34.45026), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8094,34.45026,126.8969,34.44916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7608,34.47832,126.4847,34.50277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4847,34.50277,126.5386,34.50665), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5386,34.50665,126.7342,34.50888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7342,34.50888,127.4189,34.51054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4189,34.51054,127.1683,34.51193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.1683,34.51193,127.4189,34.51054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3925,34.52805,127.4358,34.53471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4358,34.53471,127.1247,34.53721), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.1247,34.53721,127.4358,34.53471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2272,34.5436,127.1247,34.53721), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.1316,34.56527,127.3472,34.56554), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3472,34.56554,127.1316,34.56527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5316,34.5686,127.2422,34.56944), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2422,34.56944,126.5316,34.5686), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.1927,34.56944,126.5316,34.5686), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.5047,34.5761,126.4516,34.57804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4516,34.57804,127.3397,34.57915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3397,34.57915,126.7991,34.57943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7991,34.57943,127.3397,34.57915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7775,34.58554,126.7991,34.57943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2586,34.59444,126.475,34.59971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.475,34.59971,127.2586,34.59444), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.0078,34.61166,126.5289,34.61416), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5289,34.61416,127.635,34.61499), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.635,34.61499,126.5289,34.61416), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6202,34.62415,127.4889,34.62498), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4889,34.62498,126.6202,34.62415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2119,34.63249,126.2728,34.63943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2728,34.63943,127.2119,34.63249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3497,34.64804,126.4514,34.64832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4514,34.64832,126.3497,34.64804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3864,34.64915,126.4514,34.64832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2964,34.65193,126.3864,34.64915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3325,34.65638,127.2964,34.65193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.555,34.66832,127.2422,34.66971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2422,34.66971,127.555,34.66832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5475,34.67387,127.2422,34.66971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2016,34.69027,126.415,34.69138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.415,34.69138,127.2016,34.69027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.6258,34.69415,127.288,34.69471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.288,34.69471,127.6258,34.69415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3875,34.70193,127.288,34.69471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.733,34.71888,127.2955,34.72137), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2955,34.72137,127.733,34.71888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3694,34.73305,127.6564,34.73415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.6564,34.73415,126.3283,34.73471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3283,34.73471,127.6564,34.73415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3353,34.74194,127.5878,34.74805), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.5878,34.74805,127.4005,34.74832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4005,34.74832,127.5878,34.74805), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4583,34.74915,127.4005,34.74832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2969,34.7511,126.4583,34.74915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.2461,34.75999,126.3939,34.76221), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3939,34.76221,127.2461,34.75999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3541,34.76971,126.3939,34.76221), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5089,34.77894,126.6292,34.78054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6292,34.78054,126.5089,34.77894), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4161,34.78665,126.6292,34.78054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4217,34.79305,127.7694,34.79499), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.7694,34.79499,127.4217,34.79305), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5594,34.79943,126.3711,34.80054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3711,34.80054,126.5594,34.79943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.6547,34.81194,126.6597,34.81277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6597,34.81277,127.6547,34.81194), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.3616,34.81776,126.5775,34.82138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5775,34.82138,127.3616,34.81776), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.4286,34.83388,127.4916,34.83415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.4916,34.83415,128.4286,34.83388), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.7736,34.84388,127.4916,34.83415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4158,34.84388,127.4916,34.83415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5733,34.85416,127.7736,34.84388), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.36079,34.86555,128.463,34.8686), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.463,34.8686,128.36079,34.86555), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.515,34.8836,128.2039,34.88832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.2039,34.88832,128.3183,34.89054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3183,34.89054,128.2039,34.88832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.2838,34.8986,127.6352,34.90166), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.6352,34.90166,128.2838,34.8986), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3566,34.90527,127.5705,34.90749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.5705,34.90749,128.3566,34.90527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4058,34.91499,127.5705,34.90749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.21111,34.92805,128.265,34.92971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.265,34.92971,126.3694,34.93082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3694,34.93082,128.265,34.92971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.05611,34.93082,128.265,34.92971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.7108,34.93304,126.3694,34.93082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3175,34.93665,128.43739,34.93888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.43739,34.93888,126.3175,34.93665), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.8572,34.94166,128.43739,34.93888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.5933,34.94166,128.43739,34.93888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3291,34.94943,127.9094,34.95527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.9094,34.95527,128.3291,34.94943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3458,34.97083,128.0186,34.97943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.0186,34.97943,128.4183,34.98138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.4183,34.98138,127.7197,34.98305), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.7197,34.98305,126.3936,34.9836), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3936,34.9836,127.7197,34.98305), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.9683,34.99055,126.3936,34.9836), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.5002,34.99832,128.3725,35.00471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3725,35.00471,128.5002,34.99832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4211,35.01804,128.3725,35.00471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3722,35.03387,128.9738,35.03416), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.9738,35.03416,126.3722,35.03387), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.38609,35.04221,126.2978,35.04582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2978,35.04582,128.4875,35.04749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.4875,35.04749,126.2978,35.04582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6277,35.04999,128.4875,35.04749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.04359,35.05499,128.6277,35.04999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4572,35.07443,128.63361,35.07915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.63361,35.07915,126.4572,35.07443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3066,35.08638,128.9191,35.08916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.9191,35.08916,128.5724,35.09193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.5724,35.09193,128.735,35.09277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.735,35.09277,128.5724,35.09193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2594,35.0961,128.735,35.09277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.9028,35.11656,126.4114,35.11916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4114,35.11916,128.9028,35.11656), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.308,35.12749,126.2636,35.13471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2636,35.13471,128.9691,35.13582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.9691,35.13582,126.2636,35.13471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6208,35.16055,128.57269,35.16999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.57269,35.16999,128.6208,35.16055), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.378,35.20721,128.593,35.21277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.593,35.21277,129.2561,35.21804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.2561,35.21804,128.593,35.21277), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3089,35.23249,129.2561,35.21804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.33051,35.32971,126.4522,35.33693), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4522,35.33693,129.33051,35.32971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4097,35.36388,126.4522,35.33693), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.3694,35.39943,126.4219,35.40665), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4219,35.40665,129.3694,35.39943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4222,35.47083,126.4969,35.51249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4969,35.51249,129.36971,35.53027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.36971,35.53027,126.6875,35.53915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6875,35.53915,129.36971,35.53027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4986,35.57804,129.4744,35.58027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4744,35.58027,126.4986,35.57804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6586,35.59415,129.4744,35.58027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4686,35.60915,126.6586,35.59415), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4794,35.63999,126.4686,35.60915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7869,35.76138,126.7066,35.77082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7066,35.77082,129.5074,35.77999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.5074,35.77999,126.7066,35.77082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7836,35.80054,129.5074,35.77999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6977,35.83804,126.7119,35.85471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7119,35.85471,126.7878,35.86054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7878,35.86054,126.7119,35.85471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6142,35.8961,126.8278,35.90694), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8278,35.90694,126.6142,35.8961), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6369,35.96971,126.7544,35.98666), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7544,35.98666,129.463,35.99471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.463,35.99471,129.5858,35.99554), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.5858,35.99554,129.463,35.99471), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.685,36.0011,129.5858,35.99554), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.3983,36.01276,126.685,36.0011), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6578,36.04332,129.52769,36.05138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.52769,36.05138,129.3911,36.05527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.3911,36.05527,129.52769,36.05138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8665,36.06078,129.57359,36.06443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.57359,36.06443,126.8665,36.06078), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.43111,36.08749,129.57359,36.06443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.51,36.15249,126.5986,36.17027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5986,36.17027,126.51,36.15249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.3766,36.19332,126.5986,36.17027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5378,36.22527,129.3766,36.19332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5819,36.29971,126.5153,36.32138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5153,36.32138,126.5819,36.29971), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5811,36.35555,126.5153,36.32138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4958,36.39416,126.5811,36.35555), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5075,36.43638,126.5886,36.46054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5886,36.46054,126.4805,36.4811), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4805,36.4811,129.4505,36.50054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4505,36.50054,126.4805,36.4811), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5144,36.52666,126.4711,36.53721), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4711,36.53721,126.5144,36.52666), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2894,36.58471,126.5247,36.58693), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5247,36.58693,126.4741,36.58804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4741,36.58804,126.5247,36.58693), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3441,36.60944,129.4169,36.61665), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4169,36.61665,126.3441,36.60944), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3722,36.63499,126.4125,36.6486), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4125,36.6486,126.3722,36.63499), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5216,36.66749,126.1661,36.67527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1661,36.67527,126.235,36.68249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.235,36.68249,126.1661,36.67527), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.48,36.69054,126.3036,36.69276), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3036,36.69276,126.48,36.69054), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1586,36.69999,126.3036,36.69276), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3852,36.7086,126.1236,36.70915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1236,36.70915,126.3852,36.7086), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2191,36.71027,126.1236,36.70915), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2728,36.71749,126.4972,36.72388), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4972,36.72388,126.2728,36.71749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3475,36.73999,126.465,36.74026), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.465,36.74026,126.3475,36.73999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1864,36.75304,126.8386,36.75332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8386,36.75332,126.1864,36.75304), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4738,36.76249,126.8386,36.75332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2083,36.77499,126.2894,36.78582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2894,36.78582,126.3566,36.78638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3566,36.78638,126.2894,36.78582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8744,36.79249,126.3566,36.78638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1653,36.81026,126.8744,36.79249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.365,36.83415,126.3269,36.84138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3269,36.84138,126.4833,36.84249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4833,36.84249,126.3269,36.84138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8608,36.84637,126.4833,36.84249), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2278,36.86665,126.2786,36.86694), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2786,36.86694,126.2278,36.86665), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.87,36.87193,126.2786,36.86694), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.1844,36.87832,126.87,36.87193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4594,36.88527,126.1844,36.87832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2855,36.8986,126.2339,36.89888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.2339,36.89888,126.2855,36.8986), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5786,36.90027,126.2339,36.89888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.418,36.90415,126.5786,36.90027), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8255,36.90916,126.5019,36.91082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5019,36.91082,126.8255,36.90916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.0028,36.9161,126.908,36.91943), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.908,36.91943,127.0028,36.9161), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.9714,36.92805,126.5369,36.9311), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5369,36.9311,126.478,36.9336), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.478,36.9336,126.5369,36.9311), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6261,36.94666,126.6578,36.94749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6578,36.94749,126.6261,36.94666), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.345,36.95666,126.6578,36.94749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7766,36.96721,126.5772,36.97305), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5772,36.97305,126.7766,36.96721), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.0316,36.99026,126.3769,36.99194), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.3769,36.99194,127.0316,36.99026), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.4919,37.0011,126.813,37.00443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.813,37.00443,126.4919,37.0011), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.4138,37.0161,126.5716,37.02138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5716,37.02138,126.913,37.0236), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.913,37.0236,126.5716,37.02138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8122,37.03082,126.853,37.03638), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.853,37.03638,126.8122,37.03082), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7528,37.0486,126.5025,37.05276), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5025,37.05276,126.7528,37.0486), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.43359,37.05888,126.5025,37.05276), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.875,37.09888,126.6972,37.12193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6972,37.12193,126.7764,37.1236), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7764,37.1236,126.6972,37.12193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8686,37.14582,129.35831,37.15499), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.35831,37.15499,126.8686,37.14582), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.8561,37.17443,129.35831,37.15499), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6761,37.21054,126.7416,37.24055), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7416,37.24055,126.668,37.25555), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.668,37.25555,126.7416,37.24055), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.83,37.27693,126.668,37.25555), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.308,37.30276,126.7661,37.30832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7661,37.30832,129.308,37.30276), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7503,37.38332,126.7105,37.38888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7105,37.38888,126.7503,37.38332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6219,37.48138,126.6708,37.49776), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6708,37.49776,126.6219,37.48138), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.0663,37.62444,126.7494,37.63749), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7494,37.63749,129.0663,37.62444), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.683,37.65193,126.5464,37.65221), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5464,37.65221,126.683,37.65193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((129.063,37.66888,126.6566,37.67944), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6566,37.67944,126.6805,37.68443), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6805,37.68443,126.6566,37.67944), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6619,37.74999,126.5391,37.76332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.5391,37.76332,126.6619,37.74999), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6886,37.82732,126.6973,37.8352), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6886,37.82732,126.6973,37.8352), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6973,37.8352,126.6886,37.82732), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.6866,37.94804,126.7666,37.96832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.7666,37.96832,126.6866,37.94804), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.6969,38.0586,126.7666,37.96832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((126.9905,38.21804,127.8227,38.29916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.8227,38.29916,127.1975,38.31193), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.1975,38.31193,127.8227,38.29916), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.13409,38.32832,127.7764,38.33332), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((127.7764,38.33332,128.13409,38.32832), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.2733,38.4236,128.4355,38.49776), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.4355,38.49776,128.3105,38.50166), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3105,38.50166,128.4355,38.49776), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3036,38.58387,128.36549,38.61888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.36549,38.61888,128.3654,38.62253), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")
	render_tiles((128.3654,38.62253,128.36549,38.61888), mapfile, tile_dir, 0, 11, "kr-korea,-republic-of")