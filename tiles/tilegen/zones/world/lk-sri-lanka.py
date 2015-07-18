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
    # Region: LK
    # Region Name: Sri Lanka

	render_tiles((80.63414,5.94361,80.33359,5.97333), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.33359,5.97333,80.63414,5.94361), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.15526,6.06778,81.11887,6.1125), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.11887,6.1125,80.15526,6.06778), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.50443,6.33667,79.99414,6.38639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.99414,6.38639,81.50443,6.33667), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.66109,6.44,79.99414,6.38639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.77776,6.61528,81.66109,6.44), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.8472,6.88,79.86136,6.99305), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.86136,6.99305,81.89053,7.00055), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.89053,7.00055,79.86136,6.99305), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.84859,7.12305,79.8597,7.16722), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.8597,7.16722,79.80748,7.19222), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.80748,7.19222,79.82637,7.19889), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.82637,7.19889,79.80748,7.19222), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.85831,7.39417,81.77193,7.45028), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.77193,7.45028,81.82748,7.47305), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.82748,7.47305,81.77193,7.45028), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.79344,7.59953,81.71414,7.62361), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.79344,7.59953,81.71414,7.62361), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.71414,7.62361,79.79344,7.59953), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.68192,7.70305,81.66553,7.71444), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.66553,7.71444,81.71303,7.7175), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.71303,7.7175,81.66553,7.71444), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.59331,7.72722,81.71303,7.7175), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.61914,7.76389,81.59331,7.72722), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.63803,7.82611,81.59665,7.83555), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.59665,7.83555,81.63803,7.82611), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.75554,7.86583,81.59665,7.83555), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.81693,7.98555,79.75165,7.98805), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.75165,7.98805,79.81693,7.98555), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.83026,8.01278,79.73012,8.02081), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.73012,8.02081,79.83026,8.01278), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.79942,8.05194,79.73012,8.02081), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.42886,8.08805,81.47247,8.10083), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.47247,8.10083,81.42886,8.08805), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.4222,8.135,81.44331,8.16694), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.44331,8.16694,81.39386,8.17861), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.39386,8.17861,81.44331,8.16694), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.6983,8.1975,81.39386,8.17861), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.41464,8.2511,79.6983,8.1975), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.83165,8.31111,79.77914,8.34305), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.77914,8.34305,79.83165,8.31111), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.2197,8.46277,81.28331,8.46333), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.28331,8.46333,81.2197,8.46277), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.13054,8.5,81.2897,8.50194), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.2897,8.50194,81.13054,8.5), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.21719,8.51194,81.3347,8.51611), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.3347,8.51611,81.21719,8.51194), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.17914,8.52583,81.3347,8.51611), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.24359,8.53694,81.17914,8.52583), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.25192,8.55944,81.20831,8.57083), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.20831,8.57083,81.25192,8.55944), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.94275,8.6425,81.20831,8.57083), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((81.08331,8.86,80.93526,8.93027), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.93526,8.93027,79.91721,8.93777), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.91721,8.93777,80.93526,8.93027), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.97971,8.97361,80.95581,9.00083), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.95581,9.00083,80.05026,9.02639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.05026,9.02639,80.87387,9.03166), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.87387,9.03166,80.05026,9.02639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.89247,9.11139,80.87387,9.03166), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.8047,9.25416,80.78026,9.26361), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.12276,9.25416,80.78026,9.26361), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.78026,9.26361,80.8047,9.25416), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.80193,9.28861,80.73497,9.3025), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.73497,9.3025,80.80193,9.28861), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.06276,9.33139,80.73497,9.3025), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.05359,9.39694,80.30998,9.45277), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.30998,9.45277,80.26192,9.46), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.26192,9.46,80.30998,9.45277), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.19498,9.47027,80.26192,9.46), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.40331,9.49777,80.27664,9.49972), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.27664,9.49972,80.17665,9.50027), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.17665,9.50027,80.27664,9.49972), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.51276,9.54194,80.07303,9.57139), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.07303,9.57139,80.44359,9.57638), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.44359,9.57638,80.07303,9.57139), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.18469,9.58222,80.44359,9.57638), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.08942,9.58972,80.18469,9.58222), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.18109,9.61694,80.08942,9.58972), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.35609,9.61694,80.08942,9.58972), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.18109,9.64916,80.36803,9.65389), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.36803,9.65389,80.33664,9.65861), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.33664,9.65861,80.36803,9.65389), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.95081,9.68611,80.33664,9.65861), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((79.91998,9.76972,80.14026,9.81639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.14026,9.81639,80.11859,9.81833), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.11859,9.81833,80.14026,9.81639), mapfile, tile_dir, 0, 11, "lk-sri-lanka")
	render_tiles((80.20692,9.82611,80.11859,9.81833), mapfile, tile_dir, 0, 11, "lk-sri-lanka")