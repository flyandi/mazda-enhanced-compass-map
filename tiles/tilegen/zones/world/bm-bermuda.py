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
    # Region: BM
    # Region Name: Bermuda

	render_tiles((-64.8231,32.2606,-64.8164,32.2611), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8231,32.2606,-64.8164,32.2611), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8164,32.2611,-64.8383,32.2614), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8383,32.2614,-64.8097,32.2617), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8311,32.2614,-64.8097,32.2617), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8097,32.2617,-64.8383,32.2614), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8033,32.2625,-64.8097,32.2617), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8461,32.2625,-64.8097,32.2617), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7992,32.2639,-64.8522,32.2644), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8522,32.2644,-64.7992,32.2639), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7939,32.2661,-64.8583,32.2667), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8583,32.2667,-64.7939,32.2661), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7889,32.2683,-64.8267,32.2692), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8333,32.2683,-64.8267,32.2692), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8267,32.2692,-64.82,32.2697), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.82,32.2697,-64.8267,32.2692), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7833,32.2706,-64.8142,32.2711), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8394,32.2706,-64.8142,32.2711), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8578,32.2706,-64.8142,32.2711), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8142,32.2711,-64.7833,32.2706), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8519,32.2719,-64.8456,32.2725), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8456,32.2725,-64.8519,32.2719), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8075,32.2725,-64.8519,32.2719), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7792,32.2733,-64.8014,32.2739), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.8014,32.2739,-64.7792,32.2733), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7964,32.2758,-64.7753,32.2769), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7753,32.2769,-64.7906,32.2775), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7906,32.2775,-64.7753,32.2769), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7847,32.2786,-64.77,32.2792), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.77,32.2792,-64.7847,32.2786), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.78,32.2817,-64.7653,32.2819), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7653,32.2819,-64.78,32.2817), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7611,32.285,-64.7772,32.2861), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7772,32.2861,-64.7611,32.285), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7569,32.2886,-64.7806,32.29), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7806,32.29,-64.7569,32.2886), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7533,32.2922,-64.7847,32.2931), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7847,32.2931,-64.7533,32.2922), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7489,32.2953,-64.7883,32.2969), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7883,32.2969,-64.7489,32.2953), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.745,32.2989,-64.7831,32.2992), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7831,32.2992,-64.745,32.2989), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7778,32.3011,-64.7719,32.3025), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7719,32.3025,-64.7778,32.3011), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7411,32.3025,-64.7778,32.3011), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7669,32.3047,-64.7375,32.3061), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7375,32.3061,-64.7617,32.3069), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7617,32.3069,-64.7375,32.3061), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7564,32.3092,-64.7336,32.31), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7336,32.31,-64.7564,32.3092), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7511,32.3111,-64.7478,32.3122), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7478,32.3122,-64.7289,32.3128), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7289,32.3128,-64.7478,32.3122), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7456,32.3153,-64.7253,32.3164), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7253,32.3164,-64.7456,32.3153), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7419,32.3189,-64.7214,32.3203), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7214,32.3203,-64.7419,32.3189), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7164,32.3222,-64.7381,32.3228), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7381,32.3228,-64.7164,32.3222), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7117,32.3253,-64.7342,32.3264), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7342,32.3264,-64.7117,32.3253), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7067,32.3275,-64.7342,32.3264), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7006,32.3286,-64.7067,32.3275), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7306,32.33,-64.7006,32.3286), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6947,32.33,-64.7006,32.3286), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7267,32.3336,-64.6925,32.3353), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6925,32.3353,-64.7222,32.3367), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7222,32.3367,-64.6925,32.3353), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6947,32.3397,-64.7183,32.3403), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7183,32.3403,-64.6947,32.3397), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6986,32.3436,-64.715,32.3447), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.715,32.3447,-64.6986,32.3436), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7,32.3483,-64.7122,32.3492), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7122,32.3492,-64.7,32.3483), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7089,32.3536,-64.6983,32.3544), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6983,32.3544,-64.7089,32.3536), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7058,32.3581,-64.6958,32.3597), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6958,32.3597,-64.7058,32.3581), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.7025,32.3625,-64.6919,32.3633), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6919,32.3633,-64.7025,32.3625), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6883,32.3669,-64.6836,32.37), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6994,32.3669,-64.6836,32.37), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6836,32.37,-64.6964,32.3714), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6964,32.3714,-64.6792,32.3728), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6792,32.3728,-64.6964,32.3714), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6742,32.375,-64.6792,32.3728), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6925,32.375,-64.6792,32.3728), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6881,32.3778,-64.6717,32.38), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6717,32.38,-64.6836,32.3808), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6836,32.3808,-64.6717,32.38), mapfile, tile_dir, 0, 11, "bm-bermuda")
	render_tiles((-64.6778,32.3822,-64.6836,32.3808), mapfile, tile_dir, 0, 11, "bm-bermuda")