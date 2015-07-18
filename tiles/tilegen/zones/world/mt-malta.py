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
    # Region: MT
    # Region Name: Malta

	render_tiles((14.2675,36.0114,14.275,36.0117), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2675,36.0114,14.275,36.0117), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2514,36.0114,14.275,36.0117), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.275,36.0117,14.2431,36.0119), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2592,36.0117,14.2431,36.0119), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2431,36.0119,14.275,36.0117), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2372,36.0128,14.2811,36.0133), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2811,36.0133,14.2372,36.0128), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2308,36.0144,14.2869,36.0153), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2869,36.0153,14.2308,36.0144), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.225,36.0167,14.2869,36.0153), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2914,36.0183,14.2194,36.0189), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2194,36.0189,14.2914,36.0183), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2128,36.0206,14.2967,36.0208), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2967,36.0208,14.2128,36.0206), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2044,36.0211,14.2967,36.0208), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1969,36.0219,14.2044,36.0211), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3019,36.0233,14.1911,36.0244), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1911,36.0244,14.3239,36.025), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3239,36.025,14.1911,36.0244), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3078,36.025,14.1911,36.0244), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3156,36.0256,14.3239,36.025), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3358,36.0272,14.1881,36.0286), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1881,36.0286,14.3408,36.0297), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3408,36.0297,14.1881,36.0286), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1858,36.0333,14.3411,36.0339), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3411,36.0339,14.1858,36.0333), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3372,36.0372,14.1889,36.0381), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1889,36.0381,14.3372,36.0372), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3333,36.0408,14.1908,36.0433), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1908,36.0433,14.3286,36.0439), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3286,36.0439,14.1908,36.0433), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3244,36.0475,14.3286,36.0439), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1878,36.0475,14.3286,36.0439), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3206,36.0511,14.315,36.0533), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1839,36.0511,14.315,36.0533), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.315,36.0533,14.3075,36.0544), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3075,36.0544,14.2992,36.0547), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2992,36.0547,14.3075,36.0544), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1808,36.0553,14.2917,36.0558), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2917,36.0558,14.1808,36.0553), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2858,36.0581,14.2803,36.0603), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2803,36.0603,14.1811,36.0608), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1811,36.0608,14.2803,36.0603), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2744,36.0625,14.1856,36.0639), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1856,36.0639,14.2744,36.0625), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2697,36.0656,14.1908,36.0664), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1908,36.0664,14.2697,36.0656), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.1967,36.0683,14.2647,36.0686), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2647,36.0686,14.1967,36.0683), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2028,36.07,14.2592,36.0708), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2592,36.0708,14.2028,36.07), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2089,36.0717,14.2592,36.0708), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2156,36.0728,14.2533,36.0731), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2533,36.0731,14.2156,36.0728), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2222,36.0739,14.2458,36.0742), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2458,36.0742,14.2375,36.0744), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2375,36.0744,14.2458,36.0742), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.2292,36.075,14.2375,36.0744), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5197,35.8,14.5272,35.8003), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5197,35.8,14.5272,35.8003), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5272,35.8003,14.5197,35.8), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5131,35.8017,14.4997,35.8028), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4997,35.8028,14.5075,35.8033), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5075,35.8033,14.53,35.8036), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.53,35.8036,14.4922,35.8039), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4922,35.8039,14.53,35.8036), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4867,35.8061,14.5269,35.8078), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5269,35.8078,14.4867,35.8061), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.48,35.8078,14.4867,35.8061), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4753,35.8108,14.4686,35.8125), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4686,35.8125,14.4753,35.8108), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5247,35.8125,14.4753,35.8108), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4631,35.8147,14.4556,35.8158), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4556,35.8158,14.4472,35.8164), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4472,35.8164,14.4556,35.8158), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4397,35.8172,14.4314,35.8178), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4314,35.8178,14.4397,35.8172), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5244,35.8186,14.4314,35.8178), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4239,35.8186,14.4314,35.8178), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5372,35.82,14.5622,35.8203), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5622,35.8203,14.5372,35.82), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5297,35.8211,14.4192,35.8217), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5439,35.8211,14.4192,35.8217), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4192,35.8217,14.5297,35.8211), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5583,35.8239,14.5658,35.8242), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5658,35.8242,14.5583,35.8239), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5475,35.825,14.4153,35.8253), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4153,35.8253,14.5475,35.825), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5553,35.8281,14.4111,35.8289), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4111,35.8289,14.5681,35.8294), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5681,35.8294,14.5503,35.8297), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5503,35.8297,14.5681,35.8294), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4064,35.8317,14.5503,35.8297), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4017,35.8347,14.5675,35.8356), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5675,35.8356,14.4017,35.8347), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3967,35.8378,14.5675,35.8356), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3911,35.84,14.5672,35.8417), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5672,35.8417,14.3853,35.8422), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3853,35.8422,14.5672,35.8417), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3797,35.8444,14.3853,35.8422), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5658,35.8472,14.375,35.8475), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.375,35.8475,14.5658,35.8472), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3708,35.8511,14.5656,35.8533), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5656,35.8533,14.3669,35.8547), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3669,35.8547,14.5656,35.8533), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3647,35.8594,14.3617,35.8639), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5667,35.8594,14.3617,35.8639), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3617,35.8639,14.5697,35.8642), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5697,35.8642,14.3617,35.8639), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3569,35.8667,14.5697,35.8642), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.57,35.8694,14.3522,35.8697), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3522,35.8697,14.57,35.8694), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3472,35.8725,14.5669,35.8736), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5669,35.8736,14.3472,35.8725), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3433,35.8761,14.5639,35.8778), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5639,35.8778,14.3433,35.8761), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3394,35.8797,14.56,35.8814), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.56,35.8814,14.3394,35.8797), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3364,35.8839,14.5561,35.885), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5561,35.885,14.3364,35.8839), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5514,35.8881,14.3361,35.89), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3361,35.89,14.5514,35.8881), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5483,35.8922,14.3361,35.89), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3331,35.895,14.5292,35.8953), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5292,35.8953,14.3331,35.895), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5433,35.8953,14.3331,35.895), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5225,35.8956,14.5292,35.8953), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5367,35.8956,14.5292,35.8953), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.515,35.8967,14.5225,35.8956), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5111,35.9003,14.3342,35.9011), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3342,35.9011,14.5111,35.9003), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3381,35.9056,14.3342,35.9011), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5097,35.9056,14.3342,35.9011), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3408,35.9103,14.5111,35.9117), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5111,35.9117,14.3408,35.9103), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3436,35.9147,14.5131,35.9169), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5131,35.9169,14.3436,35.9147), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5133,35.9211,14.3442,35.9217), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3442,35.9217,14.5133,35.9211), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5103,35.9253,14.5031,35.9264), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.5031,35.9264,14.3428,35.9272), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3428,35.9272,14.4956,35.9275), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4956,35.9275,14.3428,35.9272), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4889,35.9292,14.4956,35.9275), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3406,35.9319,14.4858,35.9333), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4858,35.9333,14.3406,35.9319), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3367,35.9361,14.4819,35.9369), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4819,35.9369,14.3367,35.9361), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4761,35.9392,14.4678,35.9394), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4678,35.9394,14.4761,35.9392), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4611,35.9411,14.3356,35.9417), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3356,35.9417,14.4611,35.9411), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4564,35.9442,14.3356,35.9417), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4517,35.9472,14.335,35.9478), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.335,35.9478,14.4517,35.9472), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4486,35.9514,14.3347,35.9539), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3347,35.9539,14.4464,35.9561), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4464,35.9561,14.4128,35.9564), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4128,35.9564,14.4061,35.9567), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4061,35.9567,14.4128,35.9564), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3325,35.9589,14.4014,35.9597), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4181,35.9589,14.4014,35.9597), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4014,35.9597,14.4433,35.9603), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4433,35.9603,14.4242,35.9606), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4242,35.9606,14.4433,35.9603), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.43,35.9625,14.4375,35.9628), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.4375,35.9628,14.3294,35.9631), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3294,35.9631,14.3975,35.9633), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3975,35.9633,14.3294,35.9631), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3925,35.9661,14.3717,35.9664), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3717,35.9664,14.3925,35.9661), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3633,35.9669,14.3264,35.9672), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3264,35.9672,14.3633,35.9669), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3783,35.9675,14.3558,35.9678), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3558,35.9678,14.3783,35.9675), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3861,35.9678,14.3783,35.9675), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3519,35.9714,14.325,35.9728), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.325,35.9728,14.3519,35.9714), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3539,35.9769,14.3264,35.9789), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3264,35.9789,14.3592,35.9792), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3592,35.9792,14.3264,35.9789), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3653,35.9811,14.3392,35.9822), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3325,35.9811,14.3392,35.9822), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3392,35.9822,14.3711,35.9828), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3711,35.9828,14.3469,35.9833), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3469,35.9833,14.3711,35.9828), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3519,35.9858,14.3756,35.9861), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3756,35.9861,14.3519,35.9858), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3572,35.9881,14.3756,35.9861), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.375,35.9908,14.3617,35.9914), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3617,35.9914,14.375,35.9908), mapfile, tile_dir, 0, 11, "mt-malta")
	render_tiles((14.3683,35.9925,14.3617,35.9914), mapfile, tile_dir, 0, 11, "mt-malta")