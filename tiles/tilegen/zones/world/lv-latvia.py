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
    # Region: LV
    # Region Name: Latvia

	render_tiles((26.61409,55.67605,26.5108,55.6839), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.5108,55.6839,26.7453,55.6867), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.7453,55.6867,26.5108,55.6839), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.6617,55.7058,26.85,55.7108), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.85,55.7108,26.6617,55.7058), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.3386,55.7264,26.85,55.7108), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.2689,55.7694,26.9081,55.7786), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.9081,55.7786,27.2822,55.7872), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.2822,55.7872,27.6014,55.7919), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.6014,55.7919,27.2822,55.7872), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.4,55.8039,27.6014,55.7919), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.3508,55.8264,26.99,55.8347), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.99,55.8347,27.6328,55.8406), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.6328,55.8406,27.1561,55.8464), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.1561,55.8464,27.6328,55.8406), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.1981,55.8625,27.1561,55.8464), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.6472,55.9242,26.0028,55.9583), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.0028,55.9583,27.7911,55.9889), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7911,55.9889,26.0028,55.9583), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7956,56.0253,27.7911,55.9889), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8933,56.0656,21.04962,56.07677), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.04962,56.07677,27.8933,56.0656), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.2181,56.09,21.04962,56.07677), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.715,56.09,21.04962,56.07677), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.9294,56.1133,21.03917,56.12749), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.03917,56.12749,28.0583,56.1364), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.0583,56.1364,21.03917,56.12749), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.5939,56.1481,28.1665,56.15032), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1665,56.15032,25.685,56.1517), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.685,56.1517,28.1665,56.15032), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.4958,56.1567,25.685,56.1517), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1183,56.1672,21.2444,56.1683), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.2444,56.1683,25.3275,56.1692), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.3275,56.1692,21.2444,56.1683), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.5461,56.1725,25.3275,56.1692), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.195,56.1775,25.5461,56.1725), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((20.98694,56.19777,25.0969,56.2011), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.0969,56.2011,20.98694,56.19777), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.2028,56.2297,21.3544,56.24), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.3544,56.24,21.4278,56.2419), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.4278,56.2419,21.3544,56.24), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.1478,56.2614,24.4733,56.2692), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.4733,56.2692,25.0472,56.2708), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.0472,56.2708,28.2378,56.2711), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.2378,56.2711,25.0472,56.2708), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.5583,56.2883,21.5625,56.2933), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.5625,56.2933,25,56.2956), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25,56.2956,21.5625,56.2933), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.0961,56.3058,24.3397,56.3108), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.3397,56.3108,21.7094,56.3153), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.7094,56.3153,21.5911,56.3186), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.5911,56.3186,21.7094,56.3153), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.0375,56.3286,23.9489,56.3322), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.9489,56.3322,23.5253,56.3342), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.5253,56.3342,23.7464,56.3358), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.7464,56.3358,23.5253,56.3342), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.1803,56.3456,23.7464,56.3358), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.6914,56.3567,23.7411,56.36), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.7411,56.36,23.6519,56.3603), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.6519,56.3603,23.7411,56.36), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.1711,56.3619,23.5969,56.3622), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.5969,56.3622,23.1711,56.3619), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.6533,56.3658,23.5969,56.3622), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1844,56.3747,22.8272,56.3792), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.8272,56.3792,23.2961,56.3808), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.2961,56.3808,22.8272,56.3792), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.06028,56.38416,22.6233,56.3869), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.6233,56.3869,21.06028,56.38416), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.9928,56.3931,22.2489,56.3975), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.2489,56.3975,24.7267,56.3978), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.7267,56.3978,22.2489,56.3975), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.08167,56.39888,24.7267,56.3978), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.5192,56.4044,21.08167,56.39888), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.8644,56.4128,22.0672,56.4194), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.0672,56.4194,22.9414,56.4239), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.9414,56.4239,22.1544,56.4242), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.1544,56.4242,22.9414,56.4239), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.9222,56.44,28.1897,56.4406), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1897,56.4406,24.9222,56.44), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.8722,56.4436,28.1897,56.4406), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.8997,56.4506,24.8722,56.4436), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.00583,56.48277,28.1131,56.5056), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1131,56.5056,21.02861,56.50804), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.02861,56.50804,28.1131,56.5056), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.05778,56.51054,21.02861,56.50804), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((20.99139,56.53805,28.1525,56.5544), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.1525,56.5544,20.99139,56.53805), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.0328,56.5931,28.0108,56.6244), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.0108,56.6244,28.0328,56.5931), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((28.005,56.6914,27.8861,56.7472), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8861,56.7472,27.8861,56.7661), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8861,56.7661,27.8861,56.7472), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.9336,56.8011,27.9353,56.8272), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.9353,56.8272,21.06055,56.84165), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.06055,56.84165,27.64,56.8481), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.64,56.8481,21.06055,56.84165), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8606,56.8689,27.8072,56.8789), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8072,56.8789,27.8606,56.8689), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.6631,56.8947,27.8072,56.8789), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7017,56.9147,27.6631,56.8947), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.68361,56.96526,23.58055,56.97832), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.58055,56.97832,27.7439,56.9794), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7439,56.9794,23.58055,56.97832), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.35972,56.9886,23.89444,56.99554), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.89444,56.99554,21.35972,56.9886), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7422,57.0067,23.89444,56.99554), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7775,57.0658,21.41555,57.06944), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.41555,57.06944,27.7775,57.0658), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.25916,57.09805,27.7014,57.1192), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.7014,57.1192,23.25916,57.09805), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8408,57.1636,24.33361,57.19749), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.33361,57.19749,27.8408,57.1636), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.41306,57.27277,21.42056,57.28944), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.42056,57.28944,27.8628,57.2989), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.8628,57.2989,21.42056,57.28944), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((23.13722,57.36304,27.6606,57.3975), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.6606,57.3975,24.38778,57.4211), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.38778,57.4211,27.5383,57.4308), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.5383,57.4308,24.38778,57.4211), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.5256,57.4458,27.5383,57.4308), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.5458,57.4725,21.62472,57.47887), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.62472,57.47887,27.5458,57.4725), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.86028,57.48832,21.62472,57.47887), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.3383,57.5228,26.5172,57.5244), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.5172,57.5244,27.3383,57.5228), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.6089,57.5269,26.5172,57.5244), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.5611,57.5361,27.5472,57.5364), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.5472,57.5364,26.5611,57.5361), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.3711,57.5364,26.5611,57.5361), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.245,57.5492,26.635,57.5558), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.635,57.5558,27.0872,57.5622), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.0872,57.5622,26.635,57.5558), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.4603,57.5706,26.7608,57.5711), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.7608,57.5711,26.4603,57.5706), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.7194,57.5817,26.8292,57.5825), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.8292,57.5825,26.7194,57.5817), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.755,57.58332,26.8292,57.5825), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((21.95305,57.59193,21.755,57.58332), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.2992,57.6114,22.6125,57.6161), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((27.0186,57.6114,22.6125,57.6161), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.6125,57.6161,26.91,57.6192), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.91,57.6192,24.37638,57.62054), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.37638,57.62054,26.91,57.6192), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.8644,57.6258,24.37638,57.62054), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.8908,57.6339,26.8644,57.6258), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.58416,57.6761,26.8908,57.6339), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.1803,57.7219,24.29713,57.73642), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.29713,57.73642,22.49194,57.74221), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.29713,57.73642,22.49194,57.74221), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.49194,57.74221,24.29713,57.73642), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((22.61472,57.74999,22.49194,57.74221), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.0375,57.7847,26.0286,57.8022), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.0286,57.8022,26.0375,57.7847), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.0467,57.8403,26.0314,57.85), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((26.0314,57.85,24.29444,57.85027), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.29444,57.85027,26.0314,57.85), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.8014,57.8658,24.31005,57.87083), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.31005,57.87083,24.4203,57.8744), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.4203,57.8744,24.31005,57.87083), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.4531,57.9133,25.6228,57.9164), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.6228,57.9164,24.4531,57.9133), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.7492,57.9311,25.5772,57.9422), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.5772,57.9422,25.7492,57.9311), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.5536,57.9547,24.7178,57.9589), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.7178,57.9589,24.5536,57.9547), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.5764,57.9669,24.7178,57.9589), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.8275,57.98,25.2325,57.9928), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.2325,57.9928,25.4611,57.9944), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.4611,57.9944,25.2325,57.9928), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.7542,58.0008,25.4611,57.9944), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.8853,58.0075,24.7542,58.0008), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.2944,58.0075,24.7542,58.0008), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((24.9681,58.0144,24.8853,58.0075), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.2061,58.0317,25.4217,58.0356), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.4217,58.0356,25.3483,58.0367), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.3483,58.0367,25.4217,58.0356), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.2969,58.0381,25.3483,58.0367), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.2631,58.0692,25.0917,58.0717), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.0917,58.0717,25.2631,58.0692), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.17,58.0744,25.0917,58.0717), mapfile, tile_dir, 0, 11, "lv-latvia")
	render_tiles((25.3017,58.0831,25.17,58.0744), mapfile, tile_dir, 0, 11, "lv-latvia")