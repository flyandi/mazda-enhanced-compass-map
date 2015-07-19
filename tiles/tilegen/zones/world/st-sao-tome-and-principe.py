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
    # Region: ST
    # Region Name: Sao Tome and Principe

	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.52944,0.02,6.52944,0.305), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.60083,0.07,6.52944,0.40361), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.60083,0.07,6.52944,0.40361), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.60083,0.07,6.52944,0.40361), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.60083,0.07,6.52944,0.40361), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.70917,0.18361,6.52944,0.40417), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.70917,0.18361,6.52944,0.40417), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.70917,0.18361,6.52944,0.40417), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.70917,0.18361,6.52944,0.40417), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.465,0.22194,6.52944,0.305), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.465,0.22194,6.52944,0.305), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.465,0.22194,6.52944,0.305), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.465,0.22194,6.52944,0.305), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.76444,0.29528,6.68305,0.18361), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.76444,0.29528,6.68305,0.18361), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.76444,0.29528,6.68305,0.18361), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.76444,0.29528,6.68305,0.18361), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.49055,0.305,6.52944,0.22194), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.49055,0.305,6.52944,0.22194), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.49055,0.305,6.52944,0.22194), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.49055,0.305,6.52944,0.22194), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.61778,0.40361,6.68305,0.07), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.61778,0.40361,6.68305,0.07), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.61778,0.40361,6.68305,0.07), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.61778,0.40361,6.68305,0.07), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((6.68305,0.40417,6.68305,0.18361), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((6.68305,0.40417,6.68305,0.18361), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((6.68305,0.40417,6.68305,0.18361), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((6.68305,0.40417,6.68305,0.18361), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3992,1.5306,7.3992,1.6964), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4044,1.5319,7.4067,1.5533), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4044,1.5319,7.4067,1.5533), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4044,1.5319,7.4067,1.5533), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4044,1.5319,7.4067,1.5533), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3969,1.5333,7.4067,1.5394), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3969,1.5333,7.4067,1.5394), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3969,1.5333,7.4067,1.5394), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3969,1.5333,7.4067,1.5394), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5356,7.3992,1.6978), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5356,7.3992,1.6978), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5356,7.3992,1.6978), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5356,7.3992,1.6978), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3814,1.5369,7.3992,1.6869), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3814,1.5369,7.3992,1.6869), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3814,1.5369,7.3992,1.6869), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3814,1.5369,7.3992,1.6869), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3853,1.5394,7.3992,1.6869), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3853,1.5394,7.3992,1.6869), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3853,1.5394,7.3992,1.6869), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3853,1.5394,7.3992,1.6869), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3964,1.5394,7.4067,1.5333), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3964,1.5394,7.4067,1.5333), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3964,1.5394,7.4067,1.5333), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3964,1.5394,7.4067,1.5333), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4053,1.5394,7.4067,1.5319), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4053,1.5394,7.4067,1.5319), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4053,1.5394,7.4067,1.5319), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4053,1.5394,7.4067,1.5319), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.5408,7.4067,1.5464), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.5408,7.4067,1.5464), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.5408,7.4067,1.5464), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.5408,7.4067,1.5464), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3889,1.5431,7.3992,1.6842), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3889,1.5431,7.3992,1.6842), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3889,1.5431,7.3992,1.6842), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3889,1.5431,7.3992,1.6842), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3936,1.5436,7.3992,1.695), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3936,1.5436,7.3992,1.695), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3936,1.5436,7.3992,1.695), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3936,1.5436,7.3992,1.695), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4033,1.5444,7.4067,1.5533), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4033,1.5444,7.4067,1.5533), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4033,1.5444,7.4067,1.5533), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4033,1.5444,7.4067,1.5533), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3772,1.5464,7.4067,1.5408), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3772,1.5464,7.4067,1.5408), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3772,1.5464,7.4067,1.5408), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3772,1.5464,7.4067,1.5408), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4011,1.5492,7.3992,1.6964), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4011,1.5492,7.3992,1.6964), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4011,1.5492,7.3992,1.6964), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4011,1.5492,7.3992,1.6964), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3736,1.5497,7.3992,1.6875), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3736,1.5497,7.3992,1.6875), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3736,1.5497,7.3992,1.6875), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3736,1.5497,7.3992,1.6875), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.5511,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.5511,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.5511,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.5511,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3628,1.5525,7.4067,1.6108), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3628,1.5525,7.4067,1.6108), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3628,1.5525,7.4067,1.6108), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3628,1.5525,7.4067,1.6108), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4039,1.5533,7.4067,1.5319), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4039,1.5533,7.4067,1.5319), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4039,1.5533,7.4067,1.5319), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4039,1.5533,7.4067,1.5319), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3586,1.5553,7.4067,1.6094), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3586,1.5553,7.4067,1.6094), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3586,1.5553,7.4067,1.6094), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3586,1.5553,7.4067,1.6094), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4231,1.5561,7.3992,1.6903), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4231,1.5561,7.3992,1.6903), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4231,1.5561,7.3992,1.6903), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4231,1.5561,7.3992,1.6903), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5561,7.3992,1.6978), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5561,7.3992,1.6978), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5561,7.3992,1.6978), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4081,1.5561,7.3992,1.6978), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3428,1.5561,7.4067,1.6128), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3428,1.5561,7.4067,1.6128), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3428,1.5561,7.4067,1.6128), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3428,1.5561,7.4067,1.6128), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5567,7.4067,1.6142), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5567,7.4067,1.6142), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5567,7.4067,1.6142), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5567,7.4067,1.6142), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4292,1.5567,7.4067,1.5628), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4292,1.5567,7.4067,1.5628), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4292,1.5567,7.4067,1.5628), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4292,1.5567,7.4067,1.5628), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4183,1.5581,7.4067,1.5561), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4183,1.5581,7.4067,1.5561), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4183,1.5581,7.4067,1.5561), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4183,1.5581,7.4067,1.5561), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.5586,7.3992,1.6978), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.5586,7.3992,1.6978), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.5586,7.3992,1.6978), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.5586,7.3992,1.6978), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3567,1.56,7.4067,1.6094), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3567,1.56,7.4067,1.6094), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3567,1.56,7.4067,1.6094), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3567,1.56,7.4067,1.6094), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.345,1.5608,7.4067,1.5561), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.345,1.5608,7.4067,1.5561), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.345,1.5608,7.4067,1.5561), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.345,1.5608,7.4067,1.5561), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5608,7.4067,1.5875), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5608,7.4067,1.5875), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5608,7.4067,1.5875), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5608,7.4067,1.5875), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5628,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5628,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5628,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5628,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.5636,7.4067,1.615), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.5636,7.4067,1.615), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.5636,7.4067,1.615), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.5636,7.4067,1.615), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3478,1.565,7.4067,1.615), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3478,1.565,7.4067,1.615), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3478,1.565,7.4067,1.615), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3478,1.565,7.4067,1.615), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5664,7.4067,1.5875), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5664,7.4067,1.5875), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5664,7.4067,1.5875), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3353,1.5664,7.4067,1.5875), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.5689,7.4067,1.6108), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.5689,7.4067,1.6108), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.5689,7.4067,1.6108), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.5689,7.4067,1.6108), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3367,1.5717,7.4067,1.5819), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3367,1.5717,7.4067,1.5819), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3367,1.5717,7.4067,1.5819), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3367,1.5717,7.4067,1.5819), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5753,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5753,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5753,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.5753,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5772,7.4067,1.6142), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5772,7.4067,1.6142), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5772,7.4067,1.6142), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3381,1.5772,7.4067,1.6142), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.43,1.5806,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.43,1.5806,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.43,1.5806,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.43,1.5806,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3361,1.5819,7.4067,1.5717), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3361,1.5819,7.4067,1.5717), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3361,1.5819,7.4067,1.5717), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3361,1.5819,7.4067,1.5717), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.5856,7.4067,1.5972), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.5856,7.4067,1.5972), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.5856,7.4067,1.5972), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.5856,7.4067,1.5972), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3347,1.5875,7.4067,1.5608), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3347,1.5875,7.4067,1.5608), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3347,1.5875,7.4067,1.5608), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3347,1.5875,7.4067,1.5608), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4328,1.5917,7.3992,1.6875), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4328,1.5917,7.3992,1.6875), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4328,1.5917,7.3992,1.6875), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4328,1.5917,7.3992,1.6875), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3339,1.5936,7.4067,1.615), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3339,1.5936,7.4067,1.615), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3339,1.5936,7.4067,1.615), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3339,1.5936,7.4067,1.615), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.5972,7.4067,1.5856), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.5972,7.4067,1.5856), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.5972,7.4067,1.5856), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.5972,7.4067,1.5856), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3319,1.5983,7.4067,1.615), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3319,1.5983,7.4067,1.615), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3319,1.5983,7.4067,1.615), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3319,1.5983,7.4067,1.615), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6011,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6011,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6011,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6011,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3292,1.6025,7.4067,1.6128), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3292,1.6025,7.4067,1.6128), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3292,1.6025,7.4067,1.6128), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3292,1.6025,7.4067,1.6128), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4264,1.6061,7.4067,1.6108), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4264,1.6061,7.4067,1.6108), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4264,1.6061,7.4067,1.6108), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4264,1.6061,7.4067,1.6108), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3272,1.6072,7.4067,1.6128), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3272,1.6072,7.4067,1.6128), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3272,1.6072,7.4067,1.6128), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3272,1.6072,7.4067,1.6128), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3581,1.6094,7.4067,1.5553), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3581,1.6094,7.4067,1.5553), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3581,1.6094,7.4067,1.5553), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3581,1.6094,7.4067,1.5553), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4272,1.6108,7.4067,1.5689), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4272,1.6108,7.4067,1.5689), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4272,1.6108,7.4067,1.5689), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4272,1.6108,7.4067,1.5689), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3622,1.6108,7.4067,1.5525), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3622,1.6108,7.4067,1.5525), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3622,1.6108,7.4067,1.5525), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3622,1.6108,7.4067,1.5525), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.6114,7.4067,1.615), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.6114,7.4067,1.615), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.6114,7.4067,1.615), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3531,1.6114,7.4067,1.615), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3286,1.6128,7.4067,1.6025), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3286,1.6128,7.4067,1.6025), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3286,1.6128,7.4067,1.6025), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3286,1.6128,7.4067,1.6025), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3422,1.6128,7.4067,1.5561), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3422,1.6128,7.4067,1.5561), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3422,1.6128,7.4067,1.5561), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3422,1.6128,7.4067,1.5561), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.6136,7.4067,1.5856), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.6136,7.4067,1.5856), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.6136,7.4067,1.5856), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4314,1.6136,7.4067,1.5856), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3394,1.6142,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3394,1.6142,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3394,1.6142,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3394,1.6142,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3497,1.615,7.4067,1.565), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3497,1.615,7.4067,1.565), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3497,1.615,7.4067,1.565), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3497,1.615,7.4067,1.565), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.615,7.3992,1.6319), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.615,7.3992,1.6319), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.615,7.3992,1.6319), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.615,7.3992,1.6319), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3333,1.615,7.4067,1.5936), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3333,1.615,7.4067,1.5936), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3333,1.615,7.4067,1.5936), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3333,1.615,7.4067,1.5936), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.345,1.6169,7.4067,1.5561), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.345,1.6169,7.4067,1.5561), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.345,1.6169,7.4067,1.5561), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.345,1.6169,7.4067,1.5561), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6169,7.3992,1.6567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6169,7.3992,1.6567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6169,7.3992,1.6567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6169,7.3992,1.6567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4389,1.6197,7.3992,1.6869), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4389,1.6197,7.3992,1.6869), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4389,1.6197,7.3992,1.6869), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4389,1.6197,7.3992,1.6869), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6197,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6197,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6197,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6197,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4483,1.6211,7.3992,1.665), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4483,1.6211,7.3992,1.665), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4483,1.6211,7.3992,1.665), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4483,1.6211,7.3992,1.665), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6225,7.3992,1.645), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6225,7.3992,1.645), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6225,7.3992,1.645), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6225,7.3992,1.645), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6231,7.3992,1.6356), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6231,7.3992,1.6356), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6231,7.3992,1.6356), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6231,7.3992,1.6356), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6267,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6267,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6267,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6267,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6272,7.3992,1.6381), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6272,7.3992,1.6381), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6272,7.3992,1.6381), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6272,7.3992,1.6381), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6314,7.3992,1.6828), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6314,7.3992,1.6828), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6314,7.3992,1.6828), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6314,7.3992,1.6828), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6319,7.3992,1.6628), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6319,7.3992,1.6628), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6319,7.3992,1.6628), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6319,7.3992,1.6628), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6347,7.3992,1.6828), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6347,7.3992,1.6828), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6347,7.3992,1.6828), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6347,7.3992,1.6828), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6356,7.3992,1.6231), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6356,7.3992,1.6231), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6356,7.3992,1.6231), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6356,7.3992,1.6231), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6375,7.3992,1.6628), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6375,7.3992,1.6628), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6375,7.3992,1.6628), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3656,1.6375,7.3992,1.6628), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4567,1.6381,7.3992,1.6272), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4567,1.6381,7.3992,1.6272), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4567,1.6381,7.3992,1.6272), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4567,1.6381,7.3992,1.6272), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6389,7.3992,1.6828), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6389,7.3992,1.6828), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6389,7.3992,1.6828), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4628,1.6389,7.3992,1.6828), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6394,7.3992,1.6231), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6394,7.3992,1.6231), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6394,7.3992,1.6231), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6394,7.3992,1.6231), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6431,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6431,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6431,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3669,1.6431,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4497,1.6436,7.3992,1.6211), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4497,1.6436,7.3992,1.6211), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4497,1.6436,7.3992,1.6211), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4497,1.6436,7.3992,1.6211), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4444,1.645,7.3992,1.6225), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4444,1.645,7.3992,1.6225), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4444,1.645,7.3992,1.6225), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4444,1.645,7.3992,1.6225), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3703,1.6464,7.3992,1.6772), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3703,1.6464,7.3992,1.6772), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3703,1.6464,7.3992,1.6772), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3703,1.6464,7.3992,1.6772), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.43,1.6472,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.43,1.6472,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.43,1.6472,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.43,1.6472,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4403,1.6478,7.3992,1.665), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4403,1.6478,7.3992,1.665), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4403,1.6478,7.3992,1.665), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4403,1.6478,7.3992,1.665), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6478,7.3992,1.6567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6478,7.3992,1.6567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6478,7.3992,1.6567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4347,1.6478,7.3992,1.6567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.6506,7.4067,1.6108), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.6506,7.4067,1.6108), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.6506,7.4067,1.6108), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4278,1.6506,7.4067,1.6108), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3725,1.6511,7.3992,1.6875), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3725,1.6511,7.3992,1.6875), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3725,1.6511,7.3992,1.6875), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3725,1.6511,7.3992,1.6875), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.6533,7.4067,1.5972), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.6533,7.4067,1.5972), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.6533,7.4067,1.5972), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4319,1.6533,7.4067,1.5972), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6539,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6539,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6539,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6539,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4353,1.6567,7.3992,1.6169), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4353,1.6567,7.3992,1.6169), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4353,1.6567,7.3992,1.6169), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4353,1.6567,7.3992,1.6169), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.6575,7.3992,1.6319), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.6575,7.3992,1.6319), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.6575,7.3992,1.6319), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3647,1.6575,7.3992,1.6319), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4367,1.6622,7.3992,1.6567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4367,1.6622,7.3992,1.6567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4367,1.6622,7.3992,1.6567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4367,1.6622,7.3992,1.6567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3661,1.6628,7.3992,1.6319), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3661,1.6628,7.3992,1.6319), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3661,1.6628,7.3992,1.6319), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3661,1.6628,7.3992,1.6319), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4408,1.665,7.3992,1.6478), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4408,1.665,7.3992,1.6478), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4408,1.665,7.3992,1.6478), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4408,1.665,7.3992,1.6478), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4478,1.665,7.3992,1.6211), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4478,1.665,7.3992,1.6211), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4478,1.665,7.3992,1.6211), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4478,1.665,7.3992,1.6211), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6664,7.3992,1.6356), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6664,7.3992,1.6356), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6664,7.3992,1.6356), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6664,7.3992,1.6356), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6675,7.3992,1.6739), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6675,7.3992,1.6739), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6675,7.3992,1.6739), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3683,1.6675,7.3992,1.6739), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6711,7.3992,1.6828), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6711,7.3992,1.6828), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6711,7.3992,1.6828), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4594,1.6711,7.3992,1.6828), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6717,7.3992,1.6347), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6717,7.3992,1.6347), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6717,7.3992,1.6347), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6717,7.3992,1.6347), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6725,7.3992,1.6231), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6725,7.3992,1.6231), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6725,7.3992,1.6231), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4525,1.6725,7.3992,1.6231), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3675,1.6739,7.3992,1.6197), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3675,1.6739,7.3992,1.6197), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3675,1.6739,7.3992,1.6197), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3675,1.6739,7.3992,1.6197), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6744,7.3992,1.6381), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6744,7.3992,1.6381), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6744,7.3992,1.6381), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6744,7.3992,1.6381), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6772,7.3992,1.6464), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6772,7.3992,1.6464), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6772,7.3992,1.6464), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6772,7.3992,1.6464), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6786,7.3992,1.6347), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6786,7.3992,1.6347), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6786,7.3992,1.6347), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4642,1.6786,7.3992,1.6347), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4614,1.6828,7.3992,1.6347), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4614,1.6828,7.3992,1.6347), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4614,1.6828,7.3992,1.6347), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4614,1.6828,7.3992,1.6347), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6828,7.3992,1.6464), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6828,7.3992,1.6464), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6828,7.3992,1.6464), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3711,1.6828,7.3992,1.6464), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3875,1.6842,7.4067,1.5431), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3875,1.6842,7.4067,1.5431), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3875,1.6842,7.4067,1.5431), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3875,1.6842,7.4067,1.5431), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6842,7.3992,1.6381), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6842,7.3992,1.6381), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6842,7.3992,1.6381), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4561,1.6842,7.3992,1.6381), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6847,7.4067,1.5436), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6847,7.4067,1.5436), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6847,7.4067,1.5436), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6847,7.4067,1.5436), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4394,1.6869,7.3992,1.6197), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4394,1.6869,7.3992,1.6197), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4394,1.6869,7.3992,1.6197), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4394,1.6869,7.3992,1.6197), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3833,1.6869,7.4067,1.5369), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3833,1.6869,7.4067,1.5369), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3833,1.6869,7.4067,1.5369), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3833,1.6869,7.4067,1.5369), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3731,1.6875,7.4067,1.5497), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3731,1.6875,7.4067,1.5497), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3731,1.6875,7.4067,1.5497), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3731,1.6875,7.4067,1.5497), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4333,1.6875,7.4067,1.5917), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4333,1.6875,7.4067,1.5917), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4333,1.6875,7.4067,1.5917), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4333,1.6875,7.4067,1.5917), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6881,7.3992,1.6356), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6881,7.3992,1.6356), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6881,7.3992,1.6356), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4533,1.6881,7.3992,1.6356), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.6889,7.4067,1.5464), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.6889,7.4067,1.5464), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.6889,7.4067,1.5464), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3786,1.6889,7.4067,1.5464), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6897,7.4067,1.5567), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6897,7.4067,1.5567), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6897,7.4067,1.5567), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4286,1.6897,7.4067,1.5567), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6903,7.3992,1.645), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6903,7.3992,1.645), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6903,7.3992,1.645), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4431,1.6903,7.3992,1.645), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6903,7.4067,1.5436), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6903,7.4067,1.5436), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6903,7.4067,1.5436), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3922,1.6903,7.4067,1.5436), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4239,1.6903,7.4067,1.5561), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4239,1.6903,7.4067,1.5561), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4239,1.6903,7.4067,1.5561), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4239,1.6903,7.4067,1.5561), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4511,1.6931,7.3992,1.6356), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4511,1.6931,7.3992,1.6356), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4511,1.6931,7.3992,1.6356), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4511,1.6931,7.3992,1.6356), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4469,1.6931,7.3992,1.665), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4469,1.6931,7.3992,1.665), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4469,1.6931,7.3992,1.665), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4469,1.6931,7.3992,1.665), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.6936,7.3992,1.6978), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.6936,7.3992,1.6978), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.6936,7.3992,1.6978), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4122,1.6936,7.3992,1.6978), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3942,1.695,7.4067,1.5436), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3942,1.695,7.4067,1.5436), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3942,1.695,7.4067,1.5436), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3942,1.695,7.4067,1.5436), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.3997,1.6964,7.4067,1.5306), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.3997,1.6964,7.4067,1.5306), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.3997,1.6964,7.4067,1.5306), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.3997,1.6964,7.4067,1.5306), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4094,1.6978,7.4067,1.5356), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4094,1.6978,7.4067,1.5356), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4094,1.6978,7.4067,1.5356), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4094,1.6978,7.4067,1.5356), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4025,1.7006,7.4067,1.5444), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4025,1.7006,7.4067,1.5444), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4025,1.7006,7.4067,1.5444), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4025,1.7006,7.4067,1.5444), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")
	render_tiles((7.4067,1.7019,7.4067,1.5394), mapfile, tile_dir, 0, 11, "st-sao-tome-and-principe")
	render_tiles((7.4067,1.7019,7.4067,1.5394), mapfile, tile_dir, 13, 13, "st-sao-tome-and-principe")
	render_tiles((7.4067,1.7019,7.4067,1.5394), mapfile, tile_dir, 15, 15, "st-sao-tome-and-principe")
	render_tiles((7.4067,1.7019,7.4067,1.5394), mapfile, tile_dir, 17, 17, "st-sao-tome-and-principe")