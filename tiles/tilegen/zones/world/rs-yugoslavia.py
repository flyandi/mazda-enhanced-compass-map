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
    # Region: RS
    # Region Name: Yugoslavia

	render_tiles((20.6317,41.8558,20.7025,41.8561), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.7025,41.8561,20.6317,41.8558), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.7331,41.8639,20.7025,41.8561), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.67,41.8719,20.7331,41.8639), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.58965,41.88455,20.67,41.8719), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.7772,41.9256,20.62083,41.94971), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.62083,41.94971,20.7772,41.9256), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.7592,41.9933,20.62083,41.94971), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.7928,42.0825,21.3131,42.0936), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3131,42.0936,21.2483,42.0997), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.2483,42.0997,21.3131,42.0936), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3286,42.1081,21.2483,42.0997), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.2153,42.15,21.0336,42.1511), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.0336,42.1511,21.3061,42.1514), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3061,42.1514,21.0336,42.1511), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3481,42.1956,21.1419,42.1981), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.1419,42.1981,21.1047,42.1989), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.1047,42.1989,21.1419,42.1981), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.5261,42.21165,21.1047,42.1989), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.4394,42.2297,21.7383,42.2375), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.7383,42.2375,21.5572,42.2436), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.5572,42.2436,21.7383,42.2375), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.60978,42.25318,21.46,42.2625), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.46,42.2625,21.7886,42.2633), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.7886,42.2633,21.46,42.2625), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.51,42.2647,21.7886,42.2633), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.0833,42.3006,21.8044,42.3008), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.8044,42.3008,22.0833,42.3006), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.9264,42.3067,21.8044,42.3008), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.195,42.3153,22.1247,42.3197), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.1247,42.3197,22.36942,42.32293), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.36942,42.32293,20.35166,42.32443), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.35166,42.32443,21.9958,42.3256), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.25222,42.32443,21.9958,42.3256), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.9958,42.3256,20.35166,42.32443), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.8503,42.3308,21.9958,42.3256), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.2464,42.3397,22.2219,42.3417), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.2219,42.3417,22.2464,42.3397), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.3244,42.3614,22.2219,42.3417), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.51944,42.39915,22.3244,42.3614), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.55805,42.47915,20.1686,42.50694), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.1686,42.50694,22.55805,42.47915), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.48499,42.55582,20.07272,42.55844), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.07272,42.55844,22.48499,42.55582), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.07272,42.55844,22.48499,42.55582), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.4386,42.57471,20.07272,42.55844), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.02896,42.72105,22.46194,42.79388), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.46194,42.79388,20.07548,42.81409), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.07548,42.81409,22.44083,42.8186), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.44083,42.8186,20.07548,42.81409), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.23055,42.8451,19.98244,42.86061), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.98244,42.86061,20.23055,42.8451), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.73333,42.88971,22.58583,42.89276), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.58583,42.89276,22.73333,42.88971), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.29258,42.93815,22.58583,42.89276), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.99333,43.14526,23.00694,43.19999), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((23.00694,43.19999,22.99333,43.14526), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.84972,43.2822,23.00694,43.19999), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.76971,43.3836,22.54639,43.47026), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.54639,43.47026,19.07,43.5086), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.07,43.5086,19.2292,43.5133), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2292,43.5133,19.07,43.5086), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1767,43.5294,19.2875,43.5442), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2875,43.5442,19.1767,43.5294), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5111,43.5733,19.4086,43.5844), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4086,43.5844,19.4917,43.5908), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4917,43.5908,19.3158,43.5933), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3158,43.5933,19.4917,43.5908), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3717,43.6164,22.4886,43.62331), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.4886,43.62331,19.3717,43.6164), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5108,43.6797,22.4886,43.62331), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4875,43.7633,22.35944,43.81693), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.35944,43.81693,19.4875,43.7633), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5081,43.9581,19.2506,43.9622), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2506,43.9622,19.3736,43.9656), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3736,43.9656,19.2506,43.9622), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.41693,44.00694,19.2386,44.0136), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2386,44.0136,19.6192,44.0186), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.6192,44.0186,19.2386,44.0136), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5678,44.0514,19.6197,44.0519), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.6197,44.0519,19.5678,44.0514), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5958,44.0678,19.5583,44.0703), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5583,44.0703,22.6225,44.07082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.6225,44.07082,19.5583,44.0703), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.5039,44.0894,22.6225,44.07082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4697,44.1281,19.4425,44.1422), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4425,44.1422,19.4767,44.1519), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4767,44.1519,19.4425,44.1422), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.61666,44.17276,19.3583,44.1844), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3583,44.1844,22.61666,44.17276), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.69193,44.24306,19.2394,44.2647), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2394,44.2647,19.3189,44.2725), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3189,44.2725,19.2394,44.2647), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1653,44.2844,19.2019,44.2925), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2019,44.2925,19.1653,44.2844), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.53694,44.33637,19.1042,44.3558), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1042,44.3558,22.53694,44.33637), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.11,44.3992,19.1389,44.4139), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1389,44.4139,19.11,44.3992), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1467,44.4383,19.1389,44.4139), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.14638,44.47915,22.45999,44.4822), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.45999,44.4822,22.14638,44.47915), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.08444,44.50304,19.1253,44.5183), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1253,44.5183,22.69944,44.52248), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.69944,44.52248,22.20666,44.52499), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.20666,44.52499,22.69944,44.52248), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.76361,44.54749,19.1908,44.5483), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1908,44.5483,22.76361,44.54749), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.60944,44.55221,19.1908,44.5483), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.7536,44.56915,19.1861,44.5764), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1861,44.5764,22.7536,44.56915), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2172,44.59,19.1861,44.5764), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.98888,44.63693,22.31361,44.66415), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.31361,44.66415,21.62277,44.67221), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.62277,44.67221,19.2703,44.68), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2703,44.68,21.62277,44.67221), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3136,44.7044,22.43638,44.71443), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((22.43638,44.71443,19.3136,44.7044), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.60027,44.7536,19.3458,44.7692), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3458,44.7692,21.56555,44.77165), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.56555,44.77165,19.3458,44.7692), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3353,44.7797,21.40027,44.78082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.40027,44.78082,19.3353,44.7797), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.36098,44.82261,19.3608,44.8314), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3608,44.8314,21.36098,44.82261), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3553,44.8528,19.0381,44.8606), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0381,44.8606,19.3822,44.8628), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3822,44.8628,21.3661,44.86443), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3661,44.86443,19.3822,44.8628), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.55277,44.89082,19.35459,44.8935), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.35459,44.8935,21.55277,44.89082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0047,44.9003,19.2086,44.9014), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2086,44.9014,19.0047,44.9003), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0761,44.9094,19.2503,44.9158), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2503,44.9158,19.0136,44.9167), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0136,44.9167,19.2503,44.9158), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1767,44.9233,21.55139,44.92804), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.55139,44.92804,19.0367,44.9325), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0367,44.9325,21.55139,44.92804), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1453,44.9511,19.0367,44.9325), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1428,44.9822,19.1053,44.9839), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1053,44.9839,19.1428,44.9822), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.3774,44.99497,19.1053,44.9839), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1153,45.0361,21.3774,44.99497), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1042,45.0967,21.51277,45.12331), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.51277,45.12331,19.0817,45.1261), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0817,45.1261,21.51277,45.12331), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1381,45.1292,19.0817,45.1261), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1606,45.1458,19.1381,45.1292), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1808,45.1736,19.4111,45.175), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4111,45.175,19.3286,45.1758), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3286,45.1758,19.4111,45.175), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2617,45.1803,21.48527,45.18082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.48527,45.18082,19.2617,45.1803), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4239,45.1844,21.48527,45.18082), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.3189,45.1997,19.2986,45.2036), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2986,45.2036,19.3189,45.1997), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2231,45.2086,19.1636,45.2122), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1636,45.2122,19.2231,45.2086), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.4178,45.2356,19.2603,45.2472), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2603,45.2472,19.4178,45.2356), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.195,45.2686,19.2539,45.2739), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.2539,45.2739,19.195,45.2686), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1289,45.2906,19.2539,45.2739), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1008,45.3081,21.09499,45.30832), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((21.09499,45.30832,19.1008,45.3081), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.98666,45.34582,19.0853,45.3478), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0853,45.3478,19.0386,45.3486), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0386,45.3486,19.0853,45.3478), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9825,45.3758,18.9814,45.3964), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9814,45.3964,19.0358,45.4103), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0358,45.4103,18.9814,45.3964), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9944,45.4517,20.78305,45.48471), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.78305,45.48471,19.0831,45.4878), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0831,45.4878,18.9997,45.4908), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9997,45.4908,19.0831,45.4878), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.1006,45.5122,18.9997,45.4908), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.82388,45.53721,18.9483,45.5378), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9483,45.5378,20.82388,45.53721), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0344,45.5422,18.9483,45.5378), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.0244,45.5608,18.9019,45.5703), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9019,45.5703,19.0244,45.5608), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.76722,45.60582,18.9081,45.6167), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9081,45.6167,20.76722,45.60582), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9689,45.6672,18.9092,45.7133), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9092,45.7133,18.9561,45.7361), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9561,45.7361,20.72166,45.74054), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.72166,45.74054,20.80472,45.74415), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.80472,45.74415,20.72166,45.74054), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.79583,45.76915,18.9586,45.7781), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9586,45.7781,18.9333,45.7825), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9333,45.7825,18.9586,45.7781), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.9244,45.8119,18.9333,45.7825), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.8592,45.8472,18.9244,45.8119), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.59166,45.89415,18.8281,45.89668), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((18.8281,45.89668,20.59166,45.89415), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.03819,45.9676,20.37805,45.97803), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.37805,45.97803,19.15527,45.98721), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.15527,45.98721,19.24971,45.99332), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.24971,45.99332,19.15527,45.98721), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.08389,46.01943,19.12388,46.0236), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.12388,46.0236,19.08389,46.01943), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.26114,46.11533,19.80722,46.12804), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.80722,46.12804,20.26114,46.11533), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((20.11832,46.16693,19.57055,46.1736), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.57055,46.1736,19.71082,46.17499), mapfile, tile_dir, 0, 11, "rs-yugoslavia")
	render_tiles((19.71082,46.17499,19.57055,46.1736), mapfile, tile_dir, 0, 11, "rs-yugoslavia")