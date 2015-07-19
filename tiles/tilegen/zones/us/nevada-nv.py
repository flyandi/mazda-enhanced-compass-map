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
    # Zone: us
    # Region: Nevada
    # Region Name: NV

	render_tiles((-114.63349,35.00186,-117.1978,36.14101), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.63349,35.00186,-117.1978,36.14101), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.63349,35.00186,-117.1978,36.14101), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.63349,35.00186,-117.1978,36.14101), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.62507,35.06848,-117.1978,35.4095), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.62507,35.06848,-117.1978,35.4095), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.62507,35.06848,-117.1978,35.4095), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.62507,35.06848,-117.1978,35.4095), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.59912,35.12105,-114.63349,41.99451), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.59912,35.12105,-114.63349,41.99451), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.59912,35.12105,-114.63349,41.99451), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.59912,35.12105,-114.63349,41.99451), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.61991,35.12163,-117.1978,35.06848), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.61991,35.12163,-117.1978,35.06848), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.61991,35.12163,-117.1978,35.06848), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.61991,35.12163,-117.1978,35.06848), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.57275,35.13873,-117.1978,36.15161), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.57275,35.13873,-117.1978,36.15161), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.57275,35.13873,-117.1978,36.15161), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.57275,35.13873,-117.1978,36.15161), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.80425,35.13969,-117.1978,36.07054), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.80425,35.13969,-117.1978,36.07054), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.80425,35.13969,-117.1978,36.07054), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.80425,35.13969,-117.1978,36.07054), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.58713,35.26238,-114.63349,41.99451), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.58713,35.26238,-114.63349,41.99451), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.58713,35.26238,-114.63349,41.99451), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.58713,35.26238,-114.63349,41.99451), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.04381,35.33201,-114.63349,41.99863), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.04381,35.33201,-114.63349,41.99863), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.04381,35.33201,-114.63349,41.99863), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.04381,35.33201,-114.63349,41.99863), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.62714,35.4095,-117.1978,36.14101), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.62714,35.4095,-117.1978,36.14101), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.62714,35.4095,-117.1978,36.14101), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.62714,35.4095,-117.1978,36.14101), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.16007,35.42413,-117.1978,35.33201), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.16007,35.42413,-117.1978,35.33201), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.16007,35.42413,-117.1978,35.33201), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.16007,35.42413,-117.1978,35.33201), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.6645,35.4495,-117.1978,35.52449), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.6645,35.4495,-117.1978,35.52449), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.6645,35.4495,-117.1978,35.52449), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.6645,35.4495,-117.1978,35.52449), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.66311,35.52449,-117.1978,35.4495), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.66311,35.52449,-117.1978,35.4495), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.66311,35.52449,-117.1978,35.4495), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.66311,35.52449,-117.1978,35.4495), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.30374,35.53821,-114.63349,41.9961), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.30374,35.53821,-114.63349,41.9961), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.30374,35.53821,-114.63349,41.9961), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.30374,35.53821,-114.63349,41.9961), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.65341,35.61079,-117.1978,35.52449), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.65341,35.61079,-117.1978,35.52449), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.65341,35.61079,-117.1978,35.52449), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.65341,35.61079,-117.1978,35.52449), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.40454,35.61761,-114.63349,41.9961), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.40454,35.61761,-114.63349,41.9961), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.40454,35.61761,-114.63349,41.9961), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.40454,35.61761,-114.63349,41.9961), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.68941,35.65141,-117.1978,35.73369), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.68941,35.65141,-117.1978,35.73369), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.68941,35.65141,-117.1978,35.73369), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.68941,35.65141,-117.1978,35.73369), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.69731,35.73369,-117.1978,35.90177), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.69731,35.73369,-117.1978,35.90177), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.69731,35.73369,-117.1978,35.90177), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.69731,35.73369,-117.1978,35.90177), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.64768,35.80936,-117.1978,35.80963), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.64768,35.80936,-117.1978,35.80963), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.64768,35.80936,-117.1978,35.80963), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.64768,35.80936,-117.1978,35.80963), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.64803,35.80963,-117.1978,35.80936), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.64803,35.80963,-117.1978,35.80936), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.64803,35.80963,-117.1978,35.80936), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.64803,35.80963,-117.1978,35.80936), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.70371,35.81459,-117.1978,35.90177), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.70371,35.81459,-117.1978,35.90177), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.70371,35.81459,-117.1978,35.90177), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.70371,35.81459,-117.1978,35.90177), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.66969,35.86508,-117.1978,36.11734), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.66969,35.86508,-117.1978,36.11734), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.66969,35.86508,-117.1978,36.11734), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.66969,35.86508,-117.1978,36.11734), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.70027,35.90177,-117.1978,35.73369), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.70027,35.90177,-117.1978,35.73369), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.70027,35.90177,-117.1978,35.73369), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.70027,35.90177,-117.1978,35.73369), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.73116,35.94392,-117.1978,36.10437), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.73116,35.94392,-117.1978,36.10437), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.73116,35.94392,-117.1978,36.10437), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.73116,35.94392,-117.1978,36.10437), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.84611,35.96355,-114.63349,41.99677), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.84611,35.96355,-114.63349,41.99677), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.84611,35.96355,-114.63349,41.99677), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.84611,35.96355,-114.63349,41.99677), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.89298,35.99997,-114.63349,41.99677), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.89298,35.99997,-114.63349,41.99677), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.89298,35.99997,-114.63349,41.99677), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.89298,35.99997,-114.63349,41.99677), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.74278,36.00996,-117.1978,36.06594), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.74278,36.00996,-117.1978,36.06594), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.74278,36.00996,-117.1978,36.06594), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.74278,36.00996,-117.1978,36.06594), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.21369,36.01561,-117.1978,36.03572), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.21369,36.01561,-117.1978,36.03572), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.21369,36.01561,-117.1978,36.03572), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.21369,36.01561,-117.1978,36.03572), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.15173,36.02456,-117.1978,36.05316), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.15173,36.02456,-117.1978,36.05316), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.15173,36.02456,-117.1978,36.05316), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.15173,36.02456,-117.1978,36.05316), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.27065,36.03572,-114.63349,41.99421), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.27065,36.03572,-114.63349,41.99421), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.27065,36.03572,-114.63349,41.99421), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.27065,36.03572,-114.63349,41.99421), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.1382,36.05316,-117.1978,36.02456), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.1382,36.05316,-117.1978,36.02456), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.1382,36.05316,-117.1978,36.02456), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.1382,36.05316,-117.1978,36.02456), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.31611,36.06311,-117.1978,36.10802), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.31611,36.06311,-117.1978,36.10802), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.31611,36.06311,-117.1978,36.10802), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.31611,36.06311,-117.1978,36.10802), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.7433,36.06594,-117.1978,36.07054), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.7433,36.06594,-117.1978,36.07054), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.7433,36.06594,-117.1978,36.07054), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.7433,36.06594,-117.1978,36.07054), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.74334,36.07054,-117.1978,36.06594), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.74334,36.07054,-117.1978,36.06594), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.74334,36.07054,-117.1978,36.06594), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.74334,36.07054,-117.1978,36.06594), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.73617,36.10437,-117.1978,35.94392), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.73617,36.10437,-117.1978,35.94392), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.73617,36.10437,-117.1978,35.94392), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.73617,36.10437,-117.1978,35.94392), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.33727,36.10802,-117.1978,36.06311), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.33727,36.10802,-117.1978,36.06311), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.33727,36.10802,-117.1978,36.06311), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.33727,36.10802,-117.1978,36.06311), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.66654,36.11734,-117.1978,35.4495), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.66654,36.11734,-117.1978,35.4495), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.66654,36.11734,-117.1978,35.4495), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.66654,36.11734,-117.1978,35.4495), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.09987,36.12165,-117.1978,36.05316), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.09987,36.12165,-117.1978,36.05316), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.09987,36.12165,-117.1978,36.05316), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.09987,36.12165,-117.1978,36.05316), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.44865,36.12641,-117.1978,36.14576), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.44865,36.12641,-117.1978,36.14576), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.44865,36.12641,-117.1978,36.14576), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.44865,36.12641,-117.1978,36.14576), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.48703,36.1294,-117.1978,36.15096), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.48703,36.1294,-117.1978,36.15096), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.48703,36.1294,-117.1978,36.15096), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.48703,36.1294,-117.1978,36.15096), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.62786,36.14101,-117.1978,35.4095), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.62786,36.14101,-117.1978,35.4095), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.62786,36.14101,-117.1978,35.4095), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.62786,36.14101,-117.1978,35.4095), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.37211,36.14311,-117.1978,36.10802), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.37211,36.14311,-117.1978,36.10802), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.37211,36.14311,-117.1978,36.10802), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.37211,36.14311,-117.1978,36.10802), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.41695,36.14576,-117.1978,36.12641), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.41695,36.14576,-117.1978,36.12641), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.41695,36.14576,-117.1978,36.12641), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.41695,36.14576,-117.1978,36.12641), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.51172,36.15096,-117.1978,36.1294), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.51172,36.15096,-117.1978,36.1294), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.51172,36.15096,-117.1978,36.1294), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.51172,36.15096,-117.1978,36.1294), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.57203,36.15161,-117.1978,35.13873), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.57203,36.15161,-117.1978,35.13873), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.57203,36.15161,-117.1978,35.13873), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.57203,36.15161,-117.1978,35.13873), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-116.0936,36.15581,-117.1978,35.99997), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-116.0936,36.15581,-117.1978,35.99997), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-116.0936,36.15581,-117.1978,35.99997), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-116.0936,36.15581,-117.1978,35.99997), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04684,36.19407,-114.63349,39.49994), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04684,36.19407,-114.63349,39.49994), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04684,36.19407,-114.63349,39.49994), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04684,36.19407,-114.63349,39.49994), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04823,36.26887,-114.63349,38.87869), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04823,36.26887,-114.63349,38.87869), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04823,36.26887,-114.63349,38.87869), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04823,36.26887,-114.63349,38.87869), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04758,36.32557,-114.63349,39.79416), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04758,36.32557,-114.63349,39.79416), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04758,36.32557,-114.63349,39.79416), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04758,36.32557,-114.63349,39.79416), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-116.37588,36.37256,-114.63349,41.99728), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-116.37588,36.37256,-114.63349,41.99728), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-116.37588,36.37256,-114.63349,41.99728), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-116.37588,36.37256,-114.63349,41.99728), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-116.48823,36.4591,-117.1978,36.37256), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-116.48823,36.4591,-117.1978,36.37256), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-116.48823,36.4591,-117.1978,36.37256), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-116.48823,36.4591,-117.1978,36.37256), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04949,36.60406,-114.63349,38.67736), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04949,36.60406,-114.63349,38.67736), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04949,36.60406,-114.63349,38.67736), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04949,36.60406,-114.63349,38.67736), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05016,36.84314,-114.63349,38.57292), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05016,36.84314,-114.63349,38.57292), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05016,36.84314,-114.63349,38.57292), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05016,36.84314,-114.63349,38.57292), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.0009,36.84769,-114.63349,41.99984), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.0009,36.84769,-114.63349,41.99984), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.0009,36.84769,-114.63349,41.99984), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.0009,36.84769,-114.63349,41.99984), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.166,36.97121,-114.63349,42.00038), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.166,36.97121,-114.63349,42.00038), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.166,36.97121,-114.63349,42.00038), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.166,36.97121,-114.63349,42.00038), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.0506,37.0004,-117.1978,36.84314), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.0506,37.0004,-117.1978,36.84314), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.0506,37.0004,-117.1978,36.84314), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.0506,37.0004,-117.1978,36.84314), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.24492,37.03024,-114.63349,42.00038), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.24492,37.03024,-114.63349,42.00038), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.24492,37.03024,-114.63349,42.00038), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.24492,37.03024,-114.63349,42.00038), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05175,37.08843,-117.1978,37.746), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05175,37.08843,-117.1978,37.746), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05175,37.08843,-117.1978,37.746), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05175,37.08843,-117.1978,37.746), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05197,37.28451,-117.1978,37.08843), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05197,37.28451,-117.1978,37.08843), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05197,37.28451,-117.1978,37.08843), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05197,37.28451,-117.1978,37.08843), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.68061,37.3534,-114.63349,41.99847), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.68061,37.3534,-114.63349,41.99847), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.68061,37.3534,-114.63349,41.99847), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.68061,37.3534,-114.63349,41.99847), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.8335,37.46494,-114.63349,41.99834), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.8335,37.46494,-114.63349,41.99834), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.8335,37.46494,-114.63349,41.99834), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.8335,37.46494,-114.63349,41.99834), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.0527,37.49201,-117.1978,37.60478), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.0527,37.49201,-117.1978,37.60478), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.0527,37.49201,-117.1978,37.60478), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.0527,37.49201,-117.1978,37.60478), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.02218,37.60258,-114.63349,41.99834), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.02218,37.60258,-114.63349,41.99834), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.02218,37.60258,-114.63349,41.99834), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.02218,37.60258,-114.63349,41.99834), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05247,37.60478,-117.1978,37.49201), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05173,37.746,-117.1978,37.08843), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05173,37.746,-117.1978,37.08843), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05173,37.746,-117.1978,37.08843), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05173,37.746,-117.1978,37.08843), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04966,37.88137,-117.1978,36.60406), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04966,37.88137,-117.1978,36.60406), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04966,37.88137,-117.1978,36.60406), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04966,37.88137,-117.1978,36.60406), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.428,37.89622,-117.1978,37.94902), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.428,37.89622,-117.1978,37.94902), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.428,37.89622,-117.1978,37.94902), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.428,37.89622,-117.1978,37.94902), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.50096,37.94902,-114.63349,41.99545), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.50096,37.94902,-114.63349,41.99545), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.50096,37.94902,-114.63349,41.99545), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.50096,37.94902,-114.63349,41.99545), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.0499,38.1486,-117.1978,38.40454), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.0499,38.1486,-117.1978,38.40454), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.0499,38.1486,-117.1978,38.40454), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.0499,38.1486,-117.1978,38.40454), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05014,38.24996,-114.63349,38.57292), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05014,38.24996,-114.63349,38.57292), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05014,38.24996,-114.63349,38.57292), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05014,38.24996,-114.63349,38.57292), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.94967,38.26894,-114.63349,41.99379), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.94967,38.26894,-114.63349,41.99379), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.94967,38.26894,-114.63349,41.99379), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.94967,38.26894,-114.63349,41.99379), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05012,38.40454,-117.1978,38.24996), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05012,38.40454,-117.1978,38.24996), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05012,38.40454,-117.1978,38.24996), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05012,38.40454,-117.1978,38.24996), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.15723,38.41439,-114.63349,41.99318), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.15723,38.41439,-114.63349,41.99318), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.15723,38.41439,-114.63349,41.99318), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.15723,38.41439,-114.63349,41.99318), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.27926,38.49991,-114.63349,41.99388), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.27926,38.49991,-114.63349,41.99388), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.27926,38.49991,-114.63349,41.99388), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.27926,38.49991,-114.63349,41.99388), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.3287,38.53435,-114.63349,41.99388), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.3287,38.53435,-114.63349,41.99388), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.3287,38.53435,-114.63349,41.99388), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.3287,38.53435,-114.63349,41.99388), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.05015,38.57292,-117.1978,36.84314), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.05015,38.57292,-117.1978,36.84314), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.05015,38.57292,-117.1978,36.84314), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.05015,38.57292,-117.1978,36.84314), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04944,38.67736,-117.1978,36.60406), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04944,38.67736,-117.1978,36.60406), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04944,38.67736,-117.1978,36.60406), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04944,38.67736,-117.1978,36.60406), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.58541,38.71315,-114.63349,38.71473), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.58541,38.71315,-114.63349,38.71473), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.58541,38.71315,-114.63349,38.71473), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.58541,38.71315,-114.63349,38.71473), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.58768,38.71473,-114.63349,38.71315), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.58768,38.71473,-114.63349,38.71315), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.58768,38.71473,-114.63349,38.71315), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.58768,38.71473,-114.63349,38.71315), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04805,38.87869,-117.1978,36.26887), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04805,38.87869,-117.1978,36.26887), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04805,38.87869,-117.1978,36.26887), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04805,38.87869,-117.1978,36.26887), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.90432,38.93332,-114.63349,40.32125), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.90432,38.93332,-114.63349,40.32125), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.90432,38.93332,-114.63349,40.32125), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.90432,38.93332,-114.63349,40.32125), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00101,38.99957,-114.63349,39.53885), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00101,38.99957,-114.63349,39.53885), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00101,38.99957,-114.63349,39.53885), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00101,38.99957,-114.63349,39.53885), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.0491,39.00551,-114.63349,38.67736), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.0491,39.00551,-114.63349,38.67736), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.0491,39.00551,-114.63349,38.67736), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.0491,39.00551,-114.63349,38.67736), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00198,39.0675,-114.63349,39.53885), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00198,39.0675,-114.63349,39.53885), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00198,39.0675,-114.63349,39.53885), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00198,39.0675,-114.63349,39.53885), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00261,39.11269,-114.63349,39.44505), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00261,39.11269,-114.63349,39.44505), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00261,39.11269,-114.63349,39.44505), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00261,39.11269,-114.63349,39.44505), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00336,39.16563,-114.63349,39.44505), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00336,39.16563,-114.63349,39.44505), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00336,39.16563,-114.63349,39.44505), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00336,39.16563,-114.63349,39.44505), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00514,39.29126,-114.63349,39.31648), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00514,39.29126,-114.63349,39.31648), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00514,39.29126,-114.63349,39.31648), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00514,39.29126,-114.63349,39.31648), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.0048,39.31648,-114.63349,39.29126), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00303,39.44505,-114.63349,39.16563), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00303,39.44505,-114.63349,39.16563), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00303,39.44505,-114.63349,39.16563), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00303,39.44505,-114.63349,39.16563), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04708,39.49994,-114.63349,39.54274), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04708,39.49994,-114.63349,39.54274), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04708,39.49994,-114.63349,39.54274), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04708,39.49994,-114.63349,39.54274), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-120.00174,39.53885,-114.63349,39.0675), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-120.00174,39.53885,-114.63349,39.0675), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-120.00174,39.53885,-114.63349,39.0675), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-120.00174,39.53885,-114.63349,39.0675), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04718,39.54274,-114.63349,39.90604), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04718,39.54274,-114.63349,39.90604), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04718,39.54274,-114.63349,39.90604), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04718,39.54274,-114.63349,39.90604), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99994,39.72241,-114.63349,41.18397), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99994,39.72241,-114.63349,41.18397), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99994,39.72241,-114.63349,41.18397), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99994,39.72241,-114.63349,41.18397), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04778,39.79416,-117.1978,36.32557), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04778,39.79416,-117.1978,36.32557), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04778,39.79416,-117.1978,36.32557), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04778,39.79416,-117.1978,36.32557), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04727,39.90604,-114.63349,39.54274), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04727,39.90604,-114.63349,39.54274), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04727,39.90604,-114.63349,39.54274), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04727,39.90604,-114.63349,39.54274), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99763,39.95651,-114.63349,40.72099), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99763,39.95651,-114.63349,40.72099), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99763,39.95651,-114.63349,40.72099), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99763,39.95651,-114.63349,40.72099), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04639,40.0979,-114.63349,40.11693), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04639,40.0979,-114.63349,40.11693), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04639,40.0979,-114.63349,40.11693), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04639,40.0979,-114.63349,40.11693), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04637,40.11693,-114.63349,40.0979), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04637,40.11693,-114.63349,40.0979), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04637,40.11693,-114.63349,40.0979), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04637,40.11693,-114.63349,40.0979), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99712,40.12636,-114.63349,40.72099), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99712,40.12636,-114.63349,40.72099), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99712,40.12636,-114.63349,40.72099), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99712,40.12636,-114.63349,40.72099), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99616,40.32125,-114.63349,40.12636), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99616,40.32125,-114.63349,40.12636), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99616,40.32125,-114.63349,40.12636), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99616,40.32125,-114.63349,40.12636), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04618,40.39831,-114.63349,40.11693), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04618,40.39831,-114.63349,40.11693), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04618,40.39831,-114.63349,40.11693), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04618,40.39831,-114.63349,40.11693), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04558,40.4958,-114.63349,40.39831), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04558,40.4958,-114.63349,40.39831), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04558,40.4958,-114.63349,40.39831), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04558,40.4958,-114.63349,40.39831), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99753,40.72099,-114.63349,39.95651), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99753,40.72099,-114.63349,39.95651), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99753,40.72099,-114.63349,39.95651), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99753,40.72099,-114.63349,39.95651), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04351,40.72629,-114.63349,40.99993), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04351,40.72629,-114.63349,40.99993), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04351,40.72629,-114.63349,40.99993), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04351,40.72629,-114.63349,40.99993), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99923,40.8659,-114.63349,41.87489), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99923,40.8659,-114.63349,41.87489), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99923,40.8659,-114.63349,41.87489), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99923,40.8659,-114.63349,41.87489), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04215,40.99993,-114.63349,41.99372), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04215,40.99993,-114.63349,41.99372), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04215,40.99993,-114.63349,41.99372), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04215,40.99993,-114.63349,41.99372), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99987,41.18397,-114.63349,39.72241), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99987,41.18397,-114.63349,39.72241), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99987,41.18397,-114.63349,39.72241), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99987,41.18397,-114.63349,39.72241), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04145,41.20775,-114.63349,41.99372), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04145,41.20775,-114.63349,41.99372), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04145,41.20775,-114.63349,41.99372), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04145,41.20775,-114.63349,41.99372), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04023,41.49169,-114.63349,41.75378), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04023,41.49169,-114.63349,41.75378), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04023,41.49169,-114.63349,41.75378), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04023,41.49169,-114.63349,41.75378), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99828,41.61877,-114.63349,39.95651), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99828,41.61877,-114.63349,39.95651), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99828,41.61877,-114.63349,39.95651), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99828,41.61877,-114.63349,39.95651), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.0399,41.75378,-114.63349,41.49169), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.0399,41.75378,-114.63349,41.49169), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.0399,41.75378,-114.63349,41.49169), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.0399,41.75378,-114.63349,41.49169), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99928,41.87489,-114.63349,40.8659), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99928,41.87489,-114.63349,40.8659), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99928,41.87489,-114.63349,40.8659), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99928,41.87489,-114.63349,40.8659), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.77587,41.99269,-117.1978,38.26894), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.77587,41.99269,-117.1978,38.26894), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.77587,41.99269,-117.1978,38.26894), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.77587,41.99269,-117.1978,38.26894), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.20828,41.99318,-117.1978,38.41439), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.20828,41.99318,-117.1978,38.41439), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.20828,41.99318,-117.1978,38.41439), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.20828,41.99318,-117.1978,38.41439), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.04172,41.99372,-114.63349,41.20775), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.04172,41.99372,-114.63349,41.20775), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.04172,41.99372,-114.63349,41.20775), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.04172,41.99372,-114.63349,41.20775), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.00102,41.99379,-117.1978,38.26894), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.00102,41.99379,-117.1978,38.26894), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.00102,41.99379,-117.1978,38.26894), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.00102,41.99379,-117.1978,38.26894), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.32418,41.99388,-114.63349,38.53435), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.32418,41.99388,-114.63349,38.53435), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.32418,41.99388,-114.63349,38.53435), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.32418,41.99388,-114.63349,38.53435), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.36018,41.99409,-114.63349,38.53435), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.36018,41.99409,-114.63349,38.53435), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.36018,41.99409,-114.63349,38.53435), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.36018,41.99409,-114.63349,38.53435), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.28186,41.99421,-117.1978,36.03572), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.28186,41.99421,-117.1978,36.03572), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.28186,41.99421,-117.1978,36.03572), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.28186,41.99421,-117.1978,36.03572), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.59827,41.99451,-117.1978,35.12105), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.59827,41.99451,-117.1978,35.12105), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.59827,41.99451,-117.1978,35.12105), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.59827,41.99451,-117.1978,35.12105), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.99917,41.99454,-114.63349,40.8659), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.99917,41.99454,-114.63349,40.8659), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.99917,41.99454,-114.63349,40.8659), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.99917,41.99454,-114.63349,40.8659), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.501,41.99545,-117.1978,37.94902), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.501,41.99545,-117.1978,37.94902), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.501,41.99545,-117.1978,37.94902), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.501,41.99545,-117.1978,37.94902), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.31388,41.9961,-117.1978,35.53821), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.31388,41.9961,-117.1978,35.53821), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.31388,41.9961,-117.1978,35.53821), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.31388,41.9961,-117.1978,35.53821), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-119.72573,41.9963,-114.63349,38.71473), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-119.72573,41.9963,-114.63349,38.71473), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-119.72573,41.9963,-114.63349,38.71473), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-119.72573,41.9963,-114.63349,38.71473), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.87018,41.99677,-117.1978,35.99997), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.87018,41.99677,-117.1978,35.99997), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.87018,41.99677,-117.1978,35.99997), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.87018,41.99677,-117.1978,35.99997), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-118.19719,41.997,-117.1978,37.60258), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-118.19719,41.997,-117.1978,37.60258), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-118.19719,41.997,-117.1978,37.60258), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-118.19719,41.997,-117.1978,37.60258), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-116.33276,41.99728,-117.1978,36.37256), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-116.33276,41.99728,-117.1978,36.37256), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-116.33276,41.99728,-117.1978,36.37256), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-116.33276,41.99728,-117.1978,36.37256), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-116.62595,41.99738,-117.1978,36.4591), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-116.62595,41.99738,-117.1978,36.4591), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-116.62595,41.99738,-117.1978,36.4591), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-116.62595,41.99738,-117.1978,36.4591), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.62591,41.99742,-117.1978,35.80936), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.62591,41.99742,-117.1978,35.80936), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.62591,41.99742,-117.1978,35.80936), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.62591,41.99742,-117.1978,35.80936), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.87347,41.99834,-117.1978,37.46494), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.87347,41.99834,-117.1978,37.46494), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.87347,41.99834,-117.1978,37.46494), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.87347,41.99834,-117.1978,37.46494), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.62373,41.99847,-117.1978,37.3534), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.62373,41.99847,-117.1978,37.3534), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.62373,41.99847,-117.1978,37.3534), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.62373,41.99847,-117.1978,37.3534), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-115.03825,41.99863,-117.1978,35.33201), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-115.03825,41.99863,-117.1978,35.33201), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-115.03825,41.99863,-117.1978,35.33201), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-115.03825,41.99863,-117.1978,35.33201), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.40361,41.99929,-117.1978,37.03024), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.40361,41.99929,-117.1978,37.03024), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.40361,41.99929,-117.1978,37.03024), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.40361,41.99929,-117.1978,37.03024), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.01829,41.99984,-114.63349,41.99989), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.01829,41.99984,-114.63349,41.99989), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.01829,41.99984,-114.63349,41.99989), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.01829,41.99984,-114.63349,41.99989), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.0262,41.99989,-114.63349,41.99984), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.0262,41.99989,-114.63349,41.99984), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.0262,41.99989,-114.63349,41.99984), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.0262,41.99989,-114.63349,41.99984), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-114.89921,41.99991,-117.1978,35.13969), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-114.89921,41.99991,-117.1978,35.13969), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-114.89921,41.99991,-117.1978,35.13969), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-114.89921,41.99991,-117.1978,35.13969), mapfile, tile_dir, 17, 17, "nevada-nv")
	render_tiles((-117.1978,42.00038,-117.1978,36.97121), mapfile, tile_dir, 0, 11, "nevada-nv")
	render_tiles((-117.1978,42.00038,-117.1978,36.97121), mapfile, tile_dir, 13, 13, "nevada-nv")
	render_tiles((-117.1978,42.00038,-117.1978,36.97121), mapfile, tile_dir, 15, 15, "nevada-nv")
	render_tiles((-117.1978,42.00038,-117.1978,36.97121), mapfile, tile_dir, 17, 17, "nevada-nv")