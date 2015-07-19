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
    # Region: Connecticut
    # Region Name: CT

	render_tiles((-73.65734,40.98517,-73.48731,40.98552), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.65734,40.98517,-73.48731,40.98552), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.65734,40.98517,-73.48731,40.98552), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.65734,40.98517,-73.48731,40.98552), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.65737,40.98552,-73.48731,40.98517), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.65737,40.98552,-73.48731,40.98517), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.65737,40.98552,-73.48731,40.98517), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.65737,40.98552,-73.48731,40.98517), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.65936,41.00403,-73.48731,41.01786), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.65936,41.00403,-73.48731,41.01786), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.65936,41.00403,-73.48731,41.01786), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.65936,41.00403,-73.48731,41.01786), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.5957,41.016,-73.48731,41.0168), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.5957,41.016,-73.48731,41.0168), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.5957,41.016,-73.48731,41.0168), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.5957,41.016,-73.48731,41.0168), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.56197,41.0168,-73.48731,41.29542), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.56197,41.0168,-73.48731,41.29542), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.56197,41.0168,-73.48731,41.29542), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.56197,41.0168,-73.48731,41.29542), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.65953,41.01786,-73.48731,41.00403), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.65953,41.01786,-73.48731,41.00403), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.65953,41.01786,-73.48731,41.00403), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.65953,41.01786,-73.48731,41.00403), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.5169,41.03874,-73.65734,41.66672), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.5169,41.03874,-73.65734,41.66672), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.5169,41.03874,-73.65734,41.66672), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.5169,41.03874,-73.65734,41.66672), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.42217,41.04756,-73.48731,41.05825), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.42217,41.04756,-73.48731,41.05825), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.42217,41.04756,-73.48731,41.05825), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.42217,41.04756,-73.48731,41.05825), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.46824,41.05135,-73.48731,41.21276), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.46824,41.05135,-73.48731,41.21276), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.46824,41.05135,-73.48731,41.21276), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.46824,41.05135,-73.48731,41.21276), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.38723,41.05825,-73.48731,41.10402), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.38723,41.05825,-73.48731,41.10402), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.38723,41.05825,-73.48731,41.10402), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.38723,41.05825,-73.48731,41.10402), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.35423,41.08564,-73.48731,41.10402), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.35423,41.08564,-73.48731,41.10402), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.35423,41.08564,-73.48731,41.10402), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.35423,41.08564,-73.48731,41.10402), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.72778,41.1007,-73.48731,41.11526), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.72778,41.1007,-73.48731,41.11526), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.72778,41.1007,-73.48731,41.11526), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.72778,41.1007,-73.48731,41.11526), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.3723,41.10402,-73.48731,41.05825), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.3723,41.10402,-73.48731,41.05825), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.3723,41.10402,-73.48731,41.05825), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.3723,41.10402,-73.48731,41.05825), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.33066,41.11,-73.48731,41.08564), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.33066,41.11,-73.48731,41.08564), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.33066,41.11,-73.48731,41.08564), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.33066,41.11,-73.48731,41.08564), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.29721,41.11367,-73.48731,41.11668), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.29721,41.11367,-73.48731,41.11668), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.29721,41.11367,-73.48731,41.11668), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.29721,41.11367,-73.48731,41.11668), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.69594,41.11526,-73.48731,41.1007), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.26982,41.11668,-73.48731,41.1175), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.26982,41.11668,-73.48731,41.1175), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.26982,41.11668,-73.48731,41.1175), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.26982,41.11668,-73.48731,41.1175), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.26236,41.1175,-73.48731,41.11668), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.26236,41.1175,-73.48731,41.11668), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.26236,41.1175,-73.48731,41.11668), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.26236,41.1175,-73.48731,41.11668), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.13025,41.1468,-73.65734,42.04212), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.13025,41.1468,-73.65734,42.04212), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.13025,41.1468,-73.65734,42.04212), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.13025,41.1468,-73.65734,42.04212), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.17284,41.15344,-73.48731,41.1581), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.17284,41.15344,-73.48731,41.1581), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.17284,41.15344,-73.48731,41.1581), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.17284,41.15344,-73.48731,41.1581), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.10835,41.15372,-73.48731,41.16373), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.10835,41.15372,-73.48731,41.16373), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.10835,41.15372,-73.48731,41.16373), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.10835,41.15372,-73.48731,41.16373), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.20266,41.1581,-73.65734,42.04495), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.20266,41.1581,-73.65734,42.04495), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.20266,41.1581,-73.65734,42.04495), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.20266,41.1581,-73.65734,42.04495), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.10117,41.16373,-73.48731,41.15372), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.10117,41.16373,-73.48731,41.15372), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.10117,41.16373,-73.48731,41.15372), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.10117,41.16373,-73.48731,41.15372), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.07945,41.19402,-73.48731,41.16373), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.07945,41.19402,-73.48731,41.16373), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.07945,41.19402,-73.48731,41.16373), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.07945,41.19402,-73.48731,41.16373), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.02045,41.2064,-73.65734,42.0389), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.02045,41.2064,-73.65734,42.0389), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.02045,41.2064,-73.65734,42.0389), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.02045,41.2064,-73.65734,42.0389), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.48271,41.21276,-73.65734,42.04964), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.48271,41.21276,-73.65734,42.04964), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.48271,41.21276,-73.65734,42.04964), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.48271,41.21276,-73.65734,42.04964), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.98625,41.2335,-73.48731,41.23419), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.98625,41.2335,-73.48731,41.23419), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.98625,41.2335,-73.48731,41.23419), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.98625,41.2335,-73.48731,41.23419), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.98484,41.23419,-73.48731,41.2335), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.98484,41.23419,-73.48731,41.2335), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.98484,41.23419,-73.48731,41.2335), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.98484,41.23419,-73.48731,41.2335), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.76034,41.24124,-73.65734,42.003), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.76034,41.24124,-73.65734,42.003), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.76034,41.24124,-73.65734,42.003), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.76034,41.24124,-73.65734,42.003), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.97051,41.24127,-73.48731,41.23419), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.97051,41.24127,-73.48731,41.23419), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.97051,41.24127,-73.48731,41.23419), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.97051,41.24127,-73.48731,41.23419), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.88145,41.2426,-73.48731,41.24919), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.88145,41.2426,-73.48731,41.24919), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.88145,41.2426,-73.48731,41.24919), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.88145,41.2426,-73.48731,41.24919), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.69044,41.2467,-73.48731,41.26563), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.69044,41.2467,-73.48731,41.26563), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.69044,41.2467,-73.48731,41.26563), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.69044,41.2467,-73.48731,41.26563), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.90393,41.24919,-73.48731,41.2426), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.90393,41.24919,-73.48731,41.2426), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.90393,41.24919,-73.48731,41.2426), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.90393,41.24919,-73.48731,41.2426), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.54724,41.2505,-73.48731,41.25382), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.54724,41.2505,-73.48731,41.25382), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.54724,41.2505,-73.48731,41.25382), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.54724,41.2505,-73.48731,41.25382), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.53456,41.25382,-73.65734,42.0343), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.53456,41.25382,-73.65734,42.0343), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.53456,41.25382,-73.65734,42.0343), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.53456,41.25382,-73.65734,42.0343), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.93565,41.2585,-73.48731,41.24919), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.93565,41.2585,-73.48731,41.24919), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.93565,41.2585,-73.48731,41.24919), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.93565,41.2585,-73.48731,41.24919), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.38663,41.2618,-73.65734,42.03282), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.38663,41.2618,-73.65734,42.03282), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.38663,41.2618,-73.65734,42.03282), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.38663,41.2618,-73.65734,42.03282), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.78614,41.2648,-73.65734,42.00213), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.78614,41.2648,-73.65734,42.00213), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.78614,41.2648,-73.65734,42.00213), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.78614,41.2648,-73.65734,42.00213), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.65435,41.26563,-73.48731,41.2659), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.65435,41.26563,-73.48731,41.2659), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.65435,41.26563,-73.48731,41.2659), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.65435,41.26563,-73.48731,41.2659), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.65384,41.2659,-73.48731,41.26563), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.65384,41.2659,-73.48731,41.26563), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.65384,41.2659,-73.48731,41.26563), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.65384,41.2659,-73.48731,41.26563), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.59804,41.2687,-73.65734,42.0308), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.59804,41.2687,-73.65734,42.0308), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.59804,41.2687,-73.65734,42.0308), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.59804,41.2687,-73.65734,42.0308), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.47254,41.2701,-73.65734,42.03408), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.47254,41.2701,-73.65734,42.03408), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.47254,41.2701,-73.65734,42.03408), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.47254,41.2701,-73.65734,42.03408), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.34864,41.27745,-73.48731,41.27785), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.34864,41.27745,-73.48731,41.27785), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.34864,41.27745,-73.48731,41.27785), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.34864,41.27745,-73.48731,41.27785), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.34001,41.27785,-73.48731,41.27745), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.34001,41.27785,-73.48731,41.27745), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.34001,41.27785,-73.48731,41.27745), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.34001,41.27785,-73.48731,41.27745), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.40593,41.2784,-73.65734,42.03282), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.40593,41.2784,-73.65734,42.03282), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.40593,41.2784,-73.65734,42.03282), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.40593,41.2784,-73.65734,42.03282), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.29304,41.28004,-73.65734,42.03191), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.29304,41.28004,-73.65734,42.03191), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.29304,41.28004,-73.65734,42.03191), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.29304,41.28004,-73.65734,42.03191), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.55096,41.29542,-73.48731,41.36632), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.55096,41.29542,-73.48731,41.36632), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.55096,41.29542,-73.48731,41.36632), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.55096,41.29542,-73.48731,41.36632), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.13422,41.2994,-73.65734,42.02914), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.13422,41.2994,-73.65734,42.02914), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.13422,41.2994,-73.65734,42.02914), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.13422,41.2994,-73.65734,42.02914), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.23553,41.30041,-73.48731,41.3157), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.23553,41.30041,-73.48731,41.3157), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.23553,41.30041,-73.48731,41.3157), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.23553,41.30041,-73.48731,41.3157), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.09444,41.31416,-73.65734,42.02863), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.09444,41.31416,-73.65734,42.02863), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.09444,41.31416,-73.65734,42.02863), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.09444,41.31416,-73.65734,42.02863), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.20142,41.3157,-73.65734,42.0301), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.20142,41.3157,-73.65734,42.0301), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.20142,41.3157,-73.65734,42.0301), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.20142,41.3157,-73.65734,42.0301), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.0219,41.31684,-73.65734,42.02688), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.0219,41.31684,-73.65734,42.02688), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.0219,41.31684,-73.65734,42.02688), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.0219,41.31684,-73.65734,42.02688), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.86051,41.32025,-73.48731,41.41212), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.86051,41.32025,-73.48731,41.41212), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.86051,41.32025,-73.48731,41.41212), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.86051,41.32025,-73.48731,41.41212), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.95675,41.32987,-73.65734,42.02688), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.95675,41.32987,-73.65734,42.02688), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.95675,41.32987,-73.65734,42.02688), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.95675,41.32987,-73.65734,42.02688), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.8863,41.33641,-73.65734,42.02507), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.8863,41.33641,-73.65734,42.02507), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.8863,41.33641,-73.65734,42.02507), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.8863,41.33641,-73.65734,42.02507), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.83595,41.35394,-73.48731,41.41212), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.83595,41.35394,-73.48731,41.41212), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.83595,41.35394,-73.48731,41.41212), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.83595,41.35394,-73.48731,41.41212), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.54415,41.36632,-73.48731,41.37511), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.54415,41.36632,-73.48731,41.37511), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.54415,41.36632,-73.48731,41.37511), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.54415,41.36632,-73.48731,41.37511), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.54331,41.37511,-73.48731,41.3764), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.54331,41.37511,-73.48731,41.3764), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.54331,41.37511,-73.48731,41.3764), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.54331,41.37511,-73.48731,41.3764), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.54318,41.3764,-73.48731,41.37677), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.54318,41.3764,-73.48731,41.37677), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.54318,41.3764,-73.48731,41.37677), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.54318,41.3764,-73.48731,41.37677), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.54315,41.37677,-73.48731,41.3764), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.54315,41.37677,-73.48731,41.3764), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.54315,41.37677,-73.48731,41.3764), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.54315,41.37677,-73.48731,41.3764), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.83965,41.41212,-73.48731,41.35394), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.83965,41.41212,-73.48731,41.35394), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.83965,41.41212,-73.48731,41.35394), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.83965,41.41212,-73.48731,41.35394), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.79768,41.41671,-73.65734,42.00807), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.79768,41.41671,-73.65734,42.00807), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.79768,41.41671,-73.65734,42.00807), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.79768,41.41671,-73.65734,42.00807), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.53697,41.44109,-73.48731,41.37677), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.53697,41.44109,-73.48731,41.37677), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.53697,41.44109,-73.48731,41.37677), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.53697,41.44109,-73.48731,41.37677), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.52968,41.52716,-73.48731,41.44109), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.52968,41.52716,-73.48731,41.44109), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.52968,41.52716,-73.48731,41.44109), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.52968,41.52716,-73.48731,41.44109), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.79172,41.54577,-73.65734,41.807), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.79172,41.54577,-73.65734,41.807), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.79172,41.54577,-73.65734,41.807), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.79172,41.54577,-73.65734,41.807), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.78936,41.59685,-73.65734,41.64002), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.78936,41.59685,-73.65734,41.64002), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.78936,41.59685,-73.65734,41.64002), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.78936,41.59685,-73.65734,41.64002), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.78936,41.59691,-73.65734,41.64002), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.78936,41.59691,-73.65734,41.64002), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.78936,41.59691,-73.65734,41.64002), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.78936,41.59691,-73.65734,41.64002), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.78946,41.64002,-73.65734,41.59685), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.78946,41.64002,-73.65734,41.59685), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.78946,41.64002,-73.65734,41.59685), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.78946,41.64002,-73.65734,41.59685), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.52002,41.6412,-73.65734,41.66672), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.52002,41.6412,-73.65734,41.66672), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.52002,41.6412,-73.65734,41.66672), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.52002,41.6412,-73.65734,41.66672), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.51792,41.66672,-73.48731,41.03874), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.51792,41.66672,-73.48731,41.03874), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.51792,41.66672,-73.48731,41.03874), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.51792,41.66672,-73.48731,41.03874), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.78968,41.72457,-73.65734,41.64002), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.78968,41.72457,-73.65734,41.64002), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.78968,41.72457,-73.65734,41.64002), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.78968,41.72457,-73.65734,41.64002), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.78968,41.72473,-73.65734,41.64002), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.78968,41.72473,-73.65734,41.64002), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.78968,41.72473,-73.65734,41.64002), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.78968,41.72473,-73.65734,41.64002), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.79277,41.807,-73.65734,41.54577), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.79277,41.807,-73.65734,41.54577), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.79277,41.807,-73.65734,41.54577), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.79277,41.807,-73.65734,41.54577), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.50501,41.82377,-73.48731,41.03874), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.50501,41.82377,-73.48731,41.03874), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.50501,41.82377,-73.48731,41.03874), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.50501,41.82377,-73.48731,41.03874), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.81008,41.99832,-73.48731,41.2648), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.81008,41.99832,-73.48731,41.2648), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.81008,41.99832,-73.48731,41.2648), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.81008,41.99832,-73.48731,41.2648), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.77476,42.00213,-73.65734,42.003), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.77476,42.00213,-73.65734,42.003), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.77476,42.00213,-73.65734,42.003), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.77476,42.00213,-73.65734,42.003), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.76674,42.003,-73.48731,41.24124), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.76674,42.003,-73.48731,41.24124), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.76674,42.003,-73.48731,41.24124), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.76674,42.003,-73.48731,41.24124), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.79924,42.00807,-73.65734,42.02357), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.79924,42.00807,-73.65734,42.02357), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.79924,42.00807,-73.65734,42.02357), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.79924,42.00807,-73.65734,42.02357), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.80065,42.02357,-73.65734,42.00807), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.80065,42.02357,-73.65734,42.00807), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.80065,42.02357,-73.65734,42.00807), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.80065,42.02357,-73.65734,42.00807), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.88513,42.02507,-73.48731,41.33641), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.88513,42.02507,-73.48731,41.33641), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.88513,42.02507,-73.48731,41.33641), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.88513,42.02507,-73.48731,41.33641), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-71.98733,42.02688,-73.48731,41.32987), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-71.98733,42.02688,-73.48731,41.32987), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-71.98733,42.02688,-73.48731,41.32987), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-71.98733,42.02688,-73.48731,41.32987), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.10216,42.02863,-73.48731,41.31416), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.10216,42.02863,-73.48731,41.31416), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.10216,42.02863,-73.48731,41.31416), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.10216,42.02863,-73.48731,41.31416), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.13573,42.02914,-73.48731,41.2994), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.13573,42.02914,-73.48731,41.2994), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.13573,42.02914,-73.48731,41.2994), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.13573,42.02914,-73.48731,41.2994), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.19884,42.0301,-73.48731,41.3157), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.19884,42.0301,-73.48731,41.3157), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.19884,42.0301,-73.48731,41.3157), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.19884,42.0301,-73.48731,41.3157), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.60793,42.0308,-73.48731,41.2687), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.60793,42.0308,-73.48731,41.2687), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.60793,42.0308,-73.48731,41.2687), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.60793,42.0308,-73.48731,41.2687), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.31715,42.03191,-73.48731,41.27785), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.31715,42.03191,-73.48731,41.27785), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.31715,42.03191,-73.48731,41.27785), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.31715,42.03191,-73.48731,41.27785), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.39748,42.03282,-73.48731,41.2784), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.39748,42.03282,-73.48731,41.2784), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.39748,42.03282,-73.48731,41.2784), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.39748,42.03282,-73.48731,41.2784), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.50918,42.03408,-73.65734,42.0343), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.50918,42.03408,-73.65734,42.0343), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.50918,42.03408,-73.65734,42.0343), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.50918,42.03408,-73.65734,42.0343), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.52813,42.0343,-73.48731,41.25382), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.52813,42.0343,-73.48731,41.25382), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.52813,42.0343,-73.48731,41.25382), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.52813,42.0343,-73.48731,41.25382), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.7355,42.0364,-73.48731,41.24124), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.7355,42.0364,-73.48731,41.24124), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.7355,42.0364,-73.48731,41.24124), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.7355,42.0364,-73.48731,41.24124), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.84714,42.03689,-73.48731,41.2426), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.84714,42.03689,-73.48731,41.2426), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.84714,42.03689,-73.48731,41.2426), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.84714,42.03689,-73.48731,41.2426), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-72.99955,42.03865,-73.65734,42.0389), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-72.99955,42.03865,-73.65734,42.0389), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-72.99955,42.03865,-73.65734,42.0389), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-72.99955,42.03865,-73.65734,42.0389), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.00874,42.0389,-73.65734,42.03865), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.00874,42.0389,-73.65734,42.03865), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.00874,42.0389,-73.65734,42.03865), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.00874,42.0389,-73.65734,42.03865), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.05341,42.04012,-73.48731,41.19402), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.05341,42.04012,-73.48731,41.19402), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.05341,42.04012,-73.48731,41.19402), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.05341,42.04012,-73.48731,41.19402), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.12725,42.04212,-73.48731,41.1468), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.12725,42.04212,-73.48731,41.1468), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.12725,42.04212,-73.48731,41.1468), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.12725,42.04212,-73.48731,41.1468), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.23106,42.04495,-73.48731,41.1581), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.23106,42.04495,-73.48731,41.1581), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.23106,42.04495,-73.48731,41.1581), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.23106,42.04495,-73.48731,41.1581), mapfile, tile_dir, 17, 17, "connecticut-ct")
	render_tiles((-73.48731,42.04964,-73.48731,41.21276), mapfile, tile_dir, 0, 11, "connecticut-ct")
	render_tiles((-73.48731,42.04964,-73.48731,41.21276), mapfile, tile_dir, 13, 13, "connecticut-ct")
	render_tiles((-73.48731,42.04964,-73.48731,41.21276), mapfile, tile_dir, 15, 15, "connecticut-ct")
	render_tiles((-73.48731,42.04964,-73.48731,41.21276), mapfile, tile_dir, 17, 17, "connecticut-ct")