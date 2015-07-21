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
    # Region: BT
    # Region Name: Bhutan

    render_tiles((89.84192,26.70138,89.84192,28.24888), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.84192,26.70138,89.84192,28.24888), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.84192,26.70138,89.84192,28.24888), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.84192,26.70138,89.84192,28.24888), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.6183,26.72694,89.99635,26.77111), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.6183,26.72694,89.99635,26.77111), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.6183,26.72694,89.99635,26.77111), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.6183,26.72694,89.99635,26.77111), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.98996,26.73583,89.84192,28.32333), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.98996,26.73583,89.84192,28.32333), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.98996,26.73583,89.84192,28.32333), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.98996,26.73583,89.84192,28.32333), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.14304,26.75333,89.84192,28.32333), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.14304,26.75333,89.84192,28.32333), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.14304,26.75333,89.84192,28.32333), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.14304,26.75333,89.84192,28.32333), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.63609,26.77111,89.99635,26.72694), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.63609,26.77111,89.99635,26.72694), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.63609,26.77111,89.99635,26.72694), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.63609,26.77111,89.99635,26.72694), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.68246,26.77417,89.84192,28.05055), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.68246,26.77417,89.84192,28.05055), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.68246,26.77417,89.84192,28.05055), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.68246,26.77417,89.84192,28.05055), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.30746,26.77805,89.84192,28.09666), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.30746,26.77805,89.84192,28.09666), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.30746,26.77805,89.84192,28.09666), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.30746,26.77805,89.84192,28.09666), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.71248,26.8,89.84192,27.88888), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.71248,26.8,89.84192,27.88888), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.71248,26.8,89.84192,27.88888), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.71248,26.8,89.84192,27.88888), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.37135,26.80083,89.84192,28.05249), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.37135,26.80083,89.84192,28.05249), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.37135,26.80083,89.84192,28.05249), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.37135,26.80083,89.84192,28.05249), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.54219,26.80444,89.99635,26.87472), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.54219,26.80444,89.99635,26.87472), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.54219,26.80444,89.99635,26.87472), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.54219,26.80444,89.99635,26.87472), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.22081,26.81472,89.84192,27.795), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.22081,26.81472,89.84192,27.795), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.22081,26.81472,89.84192,27.795), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.22081,26.81472,89.84192,27.795), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.11024,26.82916,89.84192,27.56749), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.11024,26.82916,89.84192,27.56749), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.11024,26.82916,89.84192,27.56749), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.11024,26.82916,89.84192,27.56749), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.99719,26.85194,89.99635,27.47194), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.99719,26.85194,89.99635,27.47194), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.99719,26.85194,89.99635,27.47194), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.99719,26.85194,89.99635,27.47194), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.91692,26.85416,89.99635,26.89388), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.91692,26.85416,89.99635,26.89388), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.91692,26.85416,89.99635,26.89388), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.91692,26.85416,89.99635,26.89388), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.36079,26.85999,89.84192,27.87166), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.36079,26.85999,89.84192,27.87166), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.36079,26.85999,89.84192,27.87166), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.36079,26.85999,89.84192,27.87166), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.06998,26.86194,89.99635,27.02277), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.06998,26.86194,89.99635,27.02277), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.06998,26.86194,89.99635,27.02277), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.06998,26.86194,89.99635,27.02277), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.8358,26.86333,89.99635,27.4161), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.8358,26.86333,89.99635,27.4161), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.8358,26.86333,89.99635,27.4161), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.8358,26.86333,89.99635,27.4161), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.41885,26.87333,89.99635,26.80083), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.41885,26.87333,89.99635,26.80083), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.41885,26.87333,89.99635,26.80083), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.41885,26.87333,89.99635,26.80083), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.49219,26.87472,89.99635,26.80444), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.49219,26.87472,89.99635,26.80444), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.49219,26.87472,89.99635,26.80444), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.49219,26.87472,89.99635,26.80444), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.91525,26.89388,89.99635,26.85416), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.91525,26.89388,89.99635,26.85416), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.91525,26.89388,89.99635,26.85416), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.91525,26.89388,89.99635,26.85416), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.34413,26.89416,89.84192,28.25777), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.34413,26.89416,89.84192,28.25777), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.34413,26.89416,89.84192,28.25777), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.34413,26.89416,89.84192,28.25777), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.3922,26.9036,89.84192,28.23555), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.3922,26.9036,89.84192,28.23555), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.3922,26.9036,89.84192,28.23555), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.3922,26.9036,89.84192,28.23555), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.11218,26.92027,89.99635,27.28583), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.11218,26.92027,89.99635,27.28583), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.11218,26.92027,89.99635,27.28583), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.11218,26.92027,89.99635,27.28583), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.87302,26.95499,89.99635,27.08055), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.87302,26.95499,89.99635,27.08055), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.87302,26.95499,89.99635,27.08055), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.87302,26.95499,89.99635,27.08055), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.92163,26.98777,89.99635,27.32104), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.92163,26.98777,89.99635,27.32104), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.92163,26.98777,89.99635,27.32104), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.92163,26.98777,89.99635,27.32104), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.08331,27.02277,89.99635,26.86194), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.08331,27.02277,89.99635,26.86194), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.08331,27.02277,89.99635,26.86194), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.08331,27.02277,89.99635,26.86194), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.86774,27.08055,89.99635,26.95499), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.86774,27.08055,89.99635,26.95499), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.86774,27.08055,89.99635,26.95499), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.86774,27.08055,89.99635,26.95499), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.01553,27.08083,89.99635,27.16805), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.01553,27.08083,89.99635,27.16805), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.01553,27.08083,89.99635,27.16805), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.01553,27.08083,89.99635,27.16805), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.84331,27.1086,89.99635,27.08055), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.84331,27.1086,89.99635,27.08055), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.84331,27.1086,89.99635,27.08055), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.84331,27.1086,89.99635,27.08055), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.75055,27.14727,89.99635,27.25305), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.01526,27.16805,89.99635,27.08083), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.01526,27.16805,89.99635,27.08083), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.01526,27.16805,89.99635,27.08083), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.01526,27.16805,89.99635,27.08083), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.80635,27.25305,89.99635,27.1086), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.80635,27.25305,89.99635,27.1086), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.80635,27.25305,89.99635,27.1086), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.80635,27.25305,89.99635,27.1086), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.90137,27.28222,89.99635,27.32104), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.90137,27.28222,89.99635,27.32104), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.90137,27.28222,89.99635,27.32104), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.90137,27.28222,89.99635,27.32104), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.11441,27.28583,89.99635,26.92027), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.11441,27.28583,89.99635,26.92027), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.11441,27.28583,89.99635,26.92027), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.11441,27.28583,89.99635,26.92027), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.91788,27.32104,89.99635,26.98777), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.91788,27.32104,89.99635,26.98777), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.91788,27.32104,89.99635,26.98777), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.91788,27.32104,89.99635,26.98777), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.77525,27.4161,89.99635,27.45694), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.77525,27.4161,89.99635,27.45694), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.77525,27.4161,89.99635,27.45694), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.77525,27.4161,89.99635,27.45694), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.76553,27.45694,89.99635,27.4161), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.76553,27.45694,89.99635,27.4161), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.76553,27.45694,89.99635,27.4161), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.76553,27.45694,89.99635,27.4161), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((92.00775,27.47194,89.99635,27.16805), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((92.00775,27.47194,89.99635,27.16805), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((92.00775,27.47194,89.99635,27.16805), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((92.00775,27.47194,89.99635,27.16805), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((88.97108,27.47361,89.99635,26.98777), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((88.97108,27.47361,89.99635,26.98777), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((88.97108,27.47361,89.99635,26.98777), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((88.97108,27.47361,89.99635,26.98777), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.63135,27.53638,89.84192,27.94805), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.63135,27.53638,89.84192,27.94805), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.63135,27.53638,89.84192,27.94805), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.63135,27.53638,89.84192,27.94805), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.12579,27.56749,89.99635,26.82916), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.12579,27.56749,89.99635,26.82916), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.12579,27.56749,89.99635,26.82916), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.12579,27.56749,89.99635,26.82916), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.59802,27.62499,89.84192,27.66666), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.59802,27.62499,89.84192,27.66666), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.59802,27.62499,89.84192,27.66666), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.59802,27.62499,89.84192,27.66666), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.6008,27.66666,89.84192,27.62499), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.6008,27.66666,89.84192,27.62499), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.6008,27.66666,89.84192,27.62499), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.6008,27.66666,89.84192,27.62499), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.65955,27.76511,89.84192,27.94805), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.65955,27.76511,89.84192,27.94805), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.65955,27.76511,89.84192,27.94805), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.65955,27.76511,89.84192,27.94805), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.22552,27.795,89.99635,26.81472), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.22552,27.795,89.99635,26.81472), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.22552,27.795,89.99635,26.81472), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.22552,27.795,89.99635,26.81472), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.35997,27.87166,89.99635,26.85999), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.35997,27.87166,89.99635,26.85999), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.35997,27.87166,89.99635,26.85999), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.35997,27.87166,89.99635,26.85999), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.66692,27.88888,89.84192,27.76511), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.66692,27.88888,89.84192,27.76511), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.66692,27.88888,89.84192,27.76511), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.66692,27.88888,89.84192,27.76511), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.65831,27.94805,89.84192,27.76511), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.65831,27.94805,89.84192,27.76511), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.65831,27.94805,89.84192,27.76511), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.65831,27.94805,89.84192,27.76511), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.10886,27.97111,89.84192,27.97777), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.10886,27.97111,89.84192,27.97777), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.10886,27.97111,89.84192,27.97777), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.10886,27.97111,89.84192,27.97777), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.01581,27.97777,89.84192,27.97111), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.01581,27.97777,89.84192,27.97111), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.01581,27.97777,89.84192,27.97111), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.01581,27.97777,89.84192,27.97111), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.89693,28.05055,89.84192,27.97777), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.89693,28.05055,89.84192,27.97777), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.89693,28.05055,89.84192,27.97777), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.89693,28.05055,89.84192,27.97777), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.33803,28.05249,89.84192,28.09666), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.33803,28.05249,89.84192,28.09666), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.33803,28.05249,89.84192,28.09666), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.33803,28.05249,89.84192,28.09666), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.38246,28.07972,89.99635,26.9036), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.38246,28.07972,89.99635,26.9036), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.38246,28.07972,89.99635,26.9036), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.38246,28.07972,89.99635,26.9036), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((91.31497,28.09666,89.99635,26.77805), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((91.31497,28.09666,89.99635,26.77805), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((91.31497,28.09666,89.99635,26.77805), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((91.31497,28.09666,89.99635,26.77805), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.58609,28.14,89.99635,26.72694), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.58609,28.14,89.99635,26.72694), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.58609,28.14,89.99635,26.72694), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.58609,28.14,89.99635,26.72694), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.39609,28.23555,89.99635,26.9036), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.39609,28.23555,89.99635,26.9036), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.39609,28.23555,89.99635,26.9036), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.39609,28.23555,89.99635,26.9036), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.83165,28.24888,89.99635,26.70138), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.83165,28.24888,89.99635,26.70138), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.83165,28.24888,89.99635,26.70138), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.83165,28.24888,89.99635,26.70138), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((90.36607,28.25777,89.84192,28.07972), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((90.36607,28.25777,89.84192,28.07972), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((90.36607,28.25777,89.84192,28.07972), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((90.36607,28.25777,89.84192,28.07972), mapfile, tile_dir, 17, 17, "bt-bhutan")
    render_tiles((89.99635,28.32333,89.99635,26.73583), mapfile, tile_dir, 0, 11, "bt-bhutan")
    render_tiles((89.99635,28.32333,89.99635,26.73583), mapfile, tile_dir, 13, 13, "bt-bhutan")
    render_tiles((89.99635,28.32333,89.99635,26.73583), mapfile, tile_dir, 15, 15, "bt-bhutan")
    render_tiles((89.99635,28.32333,89.99635,26.73583), mapfile, tile_dir, 17, 17, "bt-bhutan")