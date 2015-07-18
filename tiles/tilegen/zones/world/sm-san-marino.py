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
    # Region: SM
    # Region Name: San Marino

	render_tiles((12.4647,43.8956,12.4592,43.8961), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4592,43.8961,12.4647,43.8956), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4697,43.8961,12.4647,43.8956), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4744,43.8972,12.4542,43.8975), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4542,43.8975,12.4744,43.8972), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4778,43.8994,12.4542,43.8975), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4503,43.8994,12.4542,43.8975), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4811,43.9017,12.4475,43.9019), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4475,43.9019,12.4811,43.9017), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4844,43.9039,12.4453,43.9047), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4453,43.9047,12.4217,43.9053), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4164,43.9047,12.4217,43.9053), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4217,43.9053,12.4878,43.9058), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4878,43.9058,12.4122,43.9061), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4122,43.9061,12.4878,43.9058), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4256,43.9069,12.4428,43.9075), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4428,43.9075,12.4256,43.9069), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4303,43.9081,12.4906,43.9086), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4906,43.9086,12.4303,43.9081), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.41,43.9092,12.4392,43.9094), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4392,43.9094,12.41,43.9092), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4342,43.9097,12.4392,43.9094), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4928,43.9117,12.4083,43.9122), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4083,43.9122,12.4928,43.9117), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4077,43.91379,12.495,43.9147), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4077,43.91379,12.495,43.9147), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.495,43.9147,12.4077,43.91379), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4961,43.9189,12.4058,43.9194), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4058,43.9194,12.4961,43.9189), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4047,43.9231,12.4964,43.9233), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4964,43.9233,12.4047,43.9231), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4039,43.9267,12.4956,43.9272), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4956,43.9272,12.4039,43.9267), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4036,43.9308,12.4967,43.9311), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4967,43.9311,12.4036,43.9308), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4994,43.9339,12.4039,43.9356), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4039,43.9356,12.5072,43.9372), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5033,43.9356,12.5072,43.9372), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5072,43.9372,12.4039,43.9356), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5106,43.9392,12.405,43.9394), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.405,43.9394,12.5106,43.9392), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5117,43.9433,12.4047,43.9436), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4047,43.9436,12.5117,43.9433), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5108,43.9469,12.405,43.9483), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.405,43.9483,12.5108,43.9469), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5097,43.9508,12.4067,43.9519), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4067,43.9519,12.5097,43.9508), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5089,43.9544,12.4089,43.955), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4089,43.955,12.5089,43.9544), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4128,43.9567,12.5078,43.9581), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5078,43.9581,12.4167,43.9583), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4167,43.9583,12.5078,43.9581), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4214,43.9594,12.4167,43.9583), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4258,43.9606,12.4306,43.9617), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4306,43.9617,12.5069,43.9619), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5069,43.9619,12.4306,43.9617), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4344,43.9633,12.5069,43.9619), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4378,43.9656,12.5067,43.9661), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5067,43.9661,12.4378,43.9656), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4411,43.9678,12.5067,43.9661), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4439,43.9703,12.5072,43.9706), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5072,43.9706,12.4439,43.9703), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4456,43.9739,12.5075,43.9753), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5075,43.9753,12.4456,43.9739), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4478,43.9769,12.5075,43.9753), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5086,43.9792,12.4506,43.9797), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4506,43.9797,12.5086,43.9792), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4539,43.9817,12.5103,43.9828), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5103,43.9828,12.4581,43.9833), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4581,43.9833,12.5103,43.9828), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4625,43.9847,12.4669,43.9858), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4669,43.9858,12.4625,43.9847), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.51,43.9869,12.4767,43.9875), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4717,43.9869,12.4767,43.9875), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4767,43.9875,12.51,43.9869), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4819,43.9883,12.5056,43.9886), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.5056,43.9886,12.4819,43.9883), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4872,43.9889,12.4928,43.9892), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4992,43.9889,12.4928,43.9892), mapfile, tile_dir, 0, 11, "sm-san-marino")
	render_tiles((12.4928,43.9892,12.4872,43.9889), mapfile, tile_dir, 0, 11, "sm-san-marino")