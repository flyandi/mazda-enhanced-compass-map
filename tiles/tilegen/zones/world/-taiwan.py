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
    # Region: 
    # Region Name: Taiwan

	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.8408,21.90305,121.5594,21.98499), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.7227,21.92777,121.5594,21.95888), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.7227,21.92777,121.5594,21.95888), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.7227,21.92777,121.5594,21.95888), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.7227,21.92777,121.5594,21.95888), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.7514,21.95888,121.5594,21.92777), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.7514,21.95888,121.5594,21.92777), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.7514,21.95888,121.5594,21.92777), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.7514,21.95888,121.5594,21.92777), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.835,21.98499,121.5594,21.90305), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.835,21.98499,121.5594,21.90305), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.835,21.98499,121.5594,21.90305), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.835,21.98499,121.5594,21.90305), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.6747,22.02416,121.5594,22.14055), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.6747,22.02416,121.5594,22.14055), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.6747,22.02416,121.5594,22.14055), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.6747,22.02416,121.5594,22.14055), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.8825,22.03944,121.5594,22.21), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.8825,22.03944,121.5594,22.21), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.8825,22.03944,121.5594,22.21), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.8825,22.03944,121.5594,22.21), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.6844,22.14055,121.5594,22.02416), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.6844,22.14055,121.5594,22.02416), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.6844,22.14055,121.5594,22.02416), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.6844,22.14055,121.5594,22.02416), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.8722,22.21,120.8408,24.74), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.8722,22.21,120.8408,24.74), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.8722,22.21,120.8408,24.74), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.8722,22.21,120.8408,24.74), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.5761,22.36222,120.8408,24.51638), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.5761,22.36222,120.8408,24.51638), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.5761,22.36222,120.8408,24.51638), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.5761,22.36222,120.8408,24.51638), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.9358,22.47833,121.5594,22.03944), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.9358,22.47833,121.5594,22.03944), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.9358,22.47833,121.5594,22.03944), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.9358,22.47833,121.5594,22.03944), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.3314,22.53083,120.8408,24.05666), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.3314,22.53083,120.8408,24.05666), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.3314,22.53083,120.8408,24.05666), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.3314,22.53083,120.8408,24.05666), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.1769,22.86638,121.5594,23.01916), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.1769,22.86638,121.5594,23.01916), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.1769,22.86638,121.5594,23.01916), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.1769,22.86638,121.5594,23.01916), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.2333,22.87472,120.8408,25.11555), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.2333,22.87472,120.8408,25.11555), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.2333,22.87472,120.8408,25.11555), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.2333,22.87472,120.8408,25.11555), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.1711,23.01916,121.5594,22.86638), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.1711,23.01916,121.5594,22.86638), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.1711,23.01916,121.5594,22.86638), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.1711,23.01916,121.5594,22.86638), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.0597,23.03777,121.5594,23.07194), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.0597,23.03777,121.5594,23.07194), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.0597,23.03777,121.5594,23.07194), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.0597,23.03777,121.5594,23.07194), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.0533,23.07194,121.5594,23.03777), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.0533,23.07194,121.5594,23.03777), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.0533,23.07194,121.5594,23.03777), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.0533,23.07194,121.5594,23.03777), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.0991,23.08833,120.8408,23.61388), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.0991,23.08833,120.8408,23.61388), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.0991,23.08833,120.8408,23.61388), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.0991,23.08833,120.8408,23.61388), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.0739,23.20722,121.5594,23.03777), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.0739,23.20722,121.5594,23.03777), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.0739,23.20722,121.5594,23.03777), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.0739,23.20722,121.5594,23.03777), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.4547,23.33305,120.8408,25.12666), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.4547,23.33305,120.8408,25.12666), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.4547,23.33305,120.8408,25.12666), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.4547,23.33305,120.8408,25.12666), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.1466,23.38499,121.5594,23.01916), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.1466,23.38499,121.5594,23.01916), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.1466,23.38499,121.5594,23.01916), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.1466,23.38499,121.5594,23.01916), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.1111,23.61388,121.5594,23.08833), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.1111,23.61388,121.5594,23.08833), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.1111,23.61388,121.5594,23.08833), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.1111,23.61388,121.5594,23.08833), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.5964,23.89999,120.8408,24.08583), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.5964,23.89999,120.8408,24.08583), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.5964,23.89999,120.8408,24.08583), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.5964,23.89999,120.8408,24.08583), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.3828,24.05666,121.5594,22.53083), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.3828,24.05666,121.5594,22.53083), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.3828,24.05666,121.5594,22.53083), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.3828,24.05666,121.5594,22.53083), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.6075,24.08583,120.8408,25.2575), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.6075,24.08583,120.8408,25.2575), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.6075,24.08583,120.8408,25.2575), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.6075,24.08583,120.8408,25.2575), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.7016,24.21444,120.8408,25.17222), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.7016,24.21444,120.8408,25.17222), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.7016,24.21444,120.8408,25.17222), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.7016,24.21444,120.8408,25.17222), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.7664,24.35999,120.8408,24.75666), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.7664,24.35999,120.8408,24.75666), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.7664,24.35999,120.8408,24.75666), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.7664,24.35999,120.8408,24.75666), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.6591,24.51638,121.5594,22.02416), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.6591,24.51638,121.5594,22.02416), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.6591,24.51638,121.5594,22.02416), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.6591,24.51638,121.5594,22.02416), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.8553,24.52916,120.8408,24.86694), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.8553,24.52916,120.8408,24.86694), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.8553,24.52916,120.8408,24.86694), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.8553,24.52916,120.8408,24.86694), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.82,24.62694,120.8408,24.86694), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.82,24.62694,120.8408,24.86694), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.82,24.62694,120.8408,24.86694), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.82,24.62694,120.8408,24.86694), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.7514,24.6475,121.5594,21.92777), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.7514,24.6475,121.5594,21.92777), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.7514,24.6475,121.5594,21.92777), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.7514,24.6475,121.5594,21.92777), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.8716,24.74,121.5594,22.21), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.8716,24.74,121.5594,22.21), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.8716,24.74,121.5594,22.21), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.8716,24.74,121.5594,22.21), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.7997,24.75666,120.8408,24.62694), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.7997,24.75666,120.8408,24.62694), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.7997,24.75666,120.8408,24.62694), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.7997,24.75666,120.8408,24.62694), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.8283,24.86694,120.8408,24.62694), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.8283,24.86694,120.8408,24.62694), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.8283,24.86694,120.8408,24.62694), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.8283,24.86694,120.8408,24.62694), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((120.9986,24.99722,121.5594,22.47833), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((120.9986,24.99722,121.5594,22.47833), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((120.9986,24.99722,121.5594,22.47833), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((120.9986,24.99722,121.5594,22.47833), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.9992,25.01083,120.8408,25.01972), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.9992,25.01083,120.8408,25.01972), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.9992,25.01083,120.8408,25.01972), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.9992,25.01083,120.8408,25.01972), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.9161,25.01972,120.8408,25.11583), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.9161,25.01972,120.8408,25.11583), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.9161,25.01972,120.8408,25.11583), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.9161,25.01972,120.8408,25.11583), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.2236,25.11555,121.5594,22.87472), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.2236,25.11555,121.5594,22.87472), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.2236,25.11555,121.5594,22.87472), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.2236,25.11555,121.5594,22.87472), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.8925,25.11583,120.8408,25.01972), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.8925,25.11583,120.8408,25.01972), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.8925,25.11583,120.8408,25.01972), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.8925,25.11583,120.8408,25.01972), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.2857,25.12197,121.5594,22.87472), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.2857,25.12197,121.5594,22.87472), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.2857,25.12197,121.5594,22.87472), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.2857,25.12197,121.5594,22.87472), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.4372,25.12666,121.5594,23.33305), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.4372,25.12666,121.5594,23.33305), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.4372,25.12666,121.5594,23.33305), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.4372,25.12666,121.5594,23.33305), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.6744,25.17222,120.8408,24.21444), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.6744,25.17222,120.8408,24.21444), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.6744,25.17222,120.8408,24.21444), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.6744,25.17222,120.8408,24.21444), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.4019,25.17333,120.8408,25.12666), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.4019,25.17333,120.8408,25.12666), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.4019,25.17333,120.8408,25.12666), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.4019,25.17333,120.8408,25.12666), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.6108,25.2575,120.8408,24.08583), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.6108,25.2575,120.8408,24.08583), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.6108,25.2575,120.8408,24.08583), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.6108,25.2575,120.8408,24.08583), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.4761,25.26888,121.5594,23.33305), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.4761,25.26888,121.5594,23.33305), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.4761,25.26888,121.5594,23.33305), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.4761,25.26888,121.5594,23.33305), mapfile, tile_dir, 17, 17, "-taiwan")
	render_tiles((121.5594,25.28416,120.8408,23.89999), mapfile, tile_dir, 0, 11, "-taiwan")
	render_tiles((121.5594,25.28416,120.8408,23.89999), mapfile, tile_dir, 13, 13, "-taiwan")
	render_tiles((121.5594,25.28416,120.8408,23.89999), mapfile, tile_dir, 15, 15, "-taiwan")
	render_tiles((121.5594,25.28416,120.8408,23.89999), mapfile, tile_dir, 17, 17, "-taiwan")