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
    # Region: SL
    # Region Name: Sierra Leone

	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.55972,7.38639,-12.5975,7.44694), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.52139,7.40139,-12.55972,7.57361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.52139,7.40139,-12.55972,7.57361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.52139,7.40139,-12.55972,7.57361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.52139,7.40139,-12.55972,7.57361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.575,7.44694,-12.5975,7.38639), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.575,7.44694,-12.5975,7.38639), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.575,7.44694,-12.5975,7.38639), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.575,7.44694,-12.5975,7.38639), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.745,7.51528,-12.55972,7.61416), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.745,7.51528,-12.55972,7.61416), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.745,7.51528,-12.55972,7.61416), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.745,7.51528,-12.55972,7.61416), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.49695,7.57361,-12.5975,7.40139), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.49695,7.57361,-12.5975,7.40139), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.49695,7.57361,-12.5975,7.40139), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.49695,7.57361,-12.5975,7.40139), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.90833,7.57444,-12.55972,7.61416), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.90833,7.57444,-12.55972,7.61416), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.90833,7.57444,-12.55972,7.61416), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.90833,7.57444,-12.55972,7.61416), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.88528,7.61416,-12.55972,7.57444), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.88528,7.61416,-12.55972,7.57444), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.88528,7.61416,-12.55972,7.57444), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.88528,7.61416,-12.55972,7.57444), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.5975,7.635,-12.5975,7.44694), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.5975,7.635,-12.5975,7.44694), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.5975,7.635,-12.5975,7.44694), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.5975,7.635,-12.5975,7.44694), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.4987,6.9132,-11.21472,7.14694), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.4987,6.9132,-11.21472,7.14694), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.4987,6.9132,-11.21472,7.14694), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.4987,6.9132,-11.21472,7.14694), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.33778,7.09278,-11.21472,7.14694), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.33778,7.09278,-11.21472,7.14694), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.33778,7.09278,-11.21472,7.14694), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.33778,7.09278,-11.21472,7.14694), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.755,7.09944,-11.4987,9.93139), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.755,7.09944,-11.4987,9.93139), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.755,7.09944,-11.4987,9.93139), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.755,7.09944,-11.4987,9.93139), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.35583,7.14694,-11.21472,7.09278), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.35583,7.14694,-11.21472,7.09278), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.35583,7.14694,-11.21472,7.09278), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.35583,7.14694,-11.21472,7.09278), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.94611,7.18667,-11.4987,9.99611), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.94611,7.18667,-11.4987,9.99611), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.94611,7.18667,-11.4987,9.99611), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.94611,7.18667,-11.4987,9.99611), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.2775,7.22444,-11.21472,7.09278), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.2775,7.22444,-11.21472,7.09278), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.2775,7.22444,-11.21472,7.09278), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.2775,7.22444,-11.21472,7.09278), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.42806,7.36111,-11.21472,7.53028), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.42806,7.36111,-11.21472,7.53028), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.42806,7.36111,-11.21472,7.53028), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.42806,7.36111,-11.21472,7.53028), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.09889,7.38722,-11.4987,9.9975), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.09889,7.38722,-11.4987,9.9975), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.09889,7.38722,-11.4987,9.9975), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.09889,7.38722,-11.4987,9.9975), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.36584,7.38972,-11.21472,7.36111), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.36584,7.38972,-11.21472,7.36111), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.36584,7.38972,-11.21472,7.36111), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.36584,7.38972,-11.21472,7.36111), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.49806,7.39722,-11.21472,7.45222), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.49806,7.39722,-11.21472,7.45222), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.49806,7.39722,-11.21472,7.45222), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.49806,7.39722,-11.21472,7.45222), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.43889,7.41555,-11.21472,7.53028), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.43889,7.41555,-11.21472,7.53028), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.43889,7.41555,-11.21472,7.53028), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.43889,7.41555,-11.21472,7.53028), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.49445,7.45222,-11.21472,7.39722), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.49445,7.45222,-11.21472,7.39722), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.49445,7.45222,-11.21472,7.39722), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.49445,7.45222,-11.21472,7.39722), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.90417,7.50916,-11.4987,9.65833), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.90417,7.50916,-11.4987,9.65833), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.90417,7.50916,-11.4987,9.65833), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.90417,7.50916,-11.4987,9.65833), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.29611,7.52805,-11.21472,7.56805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.29611,7.52805,-11.21472,7.56805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.29611,7.52805,-11.21472,7.56805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.29611,7.52805,-11.21472,7.56805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.42834,7.53028,-11.21472,7.36111), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.42834,7.53028,-11.21472,7.36111), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.42834,7.53028,-11.21472,7.36111), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.42834,7.53028,-11.21472,7.36111), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.46361,7.55611,-11.21472,7.77), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.46361,7.55611,-11.21472,7.77), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.46361,7.55611,-11.21472,7.77), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.46361,7.55611,-11.21472,7.77), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.29528,7.56805,-11.21472,7.52805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.29528,7.56805,-11.21472,7.52805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.29528,7.56805,-11.21472,7.52805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.29528,7.56805,-11.21472,7.52805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.18556,7.59861,-11.4987,9.93888), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.18556,7.59861,-11.4987,9.93888), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.18556,7.59861,-11.4987,9.93888), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.18556,7.59861,-11.4987,9.93888), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.505,7.63361,-11.4987,9.86222), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.505,7.63361,-11.4987,9.86222), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.505,7.63361,-11.4987,9.86222), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.505,7.63361,-11.4987,9.86222), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.54222,7.65722,-11.21472,7.70861), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.54222,7.65722,-11.21472,7.70861), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.54222,7.65722,-11.21472,7.70861), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.54222,7.65722,-11.21472,7.70861), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.60639,7.68278,-11.4987,9.66222), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.60639,7.68278,-11.4987,9.66222), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.60639,7.68278,-11.4987,9.66222), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.60639,7.68278,-11.4987,9.66222), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.54139,7.70861,-11.21472,7.65722), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.54139,7.70861,-11.21472,7.65722), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.54139,7.70861,-11.21472,7.65722), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.54139,7.70861,-11.21472,7.65722), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.68472,7.74444,-11.21472,8.27575), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.68472,7.74444,-11.21472,8.27575), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.68472,7.74444,-11.21472,8.27575), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.68472,7.74444,-11.21472,8.27575), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.46194,7.77,-11.21472,7.55611), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.46194,7.77,-11.21472,7.55611), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.46194,7.77,-11.21472,7.55611), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.46194,7.77,-11.21472,7.55611), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.60044,7.77316,-11.21472,8.03055), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.60044,7.77316,-11.21472,8.03055), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.60044,7.77316,-11.21472,8.03055), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.60044,7.77316,-11.21472,8.03055), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.86195,7.81194,-11.4987,8.56722), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.86195,7.81194,-11.4987,8.56722), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.86195,7.81194,-11.4987,8.56722), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.86195,7.81194,-11.4987,8.56722), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.95806,7.90833,-11.4987,9.23444), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.95806,7.90833,-11.4987,9.23444), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.95806,7.90833,-11.4987,9.23444), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.95806,7.90833,-11.4987,9.23444), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.8225,7.91806,-11.4987,9.28222), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.8225,7.91806,-11.4987,9.28222), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.8225,7.91806,-11.4987,9.28222), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.8225,7.91806,-11.4987,9.28222), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.88194,7.9375,-11.21472,8.10166), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.88194,7.9375,-11.21472,8.10166), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.88194,7.9375,-11.21472,8.10166), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.88194,7.9375,-11.21472,8.10166), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.60333,8.03055,-11.21472,7.77316), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.60333,8.03055,-11.21472,7.77316), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.60333,8.03055,-11.21472,7.77316), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.60333,8.03055,-11.21472,7.77316), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.90806,8.03333,-11.4987,9.27047), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.90806,8.03333,-11.4987,9.27047), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.90806,8.03333,-11.4987,9.27047), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.90806,8.03333,-11.4987,9.27047), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.87806,8.10166,-11.21472,7.9375), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.87806,8.10166,-11.21472,7.9375), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.87806,8.10166,-11.21472,7.9375), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.87806,8.10166,-11.21472,7.9375), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.49806,8.13638,-11.4987,8.625), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.49806,8.13638,-11.4987,8.625), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.49806,8.13638,-11.4987,8.625), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.49806,8.13638,-11.4987,8.625), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.35555,8.14639,-11.4987,8.495), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.35555,8.14639,-11.4987,8.495), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.35555,8.14639,-11.4987,8.495), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.35555,8.14639,-11.4987,8.495), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.16,8.16805,-11.21472,8.24472), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.16,8.16805,-11.21472,8.24472), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.16,8.16805,-11.21472,8.24472), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.16,8.16805,-11.21472,8.24472), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.98278,8.18889,-11.21472,8.25222), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.98278,8.18889,-11.21472,8.25222), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.98278,8.18889,-11.21472,8.25222), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.98278,8.18889,-11.21472,8.25222), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.29722,8.19833,-11.21472,8.36583), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.29722,8.19833,-11.21472,8.36583), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.29722,8.19833,-11.21472,8.36583), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.29722,8.19833,-11.21472,8.36583), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.02222,8.23694,-11.4987,8.64805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.02222,8.23694,-11.4987,8.64805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.02222,8.23694,-11.4987,8.64805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.02222,8.23694,-11.4987,8.64805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.16222,8.24472,-11.21472,8.16805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.16222,8.24472,-11.21472,8.16805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.16222,8.24472,-11.21472,8.16805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.16222,8.24472,-11.21472,8.16805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.97695,8.25222,-11.21472,8.18889), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.97695,8.25222,-11.21472,8.18889), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.97695,8.25222,-11.21472,8.18889), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.97695,8.25222,-11.21472,8.18889), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.70127,8.27575,-11.21472,7.74444), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.70127,8.27575,-11.21472,7.74444), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.70127,8.27575,-11.21472,7.74444), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.70127,8.27575,-11.21472,7.74444), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.54944,8.31027,-11.4987,8.75139), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.54944,8.31027,-11.4987,8.75139), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.54944,8.31027,-11.4987,8.75139), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.54944,8.31027,-11.4987,8.75139), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.65222,8.33805,-11.21472,8.435), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.65222,8.33805,-11.21472,8.435), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.65222,8.33805,-11.21472,8.435), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.65222,8.33805,-11.21472,8.435), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.22472,8.34222,-11.4987,8.82416), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.22472,8.34222,-11.4987,8.82416), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.22472,8.34222,-11.4987,8.82416), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.22472,8.34222,-11.4987,8.82416), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.48583,8.355,-11.21472,8.13638), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.48583,8.355,-11.21472,8.13638), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.48583,8.355,-11.21472,8.13638), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.48583,8.355,-11.21472,8.13638), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.29445,8.36583,-11.21472,8.19833), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.29445,8.36583,-11.21472,8.19833), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.29445,8.36583,-11.21472,8.19833), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.29445,8.36583,-11.21472,8.19833), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.06889,8.37139,-11.4987,8.85639), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.06889,8.37139,-11.4987,8.85639), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.06889,8.37139,-11.4987,8.85639), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.06889,8.37139,-11.4987,8.85639), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.03528,8.38,-11.4987,8.64805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.03528,8.38,-11.4987,8.64805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.03528,8.38,-11.4987,8.64805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.03528,8.38,-11.4987,8.64805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.09806,8.4225,-11.4987,9.04583), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.09806,8.4225,-11.4987,9.04583), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.09806,8.4225,-11.4987,9.04583), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.09806,8.4225,-11.4987,9.04583), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.65028,8.435,-11.21472,8.33805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.65028,8.435,-11.21472,8.33805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.65028,8.435,-11.21472,8.33805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.65028,8.435,-11.21472,8.33805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.12583,8.45333,-11.4987,8.92555), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.12583,8.45333,-11.4987,8.92555), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.12583,8.45333,-11.4987,8.92555), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.12583,8.45333,-11.4987,8.92555), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.26652,8.4887,-11.21472,8.36583), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.26652,8.4887,-11.21472,8.36583), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.26652,8.4887,-11.21472,8.36583), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.26652,8.4887,-11.21472,8.36583), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.19806,8.49361,-11.4987,8.86028), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.19806,8.49361,-11.4987,8.86028), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.19806,8.49361,-11.4987,8.86028), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.19806,8.49361,-11.4987,8.86028), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.38611,8.495,-11.21472,8.14639), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.38611,8.495,-11.21472,8.14639), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.38611,8.495,-11.21472,8.14639), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.38611,8.495,-11.21472,8.14639), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.28528,8.49889,-11.4987,9.03845), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.28528,8.49889,-11.4987,9.03845), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.28528,8.49889,-11.4987,9.03845), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.28528,8.49889,-11.4987,9.03845), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.14528,8.51444,-11.21472,8.16805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.14528,8.51444,-11.21472,8.16805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.14528,8.51444,-11.21472,8.16805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.14528,8.51444,-11.21472,8.16805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.16556,8.51861,-11.21472,8.24472), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.16556,8.51861,-11.21472,8.24472), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.16556,8.51861,-11.21472,8.24472), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.16556,8.51861,-11.21472,8.24472), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.95083,8.55111,-11.4987,8.59166), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.95083,8.55111,-11.4987,8.59166), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.95083,8.55111,-11.4987,8.59166), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.95083,8.55111,-11.4987,8.59166), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.01472,8.55639,-11.4987,9.10361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.01472,8.55639,-11.4987,9.10361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.01472,8.55639,-11.4987,9.10361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.01472,8.55639,-11.4987,9.10361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.87139,8.56722,-11.21472,8.10166), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.87139,8.56722,-11.21472,8.10166), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.87139,8.56722,-11.21472,8.10166), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.87139,8.56722,-11.21472,8.10166), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.58972,8.57166,-11.4987,8.88944), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.58972,8.57166,-11.4987,8.88944), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.58972,8.57166,-11.4987,8.88944), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.58972,8.57166,-11.4987,8.88944), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.10195,8.57972,-11.21472,8.4225), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.10195,8.57972,-11.21472,8.4225), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.10195,8.57972,-11.21472,8.4225), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.10195,8.57972,-11.21472,8.4225), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.94667,8.59166,-11.4987,8.55111), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.94667,8.59166,-11.4987,8.55111), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.94667,8.59166,-11.4987,8.55111), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.94667,8.59166,-11.4987,8.55111), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.08778,8.59333,-11.4987,8.94111), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.08778,8.59333,-11.4987,8.94111), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.08778,8.59333,-11.4987,8.94111), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.08778,8.59333,-11.4987,8.94111), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.50028,8.625,-11.21472,8.13638), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.50028,8.625,-11.21472,8.13638), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.50028,8.625,-11.21472,8.13638), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.50028,8.625,-11.21472,8.13638), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.02889,8.64805,-11.21472,8.38), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.02889,8.64805,-11.21472,8.38), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.02889,8.64805,-11.21472,8.38), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.02889,8.64805,-11.21472,8.38), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.16222,8.65528,-11.21472,8.16805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.16222,8.65528,-11.21472,8.16805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.16222,8.65528,-11.21472,8.16805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.16222,8.65528,-11.21472,8.16805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.24084,8.66528,-11.4987,8.82416), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.24084,8.66528,-11.4987,8.82416), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.24084,8.66528,-11.4987,8.82416), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.24084,8.66528,-11.4987,8.82416), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.46722,8.68416,-11.21472,8.355), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.46722,8.68416,-11.21472,8.355), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.46722,8.68416,-11.21472,8.355), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.46722,8.68416,-11.21472,8.355), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.53028,8.75139,-11.21472,8.31027), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.53028,8.75139,-11.21472,8.31027), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.53028,8.75139,-11.21472,8.31027), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.53028,8.75139,-11.21472,8.31027), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.23695,8.82416,-11.4987,8.66528), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.23695,8.82416,-11.4987,8.66528), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.23695,8.82416,-11.4987,8.66528), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.23695,8.82416,-11.4987,8.66528), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.17222,8.84,-11.4987,8.51861), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.17222,8.84,-11.4987,8.51861), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.17222,8.84,-11.4987,8.51861), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.17222,8.84,-11.4987,8.51861), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.06917,8.85639,-11.21472,8.37139), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.06917,8.85639,-11.21472,8.37139), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.06917,8.85639,-11.21472,8.37139), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.06917,8.85639,-11.21472,8.37139), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.19861,8.86028,-11.4987,8.49361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.19861,8.86028,-11.4987,8.49361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.19861,8.86028,-11.4987,8.49361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.19861,8.86028,-11.4987,8.49361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.59528,8.88944,-11.21472,7.77316), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.59528,8.88944,-11.21472,7.77316), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.59528,8.88944,-11.21472,7.77316), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.59528,8.88944,-11.21472,7.77316), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.16222,8.89055,-11.21472,8.16805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.16222,8.89055,-11.21472,8.16805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.16222,8.89055,-11.21472,8.16805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.16222,8.89055,-11.21472,8.16805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.19417,8.91416,-11.4987,8.49361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.19417,8.91416,-11.4987,8.49361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.19417,8.91416,-11.4987,8.49361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.19417,8.91416,-11.4987,8.49361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.11389,8.92555,-11.4987,8.57972), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.11389,8.92555,-11.4987,8.57972), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.11389,8.92555,-11.4987,8.57972), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.11389,8.92555,-11.4987,8.57972), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.08417,8.94111,-11.4987,8.59333), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.08417,8.94111,-11.4987,8.59333), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.08417,8.94111,-11.4987,8.59333), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.08417,8.94111,-11.4987,8.59333), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.25111,8.95472,-11.4987,8.66528), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.25111,8.95472,-11.4987,8.66528), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.25111,8.95472,-11.4987,8.66528), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.25111,8.95472,-11.4987,8.66528), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.29843,9.03845,-11.4987,8.49889), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.29843,9.03845,-11.4987,8.49889), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.29843,9.03845,-11.4987,8.49889), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.29843,9.03845,-11.4987,8.49889), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.09583,9.04583,-11.21472,8.4225), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.09583,9.04583,-11.21472,8.4225), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.09583,9.04583,-11.21472,8.4225), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.09583,9.04583,-11.21472,8.4225), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.57125,9.057,-11.4987,8.57166), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.57125,9.057,-11.4987,8.57166), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.57125,9.057,-11.4987,8.57166), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.57125,9.057,-11.4987,8.57166), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.73389,9.08,-11.4987,9.18111), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.73389,9.08,-11.4987,9.18111), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.73389,9.08,-11.4987,9.18111), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.73389,9.08,-11.4987,9.18111), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.1875,9.08694,-11.4987,8.91416), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.1875,9.08694,-11.4987,8.91416), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.1875,9.08694,-11.4987,8.91416), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.1875,9.08694,-11.4987,8.91416), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-13.0075,9.10361,-11.4987,8.55639), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-13.0075,9.10361,-11.4987,8.55639), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-13.0075,9.10361,-11.4987,8.55639), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-13.0075,9.10361,-11.4987,8.55639), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.72694,9.18111,-11.4987,9.08), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.72694,9.18111,-11.4987,9.08), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.72694,9.18111,-11.4987,9.08), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.72694,9.18111,-11.4987,9.08), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.96083,9.23444,-11.21472,7.90833), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.96083,9.23444,-11.21472,7.90833), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.96083,9.23444,-11.21472,7.90833), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.96083,9.23444,-11.21472,7.90833), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.90091,9.27047,-11.21472,8.03333), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.84417,9.28222,-11.21472,7.81194), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.84417,9.28222,-11.21472,7.81194), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.84417,9.28222,-11.21472,7.81194), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.84417,9.28222,-11.21472,7.81194), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.93056,9.28889,-11.4987,8.59166), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.93056,9.28889,-11.4987,8.59166), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.93056,9.28889,-11.4987,8.59166), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.93056,9.28889,-11.4987,8.59166), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.65667,9.295,-11.21472,8.33805), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.65667,9.295,-11.21472,8.33805), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.65667,9.295,-11.21472,8.33805), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.65667,9.295,-11.21472,8.33805), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.78806,9.32722,-11.21472,7.91806), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.78806,9.32722,-11.21472,7.91806), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.78806,9.32722,-11.21472,7.91806), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.78806,9.32722,-11.21472,7.91806), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.74833,9.38778,-11.4987,9.08), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.74833,9.38778,-11.4987,9.08), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.74833,9.38778,-11.4987,9.08), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.74833,9.38778,-11.4987,9.08), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.70472,9.39916,-11.4987,9.32722), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.70472,9.39916,-11.4987,9.32722), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.70472,9.39916,-11.4987,9.32722), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.70472,9.39916,-11.4987,9.32722), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.82861,9.43333,-11.21472,7.50916), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.82861,9.43333,-11.21472,7.50916), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.82861,9.43333,-11.21472,7.50916), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.82861,9.43333,-11.21472,7.50916), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-10.93639,9.65833,-11.21472,7.50916), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-10.93639,9.65833,-11.21472,7.50916), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-10.93639,9.65833,-11.21472,7.50916), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-10.93639,9.65833,-11.21472,7.50916), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.58361,9.66222,-11.21472,7.68278), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.58361,9.66222,-11.21472,7.68278), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.58361,9.66222,-11.21472,7.68278), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.58361,9.66222,-11.21472,7.68278), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.51945,9.71083,-11.21472,7.63361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.51945,9.71083,-11.21472,7.63361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.51945,9.71083,-11.21472,7.63361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.51945,9.71083,-11.21472,7.63361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.50333,9.86222,-11.21472,7.63361), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.50333,9.86222,-11.21472,7.63361), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.50333,9.86222,-11.21472,7.63361), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.50333,9.86222,-11.21472,7.63361), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.13194,9.875,-11.21472,7.59861), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.13194,9.875,-11.21472,7.59861), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.13194,9.875,-11.21472,7.59861), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.13194,9.875,-11.21472,7.59861), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.89333,9.93139,-11.4987,9.99611), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.89333,9.93139,-11.4987,9.99611), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.89333,9.93139,-11.4987,9.99611), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.89333,9.93139,-11.4987,9.99611), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-12.23556,9.93888,-11.21472,7.59861), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-12.23556,9.93888,-11.21472,7.59861), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-12.23556,9.93888,-11.21472,7.59861), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-12.23556,9.93888,-11.21472,7.59861), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.895,9.99611,-11.4987,9.93139), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.895,9.99611,-11.4987,9.93139), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.895,9.99611,-11.4987,9.93139), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.895,9.99611,-11.4987,9.93139), mapfile, tile_dir, 17, 17, "sl-sierra-leone")
	render_tiles((-11.21472,9.9975,-11.21472,7.22444), mapfile, tile_dir, 0, 11, "sl-sierra-leone")
	render_tiles((-11.21472,9.9975,-11.21472,7.22444), mapfile, tile_dir, 13, 13, "sl-sierra-leone")
	render_tiles((-11.21472,9.9975,-11.21472,7.22444), mapfile, tile_dir, 15, 15, "sl-sierra-leone")
	render_tiles((-11.21472,9.9975,-11.21472,7.22444), mapfile, tile_dir, 17, 17, "sl-sierra-leone")