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
        mapfile = "../../../tilestyles/mazda/mazda.xml"
    except KeyError:
        print("[MapFile] Not found")
        sys.exit(1)
    try:
        # ./tilegen/zones/[zone]/[region]
        tile_dir = "../../../../output/"
    except KeyError:
        print("[OutputDir] No output directory found")
        sys.exit(1)

    if not tile_dir.endswith('/'):
        tile_dir = tile_dir + '/'


    print ("Starting")

    # ------------------------------------------------------------------------
    # Tile Render Data
    # Zone: world
    # Region: AM
    # Region Name: Armenia

    render_tiles((46.17796,38.84422,45.0225,39.6578), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.17796,38.84422,45.0225,39.6578), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.17796,38.84422,45.0225,39.6578), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.17796,38.84422,45.0225,39.6578), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5336,38.87082,45.0225,38.87672), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5336,38.87082,45.0225,38.87672), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5336,38.87082,45.0225,38.87672), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5336,38.87082,45.0225,38.87672), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.53992,38.87672,45.0225,39.5644), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.53992,38.87672,45.0225,39.5644), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.53992,38.87672,45.0225,39.5644), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.53992,38.87672,45.0225,39.5644), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.35471,38.91054,45.0225,39.6281), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.35471,38.91054,45.0225,39.6281), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.35471,38.91054,45.0225,39.6281), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.35471,38.91054,45.0225,39.6281), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.1072,38.9361,45.0225,39.6894), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.1072,38.9361,45.0225,39.6894), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.1072,38.9361,45.0225,39.6894), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.1072,38.9361,45.0225,39.6894), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4947,38.9597,45.0225,39.1286), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4947,38.9597,45.0225,39.1286), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4947,38.9597,45.0225,39.1286), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4947,38.9597,45.0225,39.1286), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5236,39.0442,45.0225,39.3303), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5236,39.0442,45.0225,39.3303), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5236,39.0442,45.0225,39.3303), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5236,39.0442,45.0225,39.3303), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.0428,39.0783,45.0225,39.6894), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.0428,39.0783,45.0225,39.6894), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.0428,39.0783,45.0225,39.6894), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.0428,39.0783,45.0225,39.6894), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5108,39.0969,45.0225,39.5133), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5108,39.0969,45.0225,39.5133), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5108,39.0969,45.0225,39.5133), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5108,39.0969,45.0225,39.5133), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4936,39.1286,45.0225,38.9597), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4936,39.1286,45.0225,38.9597), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4936,39.1286,45.0225,38.9597), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4936,39.1286,45.0225,38.9597), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4239,39.1639,45.0225,39.2014), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4239,39.1639,45.0225,39.2014), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4239,39.1639,45.0225,39.2014), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4239,39.1639,45.0225,39.2014), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9703,39.1664,45.0225,39.2067), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9703,39.1664,45.0225,39.2067), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9703,39.1664,45.0225,39.2067), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9703,39.1664,45.0225,39.2067), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5456,39.1894,45.0225,39.5386), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5456,39.1894,45.0225,39.5386), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5456,39.1894,45.0225,39.5386), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5456,39.1894,45.0225,39.5386), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4161,39.2014,45.0225,39.5839), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4161,39.2014,45.0225,39.5839), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4161,39.2014,45.0225,39.5839), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4161,39.2014,45.0225,39.5839), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9719,39.2067,45.0225,39.1664), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9719,39.2067,45.0225,39.1664), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9719,39.2067,45.0225,39.1664), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9719,39.2067,45.0225,39.1664), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4522,39.2186,45.0225,39.1639), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4522,39.2186,45.0225,39.1639), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4522,39.2186,45.0225,39.1639), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4522,39.2186,45.0225,39.1639), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.6225,39.2247,45.0225,39.5386), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.6225,39.2247,45.0225,39.5386), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.6225,39.2247,45.0225,39.5386), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.6225,39.2247,45.0225,39.5386), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.0089,39.2508,45.0225,39.7731), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.0089,39.2508,45.0225,39.7731), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.0089,39.2508,45.0225,39.7731), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.0089,39.2508,45.0225,39.7731), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5442,39.2789,45.0225,39.1894), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5442,39.2789,45.0225,39.1894), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5442,39.2789,45.0225,39.1894), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5442,39.2789,45.0225,39.1894), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9781,39.28,46.17796,40.1169), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9781,39.28,46.17796,40.1169), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9781,39.28,46.17796,40.1169), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9781,39.28,46.17796,40.1169), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8858,39.3194,46.17796,40.2697), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8858,39.3194,46.17796,40.2697), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8858,39.3194,46.17796,40.2697), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8858,39.3194,46.17796,40.2697), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5181,39.3303,45.0225,39.4758), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5181,39.3303,45.0225,39.4758), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5181,39.3303,45.0225,39.4758), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5181,39.3303,45.0225,39.4758), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8556,39.3481,45.0225,39.8236), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8556,39.3481,45.0225,39.8236), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8556,39.3481,45.0225,39.8236), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8556,39.3481,45.0225,39.8236), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.7956,39.3564,45.0225,39.9375), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.7956,39.3564,45.0225,39.9375), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.7956,39.3564,45.0225,39.9375), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.7956,39.3564,45.0225,39.9375), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.7761,39.3747,45.0225,39.5692), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.7761,39.3747,45.0225,39.5692), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.7761,39.3747,45.0225,39.5692), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.7761,39.3747,45.0225,39.5692), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4039,39.3786,45.0225,39.4508), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4039,39.3786,45.0225,39.4508), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4039,39.3786,45.0225,39.4508), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4039,39.3786,45.0225,39.4508), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.3806,39.425,45.0225,39.4508), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.3806,39.425,45.0225,39.4508), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.3806,39.425,45.0225,39.4508), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.3806,39.425,45.0225,39.4508), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.83,39.4494,45.0225,39.5453), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.83,39.4494,45.0225,39.5453), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.83,39.4494,45.0225,39.5453), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.83,39.4494,45.0225,39.5453), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4008,39.4508,45.0225,39.3786), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4008,39.4508,45.0225,39.3786), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4008,39.4508,45.0225,39.3786), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4008,39.4508,45.0225,39.3786), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5167,39.4758,45.0225,39.3303), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5167,39.4758,45.0225,39.3303), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5167,39.4758,45.0225,39.3303), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5167,39.4758,45.0225,39.3303), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4575,39.4936,46.17796,40.5811), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4575,39.4936,46.17796,40.5811), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4575,39.4936,46.17796,40.5811), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4575,39.4936,46.17796,40.5811), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8147,39.495,45.0225,39.8656), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8147,39.495,45.0225,39.8656), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8147,39.495,45.0225,39.8656), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8147,39.495,45.0225,39.8656), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5097,39.5133,45.0225,39.0969), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5097,39.5133,45.0225,39.0969), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5097,39.5133,45.0225,39.0969), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5097,39.5133,45.0225,39.0969), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4086,39.53,46.17796,40.6), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4086,39.53,46.17796,40.6), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4086,39.53,46.17796,40.6), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4086,39.53,46.17796,40.6), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.3261,39.5383,45.0225,39.5881), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.3261,39.5383,45.0225,39.5881), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.3261,39.5383,45.0225,39.5881), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.3261,39.5383,45.0225,39.5881), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5464,39.5386,45.0225,39.1894), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5464,39.5386,45.0225,39.1894), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5464,39.5386,45.0225,39.1894), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5464,39.5386,45.0225,39.1894), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8231,39.5453,45.0225,39.8656), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8231,39.5453,45.0225,39.8656), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8231,39.5453,45.0225,39.8656), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8231,39.5453,45.0225,39.8656), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6267,39.56,46.17796,40.8439), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6267,39.56,46.17796,40.8439), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6267,39.56,46.17796,40.8439), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6267,39.56,46.17796,40.8439), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5414,39.5644,45.0225,38.87672), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5414,39.5644,45.0225,38.87672), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5414,39.5644,45.0225,38.87672), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5414,39.5644,45.0225,38.87672), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.7833,39.5692,45.0225,39.9375), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.7833,39.5692,45.0225,39.9375), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.7833,39.5692,45.0225,39.9375), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.7833,39.5692,45.0225,39.9375), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.2044,39.5722,46.17796,41.1169), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.2044,39.5722,46.17796,41.1169), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.2044,39.5722,46.17796,41.1169), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.2044,39.5722,46.17796,41.1169), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1711,39.5792,45.0225,39.6725), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1711,39.5792,45.0225,39.6725), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1711,39.5792,45.0225,39.6725), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1711,39.5792,45.0225,39.6725), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.7322,39.5806,45.0225,39.3747), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.7322,39.5806,45.0225,39.3747), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.7322,39.5806,45.0225,39.3747), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.7322,39.5806,45.0225,39.3747), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.5133,39.5836,45.0225,39.0969), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.5133,39.5836,45.0225,39.0969), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.5133,39.5836,45.0225,39.0969), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.5133,39.5836,45.0225,39.0969), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.4139,39.5839,45.0225,39.2014), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.4139,39.5839,45.0225,39.2014), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.4139,39.5839,45.0225,39.2014), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.4139,39.5839,45.0225,39.2014), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.3075,39.5881,45.0225,39.5383), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.3075,39.5881,45.0225,39.5383), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.3075,39.5881,45.0225,39.5383), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.3075,39.5881,45.0225,39.5383), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.2503,39.595,45.0225,39.5981), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.2503,39.595,45.0225,39.5981), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.2503,39.595,45.0225,39.5981), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.2503,39.595,45.0225,39.5981), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.2042,39.5981,45.0225,38.84422), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.2042,39.5981,45.0225,38.84422), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.2042,39.5981,45.0225,38.84422), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.2042,39.5981,45.0225,38.84422), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.2539,39.6078,46.17796,41.0217), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.2539,39.6078,46.17796,41.0217), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.2539,39.6078,46.17796,41.0217), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.2539,39.6078,46.17796,41.0217), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.2819,39.6086,45.0225,39.5881), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.2819,39.6086,45.0225,39.5881), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.2819,39.6086,45.0225,39.5881), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.2819,39.6086,45.0225,39.5881), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.35,39.6281,45.0225,38.91054), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.35,39.6281,45.0225,38.91054), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.35,39.6281,45.0225,38.91054), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.35,39.6281,45.0225,38.91054), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.1536,39.6578,45.0225,38.84422), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.1536,39.6578,45.0225,38.84422), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.1536,39.6578,45.0225,38.84422), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.1536,39.6578,45.0225,38.84422), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1758,39.6725,45.0225,39.5792), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1758,39.6725,45.0225,39.5792), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1758,39.6725,45.0225,39.5792), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1758,39.6725,45.0225,39.5792), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.0678,39.6894,45.0225,39.0783), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.0678,39.6894,45.0225,39.0783), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.0678,39.6894,45.0225,39.0783), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.0678,39.6894,45.0225,39.0783), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.77858,39.70665,46.17796,41.2619), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.77858,39.70665,46.17796,41.2619), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.77858,39.70665,46.17796,41.2619), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.77858,39.70665,46.17796,41.2619), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.9331,39.7214,46.17796,41.2617), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.9331,39.7214,46.17796,41.2617), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.9331,39.7214,46.17796,41.2617), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.9331,39.7214,46.17796,41.2617), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.8583,39.725,46.17796,41.2147), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.8583,39.725,46.17796,41.2147), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.8583,39.725,46.17796,41.2147), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.8583,39.725,46.17796,41.2147), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.0061,39.7731,45.0225,39.2508), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.0061,39.7731,45.0225,39.2508), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.0061,39.7731,45.0225,39.2508), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.0061,39.7731,45.0225,39.2508), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0619,39.7789,46.17796,41.2453), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0619,39.7789,46.17796,41.2453), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0619,39.7789,46.17796,41.2453), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0619,39.7789,46.17796,41.2453), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9264,39.7881,46.17796,40.0925), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9264,39.7881,46.17796,40.0925), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9264,39.7881,46.17796,40.0925), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9264,39.7881,46.17796,40.0925), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.62526,39.81499,46.17796,41.2317), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.62526,39.81499,46.17796,41.2317), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.62526,39.81499,46.17796,41.2317), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.62526,39.81499,46.17796,41.2317), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8567,39.8236,45.0225,39.3481), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8567,39.8236,45.0225,39.3481), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8567,39.8236,45.0225,39.3481), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8567,39.8236,45.0225,39.3481), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8164,39.8656,45.0225,39.495), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8164,39.8656,45.0225,39.495), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8164,39.8656,45.0225,39.495), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8164,39.8656,45.0225,39.495), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.54027,39.91998,46.17796,41.2142), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.54027,39.91998,46.17796,41.2142), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.54027,39.91998,46.17796,41.2142), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.54027,39.91998,46.17796,41.2142), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.7842,39.9375,45.0225,39.5692), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.7842,39.9375,45.0225,39.5692), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.7842,39.9375,45.0225,39.5692), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.7842,39.9375,45.0225,39.5692), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.5928,39.9825,46.17796,40.7878), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.5928,39.9825,46.17796,40.7878), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.5928,39.9825,46.17796,40.7878), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.5928,39.9825,46.17796,40.7878), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6039,40.0078,46.17796,40.7878), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6039,40.0078,46.17796,40.7878), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6039,40.0078,46.17796,40.7878), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6039,40.0078,46.17796,40.7878), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.01693,40.0086,46.17796,41.1631), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.01693,40.0086,46.17796,41.1631), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.01693,40.0086,46.17796,41.1631), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.01693,40.0086,46.17796,41.1631), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9056,40.0206,46.17796,40.2647), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9056,40.0206,46.17796,40.2647), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9056,40.0206,46.17796,40.2647), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9056,40.0206,46.17796,40.2647), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6381,40.0217,45.0225,39.56), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6381,40.0217,45.0225,39.56), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6381,40.0217,45.0225,39.56), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6381,40.0217,45.0225,39.56), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.28027,40.04665,46.17796,41.195), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.28027,40.04665,46.17796,41.195), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.28027,40.04665,46.17796,41.195), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.28027,40.04665,46.17796,41.195), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9244,40.0925,45.0225,39.7881), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9244,40.0925,45.0225,39.7881), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9244,40.0925,45.0225,39.7881), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9244,40.0925,45.0225,39.7881), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.65776,40.1086,46.17796,40.13416), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.65776,40.1086,46.17796,40.13416), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.65776,40.1086,46.17796,40.13416), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.65776,40.1086,46.17796,40.13416), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9769,40.1169,45.0225,39.28), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9769,40.1169,45.0225,39.28), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9769,40.1169,45.0225,39.28), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9769,40.1169,45.0225,39.28), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.65276,40.13416,46.17796,40.1086), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.65276,40.13416,46.17796,40.1086), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.65276,40.13416,46.17796,40.1086), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.65276,40.13416,46.17796,40.1086), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.72109,40.16721,46.17796,40.78249), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.72109,40.16721,46.17796,40.78249), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.72109,40.16721,46.17796,40.78249), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.72109,40.16721,46.17796,40.78249), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((46.0028,40.2203,45.0225,39.7731), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((46.0028,40.2203,45.0225,39.7731), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((46.0028,40.2203,45.0225,39.7731), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((46.0028,40.2203,45.0225,39.7731), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9133,40.2647,45.0225,40.0206), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9133,40.2647,45.0225,40.0206), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9133,40.2647,45.0225,40.0206), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9133,40.2647,45.0225,40.0206), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8758,40.2697,45.0225,39.3194), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8758,40.2697,45.0225,39.3194), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8758,40.2697,45.0225,39.3194), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8758,40.2697,45.0225,39.3194), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.9583,40.2722,45.0225,39.1664), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.9583,40.2722,45.0225,39.1664), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.9583,40.2722,45.0225,39.1664), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.9583,40.2722,45.0225,39.1664), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.8414,40.3033,45.0225,39.4494), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.8414,40.3033,45.0225,39.4494), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.8414,40.3033,45.0225,39.4494), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.8414,40.3033,45.0225,39.4494), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.59443,40.33749,46.17796,40.44026), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.59443,40.33749,46.17796,40.44026), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.59443,40.33749,46.17796,40.44026), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.59443,40.33749,46.17796,40.44026), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6628,40.3758,45.0225,40.0217), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6628,40.3758,45.0225,40.0217), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6628,40.3758,45.0225,40.0217), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6628,40.3758,45.0225,40.0217), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.61804,40.39554,46.17796,40.98832), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.61804,40.39554,46.17796,40.98832), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.61804,40.39554,46.17796,40.98832), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.61804,40.39554,46.17796,40.98832), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.59276,40.44026,46.17796,40.33749), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.59276,40.44026,46.17796,40.33749), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.59276,40.44026,46.17796,40.33749), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.59276,40.44026,46.17796,40.33749), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.5403,40.4533,46.17796,40.8778), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.5403,40.4533,46.17796,40.8778), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.5403,40.4533,46.17796,40.8778), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.5403,40.4533,46.17796,40.8778), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.5069,40.5125,46.17796,40.9467), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.5069,40.5125,46.17796,40.9467), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.5069,40.5125,46.17796,40.9467), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.5069,40.5125,46.17796,40.9467), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4692,40.5381,46.17796,40.5811), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4692,40.5381,46.17796,40.5811), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4692,40.5381,46.17796,40.5811), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4692,40.5381,46.17796,40.5811), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4592,40.5811,45.0225,39.4936), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4592,40.5811,45.0225,39.4936), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4592,40.5811,45.0225,39.4936), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4592,40.5811,45.0225,39.4936), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4206,40.6,46.17796,40.7292), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4206,40.6,46.17796,40.7292), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4206,40.6,46.17796,40.7292), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4206,40.6,46.17796,40.7292), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.3911,40.6433,46.17796,40.6711), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.3911,40.6433,46.17796,40.6711), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.3911,40.6433,46.17796,40.6711), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.3911,40.6433,46.17796,40.6711), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.3883,40.6711,46.17796,40.6433), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.3883,40.6711,46.17796,40.6433), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.3883,40.6711,46.17796,40.6433), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.3883,40.6711,46.17796,40.6433), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.75353,40.67593,46.17796,40.78249), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.75353,40.67593,46.17796,40.78249), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.75353,40.67593,46.17796,40.78249), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.75353,40.67593,46.17796,40.78249), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4292,40.7292,46.17796,40.9569), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4292,40.7292,46.17796,40.9569), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4292,40.7292,46.17796,40.9569), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4292,40.7292,46.17796,40.9569), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.73582,40.78249,46.17796,40.16721), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.73582,40.78249,46.17796,40.16721), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.73582,40.78249,46.17796,40.16721), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.73582,40.78249,46.17796,40.16721), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.5942,40.7878,45.0225,39.9825), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.5942,40.7878,45.0225,39.9825), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.5942,40.7878,45.0225,39.9825), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.5942,40.7878,45.0225,39.9825), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6208,40.8439,46.17796,40.8719), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6208,40.8439,46.17796,40.8719), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6208,40.8439,46.17796,40.8719), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6208,40.8439,46.17796,40.8719), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.6181,40.8719,46.17796,40.8439), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.6181,40.8719,46.17796,40.8439), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.6181,40.8719,46.17796,40.8439), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.6181,40.8719,46.17796,40.8439), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.5661,40.8778,46.17796,40.4533), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.5661,40.8778,46.17796,40.4533), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.5661,40.8778,46.17796,40.4533), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.5661,40.8778,46.17796,40.4533), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4878,40.9467,46.17796,40.5381), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4878,40.9467,46.17796,40.5381), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4878,40.9467,46.17796,40.5381), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4878,40.9467,46.17796,40.5381), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4372,40.9569,46.17796,41.0225), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4372,40.9569,46.17796,41.0225), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4372,40.9569,46.17796,41.0225), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4372,40.9569,46.17796,41.0225), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.60638,40.98832,46.17796,40.39554), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.60638,40.98832,46.17796,40.39554), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.60638,40.98832,46.17796,40.39554), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.60638,40.98832,46.17796,40.39554), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.55415,40.99804,46.17796,41.1364), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.55415,40.99804,46.17796,41.1364), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.55415,40.99804,46.17796,41.1364), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.55415,40.99804,46.17796,41.1364), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.345,41.0006,45.0225,39.5383), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.345,41.0006,45.0225,39.5383), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.345,41.0006,45.0225,39.5383), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.345,41.0006,45.0225,39.5383), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.2328,41.0217,46.17796,41.1419), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.2328,41.0217,46.17796,41.1419), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.2328,41.0217,46.17796,41.1419), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.2328,41.0217,46.17796,41.1419), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.4389,41.0225,46.17796,40.9569), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.4389,41.0225,46.17796,40.9569), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.4389,41.0225,46.17796,40.9569), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.4389,41.0225,46.17796,40.9569), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.47359,41.04193,46.17796,41.10621), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.47359,41.04193,46.17796,41.10621), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.47359,41.04193,46.17796,41.10621), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.47359,41.04193,46.17796,41.10621), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1328,41.0567,46.17796,41.0828), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1328,41.0567,46.17796,41.0828), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1328,41.0567,46.17796,41.0828), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1328,41.0567,46.17796,41.0828), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0914,41.0628,46.17796,41.1161), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0914,41.0628,46.17796,41.1161), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0914,41.0628,46.17796,41.1161), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0914,41.0628,46.17796,41.1161), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1386,41.0828,46.17796,41.0567), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1386,41.0828,46.17796,41.0567), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1386,41.0828,46.17796,41.0567), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1386,41.0828,46.17796,41.0567), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1569,41.0856,46.17796,41.2011), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1569,41.0856,46.17796,41.2011), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1569,41.0856,46.17796,41.2011), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1569,41.0856,46.17796,41.2011), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0733,41.1011,46.17796,41.1161), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0733,41.1011,46.17796,41.1161), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0733,41.1011,46.17796,41.1161), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0733,41.1011,46.17796,41.1161), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.47324,41.10621,46.17796,41.04193), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.7822,41.1158,46.17796,40.67593), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.7822,41.1158,46.17796,40.67593), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.7822,41.1158,46.17796,40.67593), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.7822,41.1158,46.17796,40.67593), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0814,41.1161,46.17796,41.1011), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0814,41.1161,46.17796,41.1011), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0814,41.1161,46.17796,41.1011), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0814,41.1161,46.17796,41.1011), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1947,41.1169,45.0225,39.5722), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1947,41.1169,45.0225,39.5722), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1947,41.1169,45.0225,39.5722), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1947,41.1169,45.0225,39.5722), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.5617,41.1364,46.17796,40.99804), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.5617,41.1364,46.17796,40.99804), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.5617,41.1364,46.17796,40.99804), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.5617,41.1364,46.17796,40.99804), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.2228,41.1419,46.17796,41.0217), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.2228,41.1419,46.17796,41.0217), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.2228,41.1419,46.17796,41.0217), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.2228,41.1419,46.17796,41.0217), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.8625,41.1622,46.17796,41.1158), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.8625,41.1622,46.17796,41.1158), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.8625,41.1622,46.17796,41.1158), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.8625,41.1622,46.17796,41.1158), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((43.9856,41.1631,45.0225,40.0086), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((43.9856,41.1631,45.0225,40.0086), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((43.9856,41.1631,45.0225,40.0086), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((43.9856,41.1631,45.0225,40.0086), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.5542,41.1864,45.0225,39.91998), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.5542,41.1864,45.0225,39.91998), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.5542,41.1864,45.0225,39.91998), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.5542,41.1864,45.0225,39.91998), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.4608,41.1864,46.17796,41.2142), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.4608,41.1864,46.17796,41.2142), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.4608,41.1864,46.17796,41.2142), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.4608,41.1864,46.17796,41.2142), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.5775,41.1864,46.17796,41.1864), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.5775,41.1864,46.17796,41.1864), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.5775,41.1864,46.17796,41.1864), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.5775,41.1864,46.17796,41.1864), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.1575,41.1883,46.17796,41.1992), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.1575,41.1883,46.17796,41.1992), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.1575,41.1883,46.17796,41.1992), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.1575,41.1883,46.17796,41.1992), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.07,41.1917,45.0225,40.0086), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.07,41.1917,45.0225,40.0086), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.07,41.1917,45.0225,40.0086), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.07,41.1917,45.0225,40.0086), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.3019,41.195,45.0225,40.04665), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.3019,41.195,45.0225,40.04665), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.3019,41.195,45.0225,40.04665), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.3019,41.195,45.0225,40.04665), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.1783,41.1992,46.17796,41.2364), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.1783,41.1992,46.17796,41.2364), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.1783,41.1992,46.17796,41.2364), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.1783,41.1992,46.17796,41.2364), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.1478,41.2011,46.17796,41.0856), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.1478,41.2011,46.17796,41.0856), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.1478,41.2011,46.17796,41.0856), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.1478,41.2011,46.17796,41.0856), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0528,41.2033,46.17796,41.2453), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0528,41.2033,46.17796,41.2453), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0528,41.2033,46.17796,41.2453), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0528,41.2033,46.17796,41.2453), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.7114,41.2128,45.0225,39.70665), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.7114,41.2128,45.0225,39.70665), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.7114,41.2128,45.0225,39.70665), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.7114,41.2128,45.0225,39.70665), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.5292,41.2142,45.0225,39.91998), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.5292,41.2142,45.0225,39.91998), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.5292,41.2142,45.0225,39.91998), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.5292,41.2142,45.0225,39.91998), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.8636,41.2147,45.0225,39.725), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.8636,41.2147,45.0225,39.725), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.8636,41.2147,45.0225,39.725), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.8636,41.2147,45.0225,39.725), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.3656,41.2197,46.17796,41.195), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.3656,41.2197,46.17796,41.195), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.3656,41.2197,46.17796,41.195), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.3656,41.2197,46.17796,41.195), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.8778,41.2286,46.17796,41.2147), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.8778,41.2286,46.17796,41.2147), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.8778,41.2286,46.17796,41.2147), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.8778,41.2286,46.17796,41.2147), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.6344,41.2317,45.0225,39.81499), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.6344,41.2317,45.0225,39.81499), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.6344,41.2317,45.0225,39.81499), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.6344,41.2317,45.0225,39.81499), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.1928,41.2364,46.17796,41.1992), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.1928,41.2364,46.17796,41.1992), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.1928,41.2364,46.17796,41.1992), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.1928,41.2364,46.17796,41.1992), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0603,41.2453,45.0225,39.7789), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0603,41.2453,45.0225,39.7789), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0603,41.2453,45.0225,39.7789), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0603,41.2453,45.0225,39.7789), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.9553,41.2617,45.0225,39.7214), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.9553,41.2617,45.0225,39.7214), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.9553,41.2617,45.0225,39.7214), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.9553,41.2617,45.0225,39.7214), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.8219,41.2619,46.17796,41.2869), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.8219,41.2619,46.17796,41.2869), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.8219,41.2619,46.17796,41.2869), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.8219,41.2619,46.17796,41.2869), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((44.8339,41.2869,46.17796,41.2619), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((44.8339,41.2869,46.17796,41.2619), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((44.8339,41.2869,46.17796,41.2619), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((44.8339,41.2869,46.17796,41.2619), mapfile, tile_dir, 17, 17, "am-armenia")
    render_tiles((45.0225,41.2986,46.17796,41.2033), mapfile, tile_dir, 0, 11, "am-armenia")
    render_tiles((45.0225,41.2986,46.17796,41.2033), mapfile, tile_dir, 13, 13, "am-armenia")
    render_tiles((45.0225,41.2986,46.17796,41.2033), mapfile, tile_dir, 15, 15, "am-armenia")
    render_tiles((45.0225,41.2986,46.17796,41.2033), mapfile, tile_dir, 17, 17, "am-armenia")