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
    # Region: CM
    # Region Name: Cameroon

	render_tiles((16.07222,1.65417,16.16249,1.72667), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.16249,1.72667,16.01777,1.76111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.01777,1.76111,15.93277,1.78056), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.93277,1.78056,16.01777,1.76111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.87194,1.825,15.93277,1.78056), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.12416,1.87389,15.33722,1.92055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.33722,1.92055,16.12416,1.87389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.06055,1.97333,15.48638,1.97639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.48638,1.97639,16.06055,1.97333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.07222,1.98,15.48638,1.97639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.27277,1.99222,14.95388,2.00361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.95388,2.00361,14.88778,2.00389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.88778,2.00389,14.95388,2.00361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.14139,2.00833,14.88778,2.00389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.98,2.03194,15.15139,2.03972), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.15139,2.03972,14.98,2.03194), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.91083,2.05333,14.80527,2.06083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.80527,2.06083,14.91083,2.05333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.86139,2.1125,13.73,2.16028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.73,2.16028,14.52389,2.16055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.52389,2.16055,13.73,2.16028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.37833,2.16139,14.52389,2.16055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.29422,2.16407,16.08999,2.16639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.08999,2.16639,10.02291,2.16737), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.02291,2.16737,10.67361,2.16778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.67361,2.16778,10.02291,2.16737), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.34038,2.16861,10.67361,2.16778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.56783,2.20399,16.20641,2.2211), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.20641,2.2211,12.755,2.23278), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.755,2.23278,9.85083,2.2425), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.85083,2.2425,13.08555,2.24639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.08555,2.24639,9.85083,2.2425), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13,2.25611,13.08555,2.24639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.255,2.26639,12.57568,2.27433), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.57568,2.27433,12.16222,2.28028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.16222,2.28028,12.57568,2.27433), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.13194,2.28028,12.57568,2.27433), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.16444,2.30028,11.36837,2.30359), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.36837,2.30359,16.16444,2.30028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.62472,2.31306,12.33687,2.31728), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.33687,2.31728,11.62472,2.31306), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.80855,2.34607,12.33687,2.31728), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.09194,2.65639,9.845,2.66806), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.845,2.66806,16.09194,2.65639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.11516,2.70583,16.07805,2.70611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.07805,2.70611,16.11516,2.70583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.07166,2.79667,16.11277,2.82333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.11277,2.82333,16.07166,2.79667), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((16.11055,2.86417,16.11277,2.82333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.96667,3.08167,15.86944,3.10556), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.86944,3.10556,15.93889,3.10611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.93889,3.10611,15.86944,3.10556), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.90944,3.24083,15.93889,3.10611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.79527,3.41389,9.64278,3.53389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.64278,3.53389,9.68166,3.59444), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.68166,3.59444,9.62,3.60361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.62,3.60361,9.68166,3.59444), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.81027,3.63055,9.62,3.60361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.26027,3.67389,9.81027,3.63055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.60111,3.775,9.54305,3.81417), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.54305,3.81417,9.74361,3.82361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.74361,3.82361,9.54305,3.81417), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.66528,3.83361,9.59472,3.8375), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.59472,3.8375,9.66528,3.83361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.69278,3.85472,9.61777,3.86861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.61777,3.86861,9.70222,3.8725), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.70222,3.8725,9.61777,3.86861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.46389,3.90583,9.67861,3.90722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.67861,3.90722,9.35139,3.90861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.35139,3.90861,9.67861,3.90722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.07694,3.92056,9.35139,3.90861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.62583,3.94167,9.31,3.94222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.31,3.94222,9.62583,3.94167), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.7675,3.95583,9.61472,3.96028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.61472,3.96028,9.2325,3.96056), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.2325,3.96056,9.61472,3.96028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.4925,3.96611,9.2325,3.96056), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.52889,3.97806,9.275,3.97833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.275,3.97833,9.52889,3.97806), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.29666,3.9825,9.275,3.97833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.47666,4.00444,9.5575,4.01166), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.5575,4.01166,9.11694,4.01222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.11694,4.01222,9.5575,4.01166), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.20333,4.01333,9.42111,4.01389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.42111,4.01389,9.20333,4.01333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.31805,4.01917,9.42111,4.01389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.04389,4.02916,9.63,4.03028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.63,4.03028,15.04389,4.02916), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.19083,4.04694,9.48222,4.05917), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.48222,4.05917,15.19083,4.04694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.72416,4.09972,8.96805,4.10889), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.96805,4.10889,9.47444,4.11), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.47444,4.11,8.96805,4.10889), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.12289,4.1144,9.47444,4.11), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.10277,4.17194,8.9868,4.21408), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.9868,4.21408,15.10277,4.17194), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.04389,4.36222,8.905,4.38167), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.905,4.38167,15.04389,4.36222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.89222,4.47667,8.70194,4.49444), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.70194,4.49444,14.89222,4.47667), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.51694,4.51333,8.72472,4.51944), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.72472,4.51944,8.51694,4.51333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.86583,4.5375,8.78444,4.54055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.78444,4.54055,8.86583,4.5375), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.93361,4.54694,8.78444,4.54055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.80805,4.55528,8.93361,4.54694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.5025,4.56389,8.80805,4.55528), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.53333,4.5725,8.72139,4.58), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.72139,4.58,8.81639,4.58361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.81639,4.58361,8.72139,4.58), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.86639,4.58944,8.81639,4.58361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.73277,4.62305,8.51056,4.62861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.51056,4.62861,14.73277,4.62305), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.84167,4.64083,8.51056,4.62861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.68555,4.66639,8.6425,4.68667), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.6425,4.68667,8.68555,4.66639), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.59048,4.81051,8.63611,4.82694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.63611,4.82694,8.59048,4.81051), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.70305,4.85861,8.63611,4.82694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.62194,4.89472,14.70305,4.85861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.68759,5.11428,8.82401,5.188), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.82401,5.188,8.82805,5.23416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.82805,5.23416,8.82401,5.188), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.53166,5.29361,8.82805,5.23416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.60111,5.42055,14.53166,5.29361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.58916,5.60417,8.92028,5.6075), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.92028,5.6075,14.58916,5.60417), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.62444,5.69778,8.83361,5.71361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.83361,5.71361,14.62444,5.69778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.88888,5.78611,8.86067,5.81982), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.86067,5.81982,14.62166,5.83917), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.62166,5.83917,8.88,5.85472), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((8.88,5.85472,14.62166,5.83917), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.00639,5.91,14.56333,5.91083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.56333,5.91083,9.00639,5.91), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.485,5.92,14.56333,5.91083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.59277,5.93055,14.485,5.92), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.00333,5.94472,14.59277,5.93055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.41444,6.04417,14.48805,6.12972), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.48805,6.12972,9.295,6.20778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.295,6.20778,14.74027,6.2625), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.74027,6.2625,9.295,6.20778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.36333,6.32583,9.435,6.32778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.435,6.32778,9.36333,6.32583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.805,6.34666,9.435,6.32778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.46861,6.40444,11.15472,6.43333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.15472,6.43333,11.11694,6.44416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.11694,6.44416,9.57666,6.44694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.57666,6.44694,11.11694,6.44416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.38889,6.46028,9.57666,6.44694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.61222,6.52167,9.71139,6.52278), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.71139,6.52278,9.61222,6.52167), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.07333,6.59111,11.445,6.59694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.445,6.59694,11.07333,6.59111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.51472,6.60417,11.445,6.59694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.5625,6.6675,11.08222,6.69778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.08222,6.69778,11.5625,6.6675), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.95861,6.73361,11.08222,6.69778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.87472,6.77583,10.94333,6.77805), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.94333,6.77805,15.05916,6.77861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.05916,6.77861,10.94333,6.77805), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.01916,6.77861,10.94333,6.77805), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.58777,6.78527,15.05916,6.77861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((9.79555,6.80166,11.58777,6.78527), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.55472,6.82111,10.88277,6.82778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.88277,6.82778,11.55472,6.82111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.25639,6.87583,10.51333,6.87805), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.51333,6.87805,10.25639,6.87583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.5875,6.89222,10.205,6.90055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.205,6.90055,11.5875,6.89222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.85416,6.94694,10.205,6.90055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.16722,7.01917,11.79139,7.05083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.79139,7.05083,10.62,7.05361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.62,7.05361,11.79139,7.05083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.88666,7.07805,10.62,7.05361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.89416,7.12305,10.59611,7.13416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((10.59611,7.13416,11.89416,7.12305), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.23972,7.24611,11.74972,7.27055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.74972,7.27055,15.23972,7.24611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.46027,7.39583,11.86,7.40222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((11.86,7.40222,15.46027,7.39583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.42916,7.42889,11.86,7.40222), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.4991,7.52643,12.04361,7.57778), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.04361,7.57778,15.4991,7.52643), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.58416,7.68667,12.04361,7.73972), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.04361,7.73972,15.58,7.75833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.58,7.75833,15.50361,7.77417), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.50361,7.77417,15.58,7.75833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.44666,7.8775,12.22277,7.97472), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.22277,7.97472,15.44666,7.8775), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.25805,8.35166,12.24722,8.39389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.24722,8.39389,12.27527,8.42833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.27527,8.42833,12.35333,8.43027), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.35333,8.43027,12.27527,8.42833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.19277,8.49722,12.42166,8.51861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.42166,8.51861,15.19277,8.49722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.56889,8.60055,12.38639,8.61305), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.38639,8.61305,12.56889,8.60055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.49249,8.62888,12.38639,8.61305), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.6875,8.66083,12.49249,8.62888), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.72833,8.75944,12.79416,8.76583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.79416,8.76583,12.72833,8.75944), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.82472,8.81111,14.85277,8.81777), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.85277,8.81777,14.82472,8.81111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.82416,8.8475,14.85277,8.81777), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.84138,9.08555,14.4175,9.13416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.4175,9.13416,12.84138,9.08555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.90889,9.23527,14.4175,9.13416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.91361,9.34361,12.84893,9.36008), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((12.84893,9.36008,12.91361,9.34361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.05278,9.50833,13.15639,9.51583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.15639,9.51583,13.05278,9.50833), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.22111,9.55527,13.15639,9.51583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.96027,9.63444,13.25139,9.67694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.25139,9.67694,13.96027,9.63444), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.01,9.73,13.25139,9.67694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.10861,9.81139,14.01,9.73), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.22833,9.90972,14.77639,9.92111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.77639,9.92111,15.42222,9.92694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.42222,9.92694,14.77639,9.92111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.03139,9.94638,15.42222,9.92694), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.92027,9.97166,14.19055,9.98166), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.19055,9.98166,13.26555,9.98499), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.26555,9.98499,15.23277,9.98777), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.23277,9.98777,13.26555,9.98499), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.67887,9.99243,15.23277,9.98777), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.45833,9.99888,15.67887,9.99243), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.24389,10.03166,14.45833,9.99888), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.26639,10.08611,13.39805,10.11111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.39805,10.11111,13.26639,10.08611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.47055,10.19166,15.38778,10.22861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.38778,10.22861,13.45861,10.23888), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.45861,10.23888,15.38778,10.22861), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.13972,10.51916,13.57833,10.6825), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.57833,10.6825,15.08722,10.74722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.08722,10.74722,13.6375,10.75583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.6375,10.75583,15.08722,10.74722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.05666,10.80555,13.6375,10.75583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.07861,10.88944,15.05666,10.80555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((13.88666,11.17055,15.0175,11.18611), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.0175,11.18611,13.88666,11.17055), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.15805,11.23361,14.19583,11.25083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.19583,11.25083,14.15805,11.23361), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.00916,11.28333,14.19583,11.25083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.10555,11.48972,14.61777,11.50555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.61777,11.50555,15.10555,11.48972), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.14,11.525,14.61777,11.50555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.64639,11.57583,15.14,11.525), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.64111,11.64694,15.06277,11.68167), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.06277,11.68167,14.55833,11.71527), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.55833,11.71527,15.06277,11.68167), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.11111,11.78139,14.55833,11.71527), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.64944,11.915,15.05166,11.945), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.05166,11.945,14.64944,11.915), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.05361,12.03305,14.61916,12.03555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.61916,12.03555,15.05361,12.03305), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((15.04507,12.08408,14.61916,12.03555), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.90166,12.14277,14.67416,12.15083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.67416,12.15083,14.90166,12.14277), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.65722,12.18722,14.67416,12.15083), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.55277,12.23222,14.65722,12.18722), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.49083,12.33583,14.17389,12.38416), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.17389,12.38416,14.49083,12.33583), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.87555,12.44111,14.18805,12.44389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.18805,12.44389,14.87555,12.44111), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.18351,12.46948,14.18805,12.44389), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.70722,12.65388,14.74555,12.67333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.74555,12.67333,14.70722,12.65388), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.715,12.71028,14.74555,12.67333), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.55194,12.76361,14.715,12.71028), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.55458,12.82794,14.52012,12.86465), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.52012,12.86465,14.50805,12.8775), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.50805,12.8775,14.52012,12.86465), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.52611,12.96999,14.50805,12.8775), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.07499,13.08159,14.44305,13.08472), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.07499,13.08159,14.44305,13.08472), mapfile, tile_dir, 0, 11, "cm-cameroon")
	render_tiles((14.44305,13.08472,14.07499,13.08159), mapfile, tile_dir, 0, 11, "cm-cameroon")