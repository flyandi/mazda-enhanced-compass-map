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
    # Region: BS
    # Region Name: Bahamas, The

	render_tiles((-74.18001,22.25694,-74.14084,22.32333), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.18001,22.25694,-74.14084,22.32333), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.14084,22.32333,-74.08778,22.34527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.08778,22.34527,-73.97723,22.34555), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.97723,22.34555,-74.08778,22.34527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.01973,22.40222,-73.98445,22.45805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.98445,22.45805,-73.91084,22.46888), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.91084,22.46888,-73.98445,22.45805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.86806,22.51221,-73.82945,22.5386), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.82945,22.5386,-73.87862,22.56277), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.87862,22.56277,-73.82945,22.5386), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.9214,22.59944,-74.01501,22.60222), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.01501,22.60222,-73.9214,22.59944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.86835,22.67472,-74.01723,22.67916), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.01723,22.67916,-73.86835,22.67472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.93973,22.69305,-74.01723,22.67916), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-73.85196,22.73138,-73.93973,22.69305), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.2314,25.88138,-77.19446,25.89194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.2314,25.88138,-77.19446,25.89194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.19446,25.89194,-77.2314,25.88138), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.40224,26.01138,-77.19446,25.89194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.18445,26.14055,-77.2189,26.14694), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.2189,26.14694,-77.18445,26.14055), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.25667,26.19971,-77.2189,26.14694), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.14362,26.26194,-77.22639,26.27388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.22639,26.27388,-77.14362,26.26194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.00696,26.30833,-77.22639,26.27388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.22585,26.44166,-77.15445,26.52027), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.15445,26.52027,-77.05139,26.52083), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.05139,26.52083,-77.15445,26.52027), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.1489,26.54888,-77.05139,26.52083), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.16528,26.58611,-77.34056,26.60221), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.34056,26.60221,-77.16528,26.58611), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.425,26.68722,-77.36473,26.68999), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.36473,26.68999,-77.425,26.68722), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.39862,26.77111,-77.52,26.84805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.52,26.84805,-77.95056,26.89777), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.95056,26.89777,-77.89389,26.90444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.89389,26.90444,-77.95056,26.89777), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.5975,26.91444,-77.89389,26.90444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.84084,26.92833,-77.5975,26.91444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.66139,23.71999,-77.61279,23.72749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.66139,23.71999,-77.61279,23.72749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.61279,23.72749,-77.66139,23.71999), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.56723,23.73916,-77.61279,23.72749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.65363,23.75944,-77.67612,23.76055), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.67612,23.76055,-77.65363,23.75944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.73279,23.77277,-77.69667,23.77666), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.69667,23.77666,-77.73279,23.77277), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.76279,23.81944,-77.52473,23.82972), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.52473,23.82972,-77.61612,23.83305), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.61612,23.83305,-77.52473,23.82972), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.57362,23.83666,-77.61612,23.83305), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.78418,23.84999,-77.57362,23.83666), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.66862,23.86833,-77.78418,23.84999), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.8325,23.9361,-77.52556,23.94694), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.52556,23.94694,-77.8325,23.9361), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.83084,24.01527,-77.53307,24.02777), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.53307,24.02777,-77.74167,24.03028), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.74167,24.03028,-77.53307,24.02777), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.68918,24.07833,-77.70251,24.11805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.70251,24.11805,-77.9164,24.1286), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.9164,24.1286,-77.755,24.13888), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.755,24.13888,-77.9164,24.1286), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.63417,24.20944,-77.60667,24.21388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.60667,24.21388,-77.63417,24.20944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.63417,24.25222,-77.70113,24.28472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.70113,24.28472,-77.63417,24.25222), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.46278,24.12249,-75.29001,24.14416), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.46278,24.12249,-75.29001,24.14416), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.29001,24.14416,-75.52585,24.15555), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.52585,24.15555,-75.36584,24.15749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.36584,24.15749,-75.52585,24.15555), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.40417,24.22527,-75.41528,24.27972), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.41528,24.27972,-75.47084,24.29444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.47084,24.29444,-75.41528,24.27972), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.395,24.31166,-75.47084,24.29444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.56361,24.50111,-75.58751,24.51221), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.58751,24.51221,-75.56361,24.50111), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.64362,24.52416,-75.58751,24.51221), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.64806,24.58527,-75.62668,24.64388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.62668,24.64388,-75.75862,24.66306), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.75862,24.66306,-75.62668,24.64388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.71501,24.69638,-75.75862,24.66306), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.02139,24.27277,-78.04501,24.27583), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.02139,24.27277,-78.04501,24.27583), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.04501,24.27583,-78.02139,24.27277), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.87167,24.36861,-78.07945,24.37249), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.07945,24.37249,-77.87167,24.36861), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.14001,24.40666,-77.85779,24.42305), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.85779,24.42305,-78.14001,24.40666), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.77417,24.44916,-78.18251,24.45527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.18251,24.45527,-77.77417,24.44916), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.13278,24.49805,-77.71667,24.50027), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.71667,24.50027,-78.13278,24.49805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.33945,24.51944,-77.71667,24.50027), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.2489,24.56194,-78.22057,24.56638), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.22057,24.56638,-78.2489,24.56194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.33168,24.58638,-78.27112,24.59583), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.27112,24.59583,-78.33168,24.58638), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.44057,24.61361,-78.27112,24.59583), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.22473,24.6636,-78.26501,24.67055), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.26501,24.67055,-78.22473,24.6636), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.29085,24.6786,-78.26501,24.67055), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.73889,24.70944,-78.32112,24.71139), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.32112,24.71139,-77.73889,24.70944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.30585,24.7211,-78.32112,24.71139), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.25917,24.73388,-78.30585,24.7211), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.88196,24.84694,-78.25917,24.73388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.16417,24.98694,-78.18834,25.11444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.18834,25.11444,-77.99583,25.15472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.99583,25.15472,-78.18834,25.11444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.205,25.20194,-77.99583,25.15472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.16196,24.64333,-76.20195,24.66833), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.16196,24.64333,-76.20195,24.66833), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.20195,24.66833,-76.16196,24.64333), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.22862,24.75083,-76.1814,24.7575), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.1814,24.7575,-76.22862,24.75083), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.32028,24.79361,-76.21112,24.80666), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.21112,24.80666,-76.32028,24.79361), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.31334,24.82694,-76.21112,24.80666), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.2114,24.87194,-76.1925,24.87444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.1925,24.87444,-76.2114,24.87194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.19862,24.96083,-76.145,25.00916), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.145,25.00916,-76.19862,24.96083), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.11,25.06666,-76.15224,25.10444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.15224,25.10444,-76.12195,25.13527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.12195,25.13527,-76.18306,25.15083), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.18306,25.15083,-76.12195,25.13527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.30112,25.23833,-76.30334,25.28194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.30334,25.28194,-76.30112,25.23833), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.51306,25.35249,-76.48973,25.36166), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.48973,25.36166,-76.51306,25.35249), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.77917,25.41805,-76.71613,25.44166), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.71613,25.44166,-76.77917,25.41805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.6564,25.48305,-76.71613,25.44166), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.68279,25.55777,-76.73529,25.55916), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-76.73529,25.55916,-76.68279,25.55777), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.77585,26.51221,-78.80307,26.57638), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.77585,26.51221,-78.80307,26.57638), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.80307,26.57638,-78.68195,26.59055), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.68195,26.59055,-78.80307,26.57638), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.2375,26.63527,-77.91278,26.63944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.91278,26.63944,-78.2375,26.63527), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.90001,26.67027,-78.60278,26.68388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.60278,26.68388,-77.90001,26.67027), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.12029,26.70138,-78.60278,26.68388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.53917,26.73222,-78.12029,26.70138), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-77.92445,26.77388,-78.60278,26.77999), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.60278,26.77999,-77.92445,26.77388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-78.58612,26.80527,-78.60278,26.77999), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.85362,22.85805,-74.83168,22.89472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.85362,22.85805,-74.83168,22.89472), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.83168,22.89472,-74.85362,22.85805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.89973,23.04944,-74.96918,23.06805), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.96918,23.06805,-74.89973,23.04944), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-74.96194,23.10277,-75.0464,23.12444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.0464,23.12444,-75.10229,23.14357), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.10229,23.14357,-75.0464,23.12444), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.2289,23.17194,-75.07918,23.17611), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.07918,23.17611,-75.2289,23.17194), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.14029,23.19639,-75.19446,23.19749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.19446,23.19749,-75.14029,23.19639), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.13,23.24277,-75.19446,23.19749), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.09807,23.32499,-75.13667,23.36388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.13667,23.36388,-75.09807,23.32499), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.22806,23.43972,-75.13667,23.36388), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.32779,23.62416,-75.29945,23.62722), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.29945,23.62722,-75.32779,23.62416), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")
	render_tiles((-75.30751,23.66777,-75.29945,23.62722), mapfile, tile_dir, 0, 11, "bs-bahamas,-the")