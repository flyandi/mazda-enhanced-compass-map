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
    # Region: YE
    # Region Name: Yemen

	render_tiles((43.93471,12.59972,44.06221,12.60583), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.06221,12.60583,43.93471,12.59972), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.13721,12.62972,44.28888,12.63472), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.28888,12.63472,44.13721,12.62972), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.89832,12.65167,44.16388,12.65389), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.16388,12.65389,43.89832,12.65167), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.45999,12.68444,43.53443,12.69083), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.53443,12.69083,43.52776,12.69111), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.52776,12.69111,43.53443,12.69083), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.45971,12.71805,44.72832,12.74222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.72832,12.74222,44.91582,12.74333), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.91582,12.74333,44.72832,12.74222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.59888,12.75,45.03443,12.75222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.03443,12.75222,43.59888,12.75), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.06138,12.75639,45.03443,12.75222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.7736,12.76972,44.88915,12.78278), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.88915,12.78278,44.97665,12.785), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.97665,12.785,44.88915,12.78278), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.69721,12.79556,44.54443,12.79889), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.54443,12.79889,44.69721,12.79556), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.04276,12.815,43.48388,12.81722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.48388,12.81722,44.60526,12.81861), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.60526,12.81861,43.48388,12.81722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.00638,12.85361,43.45693,12.865), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.45693,12.865,45.07221,12.87306), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.07221,12.87306,43.45693,12.865), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.16082,12.99278,45.39277,13.05722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.39277,13.05722,45.16082,12.99278), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.22804,13.27777,45.62999,13.32389), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.62999,13.32389,43.22804,13.27777), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((46.02776,13.40667,43.2461,13.41166), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.2461,13.41166,46.02776,13.40667), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((46.57499,13.43,43.2461,13.41166), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((46.83332,13.48277,46.57499,13.43), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((47.1136,13.58333,43.29305,13.59111), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.29305,13.59111,47.1136,13.58333), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((47.38721,13.64361,43.29305,13.59111), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.23777,13.83,47.78054,13.91972), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((47.78054,13.91972,48.26943,13.98361), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.26943,13.98361,43.08665,13.99333), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.08665,13.99333,48.49971,14.00083), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.49971,14.00083,43.08665,13.99333), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.44554,14.01667,48.13749,14.02417), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.13749,14.02417,48.44554,14.01667), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.54943,14.03861,48.69804,14.04), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((48.69804,14.04,48.54943,14.03861), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((47.98499,14.04556,48.69804,14.04), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.10221,14.17639,47.98499,14.04556), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.00082,14.315,42.99082,14.44389), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.99082,14.44389,43.02193,14.44555), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.02193,14.44555,42.99082,14.44389), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.07499,14.50139,49.18494,14.51055), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.18494,14.51055,49.07499,14.50139), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.1386,14.53305,49.17999,14.54055), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.17999,14.54055,49.1386,14.53305), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.02721,14.55555,49.17999,14.54055), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.96054,14.70083,49.6386,14.75027), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((49.6386,14.75027,42.96054,14.70083), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.93665,14.81111,50.04443,14.81611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((50.04443,14.81611,42.93665,14.81111), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.94832,14.82611,50.04443,14.81611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((50.01471,14.84583,42.94832,14.82611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((50.34554,14.91583,50.01471,14.84583), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((50.45082,15.00528,42.87916,15.05611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.87916,15.05611,45.59082,15.10555), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((45.59082,15.10555,50.97582,15.12833), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((50.97582,15.12833,42.85332,15.14305), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.85332,15.14305,50.97582,15.12833), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.63277,15.18417,42.71944,15.21028), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.71944,15.21028,42.63277,15.18417), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.68443,15.23889,42.78138,15.24583), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.78138,15.24583,42.61388,15.2475), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.61388,15.2475,42.78138,15.24583), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.47166,15.26639,42.61388,15.2475), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.51415,15.30694,51.67527,15.33222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.67527,15.33222,51.51415,15.30694), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.70999,15.36139,51.67527,15.33222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.67194,15.39472,42.70999,15.36139), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.8386,15.46639,42.78526,15.46694), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.78526,15.46694,51.8386,15.46639), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.71471,15.56222,42.78526,15.46694), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.23721,15.66611,42.70082,15.72194), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.70082,15.72194,52.23721,15.66611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.83638,15.89083,52.15638,15.97611), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.15638,15.97611,42.83638,15.89083), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.26721,16.22556,42.81194,16.3175), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.81194,16.3175,42.79027,16.37722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((42.79027,16.37722,52.4511,16.39389), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.4511,16.39389,42.79027,16.37722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.68721,16.50055,43.06666,16.54944), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.06666,16.54944,52.68721,16.50055), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((53.07825,16.64386,43.20609,16.67221), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.20609,16.67221,43.10249,16.67944), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.10249,16.67944,43.20609,16.67221), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.18526,16.75222,43.23054,16.77666), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.23054,16.77666,43.18526,16.75222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.22803,16.80972,43.23054,16.77666), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.15359,16.8486,43.22803,16.80972), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.71027,17.08027,43.17304,17.2111), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.17304,17.2111,43.27387,17.26361), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.27387,17.26361,43.2561,17.30722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.2561,17.30722,43.95693,17.30805), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.95693,17.30805,43.2561,17.30722), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.90971,17.31333,43.95693,17.30805), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.18054,17.3325,52.74638,17.33694), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.74638,17.33694,43.18054,17.3325), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.11471,17.34305,43.79221,17.3461), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.79221,17.3461,44.11471,17.34305), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.90971,17.35888,43.79221,17.3461), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.02387,17.37499,43.90971,17.35888), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.7036,17.39499,44.49887,17.39582), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.49887,17.39582,43.7036,17.39499), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.17332,17.41194,44.34054,17.41388), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((44.34054,17.41388,44.17332,17.41194), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.27082,17.42555,44.34054,17.41388), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.52637,17.51667,43.44915,17.5275), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((43.44915,17.5275,52.61166,17.53222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((52.61166,17.53222,43.44915,17.5275), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((47.46194,17.57415,52.61166,17.53222), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.90415,18.54528,51.99915,18.99888), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.99915,18.99888,51.90415,18.54528), mapfile, tile_dir, 0, 11, "ye-yemen")
	render_tiles((51.99915,18.99888,51.90415,18.54528), mapfile, tile_dir, 0, 11, "ye-yemen")