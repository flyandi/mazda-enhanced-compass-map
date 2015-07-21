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
    # Region: BJ
    # Region Name: Benin

    render_tiles((1.63417,6.21068,2.8435,8.35777), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.63417,6.21068,2.8435,8.35777), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.63417,6.21068,2.8435,8.35777), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.63417,6.21068,2.8435,8.35777), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.72306,6.23666,1.63417,11.42666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.72306,6.23666,1.63417,11.42666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.72306,6.23666,1.63417,11.42666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.72306,6.23666,1.63417,11.42666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.79996,6.28093,2.8435,6.3925), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.79996,6.28093,2.8435,6.3925), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.79996,6.28093,2.8435,6.3925), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.79996,6.28093,2.8435,6.3925), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.29444,6.325,1.63417,11.67916), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.29444,6.325,1.63417,11.67916), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.29444,6.325,1.63417,11.67916), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.29444,6.325,1.63417,11.67916), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.7187,6.36446,2.8435,6.95444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.7187,6.36446,2.8435,6.95444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.7187,6.36446,2.8435,6.95444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.7187,6.36446,2.8435,6.95444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.77139,6.3925,1.63417,11.42666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.77139,6.3925,1.63417,11.42666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.77139,6.3925,1.63417,11.42666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.77139,6.3925,1.63417,11.42666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.74167,6.62194,2.8435,7.80861), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.74167,6.62194,2.8435,7.80861), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.74167,6.62194,2.8435,7.80861), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.74167,6.62194,2.8435,7.80861), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.57528,6.67972,2.8435,9.11694), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.57528,6.67972,2.8435,9.11694), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.57528,6.67972,2.8435,9.11694), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.57528,6.67972,2.8435,9.11694), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.79889,6.68861,2.8435,7.04), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.79889,6.68861,2.8435,7.04), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.79889,6.68861,2.8435,7.04), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.79889,6.68861,2.8435,7.04), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.62305,6.75778,2.8435,8.54805), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.62305,6.75778,2.8435,8.54805), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.62305,6.75778,2.8435,8.54805), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.62305,6.75778,2.8435,8.54805), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.72528,6.95444,2.8435,8.08305), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.72528,6.95444,2.8435,8.08305), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.72528,6.95444,2.8435,8.08305), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.72528,6.95444,2.8435,8.08305), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.54861,6.99528,2.8435,6.67972), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.54861,6.99528,2.8435,6.67972), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.54861,6.99528,2.8435,6.67972), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.54861,6.99528,2.8435,6.67972), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.6425,6.99555,2.8435,7.26555), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.6425,6.99555,2.8435,7.26555), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.6425,6.99555,2.8435,7.26555), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.6425,6.99555,2.8435,7.26555), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.79833,7.04,2.8435,6.68861), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.79833,7.04,2.8435,6.68861), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.79833,7.04,2.8435,6.68861), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.79833,7.04,2.8435,6.68861), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.74778,7.09916,2.8435,7.80861), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.74778,7.09916,2.8435,7.80861), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.74778,7.09916,2.8435,7.80861), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.74778,7.09916,2.8435,7.80861), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.77861,7.13444,1.63417,12.37749), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.77861,7.13444,1.63417,12.37749), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.77861,7.13444,1.63417,12.37749), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.77861,7.13444,1.63417,12.37749), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.64444,7.26555,2.8435,6.99555), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.64444,7.26555,2.8435,6.99555), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.64444,7.26555,2.8435,6.99555), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.64444,7.26555,2.8435,6.99555), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.80722,7.41722,2.8435,6.68861), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.80722,7.41722,2.8435,6.68861), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.80722,7.41722,2.8435,6.68861), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.80722,7.41722,2.8435,6.68861), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.7575,7.41944,2.8435,8.58805), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.7575,7.41944,2.8435,8.58805), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.7575,7.41944,2.8435,8.58805), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.7575,7.41944,2.8435,8.58805), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.76278,7.5475,2.8435,7.41944), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.76278,7.5475,2.8435,7.41944), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.76278,7.5475,2.8435,7.41944), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.76278,7.5475,2.8435,7.41944), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.73333,7.63972,2.8435,8.08305), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.73333,7.63972,2.8435,8.08305), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.73333,7.63972,2.8435,8.08305), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.73333,7.63972,2.8435,8.08305), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.74222,7.80861,2.8435,6.62194), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.74222,7.80861,2.8435,6.62194), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.74222,7.80861,2.8435,6.62194), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.74222,7.80861,2.8435,6.62194), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.68213,7.89365,1.63417,12.29028), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.68213,7.89365,1.63417,12.29028), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.68213,7.89365,1.63417,12.29028), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.68213,7.89365,1.63417,12.29028), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.72583,8.08305,2.8435,6.95444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.72583,8.08305,2.8435,6.95444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.72583,8.08305,2.8435,6.95444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.72583,8.08305,2.8435,6.95444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.63472,8.35777,2.8435,6.21068), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.63472,8.35777,2.8435,6.21068), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.63472,8.35777,2.8435,6.21068), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.63472,8.35777,2.8435,6.21068), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.61305,8.37472,1.63417,11.38833), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.61305,8.37472,1.63417,11.38833), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.61305,8.37472,1.63417,11.38833), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.61305,8.37472,1.63417,11.38833), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.66083,8.49694,2.8435,7.26555), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.66083,8.49694,2.8435,7.26555), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.66083,8.49694,2.8435,7.26555), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.66083,8.49694,2.8435,7.26555), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.62472,8.54805,2.8435,6.75778), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.62472,8.54805,2.8435,6.75778), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.62472,8.54805,2.8435,6.75778), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.62472,8.54805,2.8435,6.75778), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.75583,8.58805,2.8435,7.41944), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.75583,8.58805,2.8435,7.41944), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.75583,8.58805,2.8435,7.41944), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.75583,8.58805,2.8435,7.41944), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.62778,8.88111,2.8435,8.54805), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.62778,8.88111,2.8435,8.54805), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.62778,8.88111,2.8435,8.54805), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.62778,8.88111,2.8435,8.54805), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.78833,9.05416,2.8435,7.13444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.78833,9.05416,2.8435,7.13444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.78833,9.05416,2.8435,7.13444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.78833,9.05416,2.8435,7.13444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.9875,9.06111,2.8435,9.09055), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.9875,9.06111,2.8435,9.09055), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.9875,9.06111,2.8435,9.09055), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.9875,9.06111,2.8435,9.09055), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.095,9.09055,1.63417,9.44111), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.095,9.09055,1.63417,9.44111), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.095,9.09055,1.63417,9.44111), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.095,9.09055,1.63417,9.44111), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.59639,9.11694,1.63417,11.38833), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.59639,9.11694,1.63417,11.38833), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.59639,9.11694,1.63417,11.38833), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.59639,9.11694,1.63417,11.38833), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.47361,9.25639,1.63417,11.46416), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.47361,9.25639,1.63417,11.46416), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.47361,9.25639,1.63417,11.46416), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.47361,9.25639,1.63417,11.46416), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.17028,9.27444,1.63417,12.06388), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.17028,9.27444,1.63417,12.06388), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.17028,9.27444,1.63417,12.06388), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.17028,9.27444,1.63417,12.06388), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.14167,9.44111,2.8435,9.27444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.14167,9.44111,2.8435,9.27444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.14167,9.44111,2.8435,9.27444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.14167,9.44111,2.8435,9.27444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.3375,9.5425,1.63417,9.99527), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.3375,9.5425,1.63417,9.99527), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.3375,9.5425,1.63417,9.99527), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.3375,9.5425,1.63417,9.99527), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.36833,9.59666,1.63417,9.99527), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.36833,9.59666,1.63417,9.99527), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.36833,9.59666,1.63417,9.99527), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.36833,9.59666,1.63417,9.99527), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.26333,9.63583,1.63417,12.01722), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.26333,9.63583,1.63417,12.01722), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.26333,9.63583,1.63417,12.01722), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.26333,9.63583,1.63417,12.01722), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.31722,9.63611,1.63417,11.89), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.31722,9.63611,1.63417,11.89), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.31722,9.63611,1.63417,11.89), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.31722,9.63611,1.63417,11.89), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.36333,9.68194,1.63417,9.80916), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.36333,9.68194,1.63417,9.80916), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.36333,9.68194,1.63417,9.80916), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.36333,9.68194,1.63417,9.80916), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.33111,9.75583,1.63417,9.63611), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.33111,9.75583,1.63417,9.63611), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.33111,9.75583,1.63417,9.63611), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.33111,9.75583,1.63417,9.63611), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.34667,9.80916,1.63417,9.75583), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.34667,9.80916,1.63417,9.75583), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.34667,9.80916,1.63417,9.75583), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.34667,9.80916,1.63417,9.75583), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.52528,9.84416,1.63417,11.75694), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.52528,9.84416,1.63417,11.75694), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.52528,9.84416,1.63417,11.75694), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.52528,9.84416,1.63417,11.75694), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.60944,9.94805,1.63417,11.69169), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.60944,9.94805,1.63417,11.69169), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.60944,9.94805,1.63417,11.69169), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.60944,9.94805,1.63417,11.69169), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.355,9.99527,1.63417,9.59666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.355,9.99527,1.63417,9.59666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.355,9.99527,1.63417,9.59666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.355,9.99527,1.63417,9.59666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.68444,10.15111,1.63417,10.44972), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.68444,10.15111,1.63417,10.44972), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.68444,10.15111,1.63417,10.44972), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.68444,10.15111,1.63417,10.44972), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.58194,10.27527,1.63417,11.75694), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.58194,10.27527,1.63417,11.75694), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.58194,10.27527,1.63417,11.75694), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.58194,10.27527,1.63417,11.75694), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((0.77667,10.37666,1.63417,10.52444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((0.77667,10.37666,1.63417,10.52444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((0.77667,10.37666,1.63417,10.52444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((0.77667,10.37666,1.63417,10.52444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.78944,10.40277,1.63417,10.79444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.78944,10.40277,1.63417,10.79444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.78944,10.40277,1.63417,10.79444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.78944,10.40277,1.63417,10.79444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.63722,10.41166,1.63417,9.94805), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.63722,10.41166,1.63417,9.94805), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.63722,10.41166,1.63417,9.94805), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.63722,10.41166,1.63417,9.94805), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.68889,10.44972,1.63417,10.15111), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.68889,10.44972,1.63417,10.15111), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.68889,10.44972,1.63417,10.15111), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.68889,10.44972,1.63417,10.15111), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((0.78528,10.52444,1.63417,10.37666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((0.78528,10.52444,1.63417,10.37666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((0.78528,10.52444,1.63417,10.37666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((0.78528,10.52444,1.63417,10.37666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.855,10.585,1.63417,10.70305), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.855,10.585,1.63417,10.70305), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.855,10.585,1.63417,10.70305), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.855,10.585,1.63417,10.70305), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.84667,10.70305,1.63417,10.585), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.84667,10.70305,1.63417,10.585), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.84667,10.70305,1.63417,10.585), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.84667,10.70305,1.63417,10.585), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.75389,10.79444,1.63417,11.11666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.75389,10.79444,1.63417,11.11666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.75389,10.79444,1.63417,11.11666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.75389,10.79444,1.63417,11.11666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((0.91744,10.99576,1.63417,10.99583), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((0.91744,10.99576,1.63417,10.99583), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((0.91744,10.99576,1.63417,10.99583), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((0.91744,10.99576,1.63417,10.99583), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((0.96778,10.99583,1.63417,11.08027), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((0.96778,10.99583,1.63417,11.08027), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((0.96778,10.99583,1.63417,11.08027), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((0.96778,10.99583,1.63417,11.08027), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.10278,11.04027,1.63417,11.24944), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.10278,11.04027,1.63417,11.24944), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.10278,11.04027,1.63417,11.24944), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.10278,11.04027,1.63417,11.24944), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((0.97889,11.08027,1.63417,10.99583), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((0.97889,11.08027,1.63417,10.99583), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((0.97889,11.08027,1.63417,10.99583), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((0.97889,11.08027,1.63417,10.99583), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.73917,11.11666,1.63417,10.79444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.73917,11.11666,1.63417,10.79444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.73917,11.11666,1.63417,10.79444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.73917,11.11666,1.63417,10.79444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.69417,11.135,1.63417,10.44972), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.69417,11.135,1.63417,10.44972), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.69417,11.135,1.63417,10.44972), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.69417,11.135,1.63417,10.44972), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.06389,11.13972,1.63417,11.04027), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.06389,11.13972,1.63417,11.04027), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.06389,11.13972,1.63417,11.04027), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.06389,11.13972,1.63417,11.04027), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.15639,11.16278,1.63417,11.27722), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.15639,11.16278,1.63417,11.27722), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.15639,11.16278,1.63417,11.27722), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.15639,11.16278,1.63417,11.27722), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.13278,11.24944,1.63417,11.27722), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.13278,11.24944,1.63417,11.27722), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.13278,11.24944,1.63417,11.27722), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.13278,11.24944,1.63417,11.27722), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.27278,11.25916,1.63417,11.29139), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.27278,11.25916,1.63417,11.29139), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.27278,11.25916,1.63417,11.29139), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.27278,11.25916,1.63417,11.29139), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.15389,11.27722,1.63417,11.16278), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.15389,11.27722,1.63417,11.16278), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.15389,11.27722,1.63417,11.16278), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.15389,11.27722,1.63417,11.16278), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.30861,11.29139,1.63417,9.5425), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.30861,11.29139,1.63417,9.5425), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.30861,11.29139,1.63417,9.5425), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.30861,11.29139,1.63417,9.5425), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.60917,11.38833,2.8435,8.37472), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.60917,11.38833,2.8435,8.37472), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.60917,11.38833,2.8435,8.37472), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.60917,11.38833,2.8435,8.37472), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.01917,11.42527,1.63417,11.42858), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.01917,11.42527,1.63417,11.42858), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.01917,11.42527,1.63417,11.42858), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.01917,11.42527,1.63417,11.42858), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.76611,11.42666,2.8435,6.3925), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.76611,11.42666,2.8435,6.3925), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.76611,11.42666,2.8435,6.3925), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.76611,11.42666,2.8435,6.3925), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.90268,11.42858,2.8435,6.28093), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.90268,11.42858,2.8435,6.28093), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.90268,11.42858,2.8435,6.28093), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.90268,11.42858,2.8435,6.28093), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.475,11.42972,1.63417,11.85583), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.475,11.42972,1.63417,11.85583), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.475,11.42972,1.63417,11.85583), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.475,11.42972,1.63417,11.85583), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.3975,11.43972,1.63417,9.59666), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.3975,11.43972,1.63417,9.59666), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.3975,11.43972,1.63417,9.59666), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.3975,11.43972,1.63417,9.59666), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((1.5,11.46416,2.8435,9.25639), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((1.5,11.46416,2.8435,9.25639), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((1.5,11.46416,2.8435,9.25639), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((1.5,11.46416,2.8435,9.25639), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.31386,11.67916,2.8435,6.325), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.31386,11.67916,2.8435,6.325), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.31386,11.67916,2.8435,6.325), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.31386,11.67916,2.8435,6.325), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.60549,11.69169,1.63417,9.94805), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.60549,11.69169,1.63417,9.94805), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.60549,11.69169,1.63417,9.94805), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.60549,11.69169,1.63417,9.94805), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.55889,11.75694,1.63417,10.27527), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.55889,11.75694,1.63417,10.27527), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.55889,11.75694,1.63417,10.27527), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.55889,11.75694,1.63417,10.27527), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.33805,11.76472,1.63417,11.67916), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.33805,11.76472,1.63417,11.67916), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.33805,11.76472,1.63417,11.67916), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.33805,11.76472,1.63417,11.67916), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.46889,11.85583,1.63417,11.42972), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.46889,11.85583,1.63417,11.42972), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.46889,11.85583,1.63417,11.42972), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.46889,11.85583,1.63417,11.42972), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.40156,11.88988,1.63417,11.90444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.31222,11.89,1.63417,9.63611), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.31222,11.89,1.63417,9.63611), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.31222,11.89,1.63417,9.63611), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.31222,11.89,1.63417,9.63611), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.41,11.90444,1.63417,11.88988), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.41,11.90444,1.63417,11.88988), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.41,11.90444,1.63417,11.88988), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.41,11.90444,1.63417,11.88988), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.39028,11.93333,1.63417,12.16639), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.39028,11.93333,1.63417,12.16639), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.39028,11.93333,1.63417,12.16639), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.39028,11.93333,1.63417,12.16639), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.47056,11.97777,1.63417,12.02416), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.47056,11.97777,1.63417,12.02416), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.47056,11.97777,1.63417,12.02416), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.47056,11.97777,1.63417,12.02416), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.25389,12.01722,1.63417,9.63583), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.25389,12.01722,1.63417,9.63583), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.25389,12.01722,1.63417,9.63583), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.25389,12.01722,1.63417,9.63583), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.44528,12.02416,1.63417,11.97777), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.44528,12.02416,1.63417,11.97777), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.44528,12.02416,1.63417,11.97777), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.44528,12.02416,1.63417,11.97777), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((3.19555,12.06388,2.8435,9.27444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((3.19555,12.06388,2.8435,9.27444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((3.19555,12.06388,2.8435,9.27444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((3.19555,12.06388,2.8435,9.27444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.38361,12.16639,1.63417,12.24027), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.38361,12.16639,1.63417,12.24027), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.38361,12.16639,1.63417,12.24027), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.38361,12.16639,1.63417,12.24027), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.37806,12.24027,1.63417,12.16639), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.37806,12.24027,1.63417,12.16639), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.37806,12.24027,1.63417,12.16639), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.37806,12.24027,1.63417,12.16639), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.68639,12.29028,2.8435,7.89365), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.68639,12.29028,2.8435,7.89365), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.68639,12.29028,2.8435,7.89365), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.68639,12.29028,2.8435,7.89365), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.64528,12.30472,2.8435,7.89365), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.64528,12.30472,2.8435,7.89365), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.64528,12.30472,2.8435,7.89365), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.64528,12.30472,2.8435,7.89365), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.77111,12.37749,2.8435,7.13444), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.77111,12.37749,2.8435,7.13444), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.77111,12.37749,2.8435,7.13444), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.77111,12.37749,2.8435,7.13444), mapfile, tile_dir, 17, 17, "bj-benin")
    render_tiles((2.8435,12.39332,2.8435,7.41722), mapfile, tile_dir, 0, 11, "bj-benin")
    render_tiles((2.8435,12.39332,2.8435,7.41722), mapfile, tile_dir, 13, 13, "bj-benin")
    render_tiles((2.8435,12.39332,2.8435,7.41722), mapfile, tile_dir, 15, 15, "bj-benin")
    render_tiles((2.8435,12.39332,2.8435,7.41722), mapfile, tile_dir, 17, 17, "bj-benin")