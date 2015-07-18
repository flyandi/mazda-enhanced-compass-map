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
    # Region: CF
    # Region Name: Central African Republic

	render_tiles((16.20641,2.2211,16.16444,2.30028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.16444,2.30028,16.20641,2.2211), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.09194,2.65639,16.11516,2.70583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.11516,2.70583,16.07805,2.70611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.07805,2.70611,16.11516,2.70583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.07166,2.79667,16.11277,2.82333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.11277,2.82333,16.50166,2.84944), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.50166,2.84944,16.11055,2.86417), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.11055,2.86417,16.50166,2.84944), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.86944,3.10556,15.93889,3.10611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.93889,3.10611,15.86944,3.10556), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.48277,3.15667,15.93889,3.10611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.626,3.47886,16.58888,3.48194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.58888,3.48194,18.18388,3.48278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.18388,3.48278,16.58888,3.48194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.23388,3.49917,18.18388,3.48278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.86194,3.52444,16.98777,3.53528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.98777,3.53528,17.86499,3.53611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.86499,3.53611,16.98777,3.53528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.98888,3.53917,17.86499,3.53611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.69916,3.54528,18.14388,3.55083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.14388,3.55083,18.54888,3.55333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.54888,3.55333,18.14388,3.55083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.87666,3.56583,18.04944,3.56639), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.04944,3.56639,16.87666,3.56583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.07722,3.57667,17.93888,3.5775), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.93888,3.5775,17.07722,3.57667), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.39805,3.5775,17.07722,3.57667), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.2725,3.58306,17.93888,3.5775), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.53333,3.59889,18.2725,3.58306), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.3561,3.61528,17.75999,3.63055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.75999,3.63055,18.48138,3.64194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.48138,3.64194,17.57944,3.64833), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.57944,3.64833,18.48138,3.64194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.26027,3.67389,17.41388,3.67972), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.41388,3.67972,15.26027,3.67389), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.4936,3.71083,17.41388,3.67972), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.59499,3.76917,17.4936,3.71083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.07694,3.92056,18.64888,4.0025), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.64888,4.0025,15.04389,4.02916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.04389,4.02916,15.19083,4.04694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.19083,4.04694,15.04389,4.02916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.12289,4.1144,22.39138,4.12861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.39138,4.12861,22.25972,4.13416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.25972,4.13416,22.39138,4.12861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.46805,4.15528,15.10277,4.17194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.10277,4.17194,22.46805,4.15528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.59138,4.23111,21.92582,4.23416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.92582,4.23416,18.59138,4.23111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.55194,4.24583,21.92582,4.23416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.35527,4.28,18.53777,4.29916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.53777,4.29916,21.17221,4.30389), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.6586,4.29916,21.17221,4.30389), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.17221,4.30389,18.53777,4.29916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.28277,4.33417,18.54194,4.33555), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.54194,4.33555,21.28277,4.33417), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.04389,4.36222,22.61305,4.36916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.61305,4.36916,18.71471,4.37028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.71471,4.37028,18.58221,4.37055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.58221,4.37055,18.71471,4.37028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.61472,4.40805,22.58722,4.41166), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.58722,4.41166,20.5761,4.41472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.5761,4.41472,22.58722,4.41166), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.77666,4.42333,20.98444,4.42666), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.98444,4.42666,18.77666,4.42333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.8386,4.44916,20.98444,4.42666), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.89222,4.47667,22.60138,4.47722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.60138,4.47722,14.89222,4.47667), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.69416,4.49305,22.60138,4.47722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.45638,4.51917,18.8161,4.52639), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.8161,4.52639,20.45638,4.51917), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.42638,4.59194,22.72666,4.62278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.72666,4.62278,14.73277,4.62305), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.73277,4.62305,22.72666,4.62278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.27222,4.62472,14.73277,4.62305), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.44999,4.65889,23.27222,4.62472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.85583,4.70694,22.79277,4.72361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.79277,4.72361,23.18666,4.72444), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.18666,4.72444,22.79277,4.72361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.37027,4.72583,23.18666,4.72444), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.0225,4.74305,20.37027,4.72583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.70916,4.77583,20.28111,4.79222), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.28111,4.79222,23.70916,4.77583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.89083,4.81917,22.97916,4.82861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.97916,4.82861,22.89083,4.81917), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.70305,4.85861,22.97916,4.82861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.15194,4.9,19.08888,4.91805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.08888,4.91805,24.10555,4.92), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.67277,4.91805,24.10555,4.92), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.10555,4.92,19.08888,4.91805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.07582,4.94722,24.10555,4.92), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.97721,4.98778,25.11277,4.99805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.11277,4.99805,25.22777,5.00694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.22777,5.00694,25.11277,4.99805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.45586,5.01714,25.22777,5.00694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.28411,5.02781,26.87249,5.03139), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.87249,5.03139,19.28411,5.02781), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.40194,5.03527,26.87249,5.03139), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.86583,5.04167,26.50055,5.04472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.50055,5.04472,19.86583,5.04167), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.3236,5.0575,24.35499,5.06), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.35499,5.06,25.3236,5.0575), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.43166,5.06528,24.35499,5.06), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.41944,5.07472,24.52555,5.07722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.52555,5.07722,27.41944,5.07472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.64638,5.08111,27.45805,5.08361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.45805,5.08361,26.64638,5.08111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.83833,5.08833,27.45805,5.08361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.4536,5.10639,14.68759,5.11428), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.68759,5.11428,24.39416,5.11555), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.39416,5.11555,14.68759,5.11428), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.39833,5.12,24.39416,5.11555), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.94527,5.14611,19.56638,5.15028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.56638,5.15028,26.94527,5.14611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.90527,5.16583,19.56638,5.15028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.03111,5.19111,27.09638,5.20278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.31055,5.19111,27.09638,5.20278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.09638,5.20278,26.03111,5.19111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.87582,5.2175,26.1636,5.23), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.1636,5.23,25.97332,5.23167), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.97332,5.23167,26.1636,5.23), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.29555,5.23583,25.79583,5.23639), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.79583,5.23639,27.29555,5.23583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.08833,5.24055,25.79583,5.23639), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.53166,5.29361,25.36194,5.31472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.36194,5.31472,14.53166,5.29361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.52083,5.3475,25.36194,5.31472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.60111,5.42055,27.23777,5.43889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.23777,5.43889,14.60111,5.42055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.28194,5.54889,14.58916,5.60417), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.58916,5.60417,27.28194,5.54889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.62444,5.69778,27.14277,5.77194), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((27.14277,5.77194,14.62166,5.83917), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.62166,5.83917,26.82944,5.905), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.82944,5.905,14.56333,5.91083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.56333,5.91083,26.82944,5.905), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.485,5.92,14.56333,5.91083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.59277,5.93055,14.485,5.92), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.80861,5.98,26.61221,6.01444), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.61221,6.01444,14.41444,6.04417), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.41444,6.04417,26.61221,6.01444), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.43749,6.07778,26.51416,6.10972), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.51416,6.10972,26.45082,6.11611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.45082,6.11611,26.51416,6.10972), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.48805,6.12972,26.45082,6.11611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.52361,6.17361,14.48805,6.12972), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.52361,6.22111,14.74027,6.2625), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.74027,6.2625,26.52361,6.22111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.805,6.34666,26.30138,6.3925), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.30138,6.3925,14.805,6.34666), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.3061,6.44944,26.30138,6.3925), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.38499,6.58917,26.39666,6.65528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.39666,6.65528,26.38499,6.58917), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((14.95861,6.73361,15.05916,6.77861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.05916,6.77861,14.95861,6.73361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.09444,6.85028,15.05916,6.77861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((26.04833,6.99833,26.09444,6.85028), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.23972,7.24611,25.37416,7.33944), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.37416,7.33944,15.46027,7.39583), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.46027,7.39583,25.3311,7.42528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.3311,7.42528,15.42916,7.42889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.42916,7.42889,25.3311,7.42528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.78277,7.45778,15.42916,7.42889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.20694,7.4975,15.51861,7.51833), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.51861,7.51833,15.68916,7.52389), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.68916,7.52389,15.4991,7.52643), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((15.4991,7.52643,15.68916,7.52389), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.81742,7.54597,15.4991,7.52643), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.18999,7.59278,16.25138,7.63722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.25138,7.63722,16.94666,7.64528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.94666,7.64528,25.2961,7.65), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.2961,7.65,16.94666,7.64528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.62999,7.67694,16.40447,7.67884), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.40447,7.67884,16.62999,7.67694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.16582,7.72805,16.62916,7.75667), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.62916,7.75667,16.4036,7.78), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.4036,7.78,25.28638,7.78333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.28638,7.78333,16.4036,7.78), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.56333,7.815,25.28638,7.78333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.22555,7.87278,16.56444,7.87333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((16.56444,7.87333,25.22555,7.87278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.39138,7.87778,16.56444,7.87333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.14888,7.89305,17.39138,7.87778), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((25.05388,7.92111,25.14888,7.89305), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.8836,7.95833,17.70847,7.98498), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((17.70847,7.98498,17.8836,7.95833), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.0461,8.01667,24.96999,8.02333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.96999,8.02333,18.0461,8.01667), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.58888,8.04027,24.96999,8.02333), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.62499,8.06722,18.58888,8.04027), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.63666,8.14583,24.85508,8.16974), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.85508,8.16974,24.80444,8.19278), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.80444,8.19278,24.53499,8.20805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.53499,8.20805,18.68666,8.21416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.68666,8.21416,24.53499,8.20805), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.35083,8.24916,18.80944,8.26083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.80944,8.26083,24.45999,8.27139), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.45999,8.27139,18.80944,8.26083), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.17722,8.31611,24.45999,8.27139), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.14027,8.37583,24.17722,8.31611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.16582,8.47777,19.06221,8.57694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.06221,8.57694,24.26916,8.58305), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.26916,8.58305,19.06221,8.57694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.12582,8.67277,24.25333,8.69111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.25333,8.69111,24.23082,8.69722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((24.23082,8.69722,24.25333,8.69111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.51777,8.71416,24.23082,8.69722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.50416,8.80139,18.88408,8.83504), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.88408,8.83504,23.50416,8.80139), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.58166,8.90611,18.92944,8.92055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((18.92944,8.92055,23.58166,8.90611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.52861,8.9575,23.48527,8.96889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.48527,8.96889,23.52861,8.9575), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.58305,8.98861,19.38888,8.99722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.38888,8.99722,23.56527,9.00528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.56527,9.00528,19.38888,8.99722), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.10944,9.01361,23.56527,9.00528), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.4486,9.025,19.10944,9.01361), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((19.86499,9.04694,23.4486,9.025), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.32888,9.10444,20.06749,9.13527), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.06749,9.13527,20.45555,9.15777), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.45555,9.15777,23.49277,9.17416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.49277,9.17416,20.45555,9.15777), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.65221,9.28,20.50194,9.28055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.50194,9.28055,23.65221,9.28), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.66777,9.29833,20.50194,9.28055), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.65916,9.34277,20.70666,9.36416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.70666,9.36416,20.65916,9.34277), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.6261,9.54611,20.95332,9.59), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.95332,9.59,23.6261,9.54611), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.69471,9.67166,20.99694,9.68527), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((20.99694,9.68527,23.69471,9.67166), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.66916,9.86694,21.34222,9.95861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.34222,9.95861,21.26944,9.98111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.26944,9.98111,21.34222,9.95861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.40833,10.00694,21.26944,9.98111), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.47721,10.15111,21.67388,10.24277), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.67388,10.24277,21.71888,10.3325), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.71888,10.3325,21.67388,10.24277), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.30388,10.45916,21.70082,10.52694), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.70082,10.52694,23.30388,10.45916), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((21.72388,10.64194,23.00944,10.69861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((23.00944,10.69861,22.00388,10.73472), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.00388,10.73472,23.00944,10.69861), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.015,10.80777,22.18444,10.81889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.18444,10.81889,22.015,10.80777), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.06388,10.83416,22.18444,10.81889), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.20193,10.87027,22.06388,10.83416), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.88116,10.92391,22.76749,10.955), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.76749,10.955,22.88116,10.92391), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.76749,10.955,22.88116,10.92391), mapfile, tile_dir, 0, 11, "cf-central-african-republic")
	render_tiles((22.4661,11.00139,22.76749,10.955), mapfile, tile_dir, 0, 11, "cf-central-african-republic")