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
    # Region: KP
    # Region Name: Korea, Peoples Republic of

	render_tiles((125.3372,37.68027,126.1533,37.7336), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.1533,37.7336,126.1016,37.74249), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.1016,37.74249,126.1533,37.7336), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6089,37.75555,125.3619,37.75804), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3619,37.75804,125.6089,37.75555), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.5739,37.77443,125.5558,37.78582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.5558,37.78582,126.6466,37.78888), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.6466,37.78888,125.5558,37.78582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3114,37.80471,126.6466,37.78888), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.9828,37.82249,125.2386,37.82304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2386,37.82304,125.9828,37.82249), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.1436,37.8236,125.2386,37.82304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.5266,37.8261,126.6886,37.82732), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.6886,37.82732,125.5266,37.8261), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.9114,37.83887,125.4466,37.84304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4466,37.84304,125.9114,37.83887), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6411,37.84915,125.4466,37.84304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1625,37.86249,125.3594,37.86721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3594,37.86721,125.1625,37.86249), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3108,37.88165,125.218,37.88527), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.218,37.88527,126.385,37.88721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.385,37.88721,125.218,37.88527), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6811,37.89082,126.385,37.88721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4958,37.8961,125.9755,37.8986), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.9755,37.8986,125.4958,37.8961), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4264,37.90665,125.0044,37.9086), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0044,37.9086,125.4264,37.90665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6261,37.91776,125.7358,37.92387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.7358,37.92387,125.6261,37.91776), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2436,37.93166,125.7358,37.92387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.6866,37.94804,125.0016,37.95805), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0016,37.95805,125.1647,37.96027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1647,37.96027,125.0016,37.95805), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.7666,37.96832,125.1647,37.96027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1783,37.98138,125.7978,37.98971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.7978,37.98971,125.1053,37.99194), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1053,37.99194,125.7978,37.98971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2533,38.02055,125.5944,38.02637), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.5944,38.02637,125.2533,38.02055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1205,38.04332,125.5944,38.02637), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0075,38.06388,125.2408,38.08221), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2408,38.08221,124.7889,38.09527), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.7889,38.09527,125.2408,38.08221), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.6739,38.11416,124.8864,38.12943), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.8864,38.12943,124.6647,38.13554), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.6647,38.13554,124.8864,38.12943), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9269,38.1811,126.9905,38.21804), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.9905,38.21804,124.9908,38.22638), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9908,38.22638,124.878,38.22665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.878,38.22665,124.9908,38.22638), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.8708,38.24776,124.878,38.22665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.8716,38.28944,127.8227,38.29916), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.8227,38.29916,124.8716,38.28944), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.1975,38.31193,127.8227,38.29916), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.13409,38.32832,127.7764,38.33332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.7764,38.33332,128.13409,38.32832), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9166,38.39665,128.2733,38.4236), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2733,38.4236,124.9822,38.44027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9822,38.44027,128.2733,38.4236), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3105,38.50166,125.0186,38.50971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0186,38.50971,128.3105,38.50166), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9911,38.53693,125.0889,38.55305), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0889,38.55305,124.9911,38.53693), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3036,38.58387,125.0003,38.58943), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0003,38.58943,128.3036,38.58387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1783,38.62054,128.3654,38.62253), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3654,38.62253,125.1783,38.62054), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3654,38.62253,125.1783,38.62054), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1302,38.66527,125.5603,38.66999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.5603,38.66999,125.3308,38.67055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3308,38.67055,125.5603,38.66999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3622,38.67944,125.3308,38.67055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2847,38.69582,125.3733,38.70415), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3733,38.70415,125.2847,38.69582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4322,38.72221,128.21249,38.72305), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.21249,38.72305,125.4322,38.72221), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2389,38.72527,128.21249,38.72305), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.363,38.73277,125.2039,38.73777), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2039,38.73777,125.363,38.73277), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2636,38.74971,128.2769,38.75416), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2769,38.75416,125.2636,38.74971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4386,38.77749,125.2342,38.79332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2342,38.79332,125.1389,38.80166), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1389,38.80166,125.2342,38.79332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.1519,38.87554,125.1389,38.80166), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.7961,39.07138,125.2978,39.10138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2978,39.10138,127.7616,39.11916), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.7616,39.11916,125.2978,39.10138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5364,39.1411,127.7616,39.11916), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.4064,39.18971,125.3397,39.20332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3397,39.20332,127.4064,39.18971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4108,39.21944,127.3772,39.22777), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.3772,39.22777,125.4108,39.21944), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5194,39.31026,127.5614,39.31776), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5614,39.31776,127.5194,39.31026), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.4683,39.33638,127.3894,39.3411), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.3894,39.3411,125.4178,39.3436), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4178,39.3436,127.3894,39.3411), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.388,39.36137,127.4508,39.3711), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.4508,39.3711,127.388,39.36137), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.4111,39.39221,127.5272,39.39526), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5272,39.39526,127.4111,39.39221), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.48,39.42915,125.338,39.42999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.338,39.42999,127.48,39.42915), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.35,39.46443,127.528,39.48138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.528,39.48138,125.35,39.46443), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3661,39.55138,125.4477,39.55388), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4477,39.55388,125.3661,39.55138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4417,39.57888,124.6242,39.59499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.6242,39.59499,127.5772,39.59666), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5772,39.59666,124.6242,39.59499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.938,39.64471,124.7539,39.66471), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.7539,39.66471,127.5025,39.68416), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5025,39.68416,124.8839,39.70055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.8839,39.70055,127.5025,39.68416), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5039,39.71944,124.8839,39.70055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.7353,39.76971,127.6119,39.81165), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.6119,39.81165,124.5566,39.81277), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.5566,39.81277,127.6119,39.81165), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.4136,39.82332,124.5566,39.81277), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.7816,39.84776,124.4136,39.82332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.3236,39.91776,124.3753,39.94666), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.3753,39.94666,124.3236,39.91776), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3174,40.0236,128.2216,40.02388), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2216,40.02388,128.3174,40.0236), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.9978,40.04137,124.3802,40.04499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.3802,40.04499,127.9978,40.04137), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2639,40.0611,124.3802,40.04499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.3839,40.0836,124.3723,40.09431), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.3723,40.09431,128.3839,40.0836), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.6461,40.17582,128.6758,40.20471), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.6758,40.20471,128.6461,40.17582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.5922,40.26138,128.6366,40.27832), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.6366,40.27832,124.5922,40.26138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.7569,40.32749,128.8864,40.37138), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.8864,40.37138,128.7569,40.32749), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.9749,40.44916,124.9577,40.45277), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9577,40.45277,124.9632,40.45374), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.9632,40.45374,124.9577,40.45277), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.08411,40.46387,125.0427,40.46804), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0427,40.46804,129.08411,40.46387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((124.8914,40.47581,125.0427,40.46804), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0094,40.50999,125.0316,40.5279), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.0316,40.5279,125.0094,40.50999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.1866,40.60332,125.4058,40.62082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.4058,40.62082,129.1866,40.60332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.2915,40.63989,125.3293,40.64536), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.3293,40.64536,125.321,40.64804), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.321,40.64804,125.3293,40.64536), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.3011,40.67165,129.21381,40.6811), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.21381,40.6811,129.3011,40.67165), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.2608,40.69777,129.21381,40.6811), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6039,40.75582,129.4191,40.76082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.4191,40.76082,125.6039,40.75582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6844,40.76998,129.4191,40.76082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.58,40.78499,125.6844,40.76998), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.6472,40.81054,129.7094,40.82999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7094,40.82999,125.6472,40.81054), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.698,40.85915,129.7094,40.82999), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((125.9,40.89693,129.7558,40.93193), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7558,40.93193,126.0416,40.93776), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.0416,40.93776,129.7558,40.93193), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.0755,40.99748,126.0416,40.93776), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.71381,41.11221,126.0755,40.99748), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7908,41.34637,126.4561,41.35721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.4561,41.35721,126.5272,41.35749), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.5272,41.35749,126.4561,41.35721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.8058,41.37693,128.16051,41.3861), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.16051,41.3861,128.0441,41.38943), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.0441,41.38943,128.16051,41.3861), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.7094,41.41331,127.5572,41.42971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.5572,41.42971,129.703,41.43027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.703,41.43027,127.5572,41.42971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.5008,41.43193,129.703,41.43027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.9369,41.44693,126.5008,41.43193), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.2875,41.47109,127.4727,41.47276), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.4727,41.47276,127.2875,41.47109), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.5538,41.49693,127.4727,41.47276), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.2108,41.52193,129.66051,41.52527), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.66051,41.52527,127.2108,41.52193), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2822,41.53027,127.1089,41.53276), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.1089,41.53276,128.2822,41.53027), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.1061,41.54527,127.1089,41.53276), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.1786,41.58749,128.2972,41.59304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.2972,41.59304,127.1786,41.58749), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.5714,41.62026,128.2972,41.59304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.0452,41.65137,126.6769,41.66943), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.6769,41.66943,127.0452,41.65137), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.7869,41.69221,126.6969,41.71499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.6969,41.71499,128.1505,41.72082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.1505,41.72082,127.0452,41.72443), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((127.0452,41.72443,128.1505,41.72082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7605,41.73055,127.0452,41.72443), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.7103,41.74054,129.7605,41.73055), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.8777,41.79054,126.9283,41.79638), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((126.9283,41.79638,129.8777,41.79054), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.8963,41.88749,129.96111,41.88888), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.96111,41.88888,129.8963,41.88749), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.06081,41.91054,129.96111,41.88888), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.05881,42.00332,128.4538,42.00388), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.4538,42.00388,128.05881,42.00332), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.9258,42.02443,128.4225,42.02859), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.4225,42.02859,128.63049,42.02998), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.63049,42.02998,128.4225,42.02859), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.28239,42.03915,130.0508,42.04665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.0508,42.04665,128.28239,42.03915), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((128.96271,42.08443,130.20219,42.09471), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.20219,42.09471,128.96271,42.08443), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.1144,42.13943,130.2041,42.1636), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.2041,42.1636,130.3102,42.18416), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.3102,42.18416,130.2041,42.1636), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.2133,42.22304,130.3013,42.22499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.3013,42.22499,129.2133,42.22304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.1758,42.23499,130.3013,42.22499), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.4066,42.24693,130.59689,42.2536), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.59689,42.2536,130.4066,42.24693), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.2094,42.28638,130.69479,42.28882), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.69479,42.28882,129.2094,42.28638), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.578,42.30666,130.65491,42.31387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.65491,42.31387,130.403,42.31915), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.403,42.31915,130.65491,42.31387), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.54691,42.36443,130.6438,42.39971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.6438,42.39971,129.5705,42.40582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.5705,42.40582,130.6438,42.39971), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.6039,42.42062,129.5705,42.40582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.70129,42.43832,129.3566,42.44665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.3566,42.44665,129.70129,42.43832), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7291,42.47803,129.3566,42.44665), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.5291,42.52304,130.4649,42.54694), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.4649,42.54694,130.5291,42.52304), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.4261,42.57443,130.4649,42.54694), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.51469,42.60221,130.4816,42.60721), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.4816,42.60721,130.51469,42.60221), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.74271,42.64082,129.7747,42.64693), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7747,42.64693,129.74271,42.64082), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.7511,42.7047,130.2502,42.70998), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.2502,42.70998,129.7511,42.7047), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.25909,42.88416,130.1008,42.91193), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.1008,42.91193,130.25909,42.88416), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.0419,42.95776,130.1358,42.9622), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.1358,42.9622,129.86189,42.96582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((129.86189,42.96582,130.1358,42.9622), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")
	render_tiles((130.0994,42.98054,129.86189,42.96582), mapfile, tile_dir, 0, 11, "kp-korea,-peoples-republic-of")