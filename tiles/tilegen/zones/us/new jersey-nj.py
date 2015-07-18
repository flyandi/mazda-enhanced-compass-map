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
    # Zone: us
    # Region: New Jersey
    # Region Name: NJ

	render_tiles((-74.93357,38.92852,-74.96727,38.93341), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.96727,38.93341,-74.93357,38.92852), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.86446,38.94041,-74.96727,38.93341), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.80792,38.98595,-74.95536,39.00126), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.95536,39.00126,-74.80792,38.98595), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.77878,39.02307,-74.95536,39.00126), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.90366,39.08744,-74.70588,39.10294), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.70588,39.10294,-74.90366,39.08744), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.71434,39.1198,-74.70588,39.10294), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.88591,39.14363,-74.71434,39.1198), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.90518,39.17495,-74.91516,39.1767), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.91516,39.1767,-74.90518,39.17495), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.67143,39.1798,-75.13667,39.18188), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.13667,39.18188,-74.67143,39.1798), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.998,39.19125,-75.13667,39.18188), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.09079,39.2108,-74.6466,39.212), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.6466,39.212,-75.09079,39.2108), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.04849,39.21522,-74.6466,39.212), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.16667,39.22258,-75.04849,39.21522), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.21251,39.26276,-74.58101,39.27082), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.58101,39.27082,-75.21251,39.26276), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.24436,39.2857,-75.28533,39.29221), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.28533,39.29221,-75.24436,39.2857), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.54099,39.29988,-75.28533,39.29221), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.5218,39.31382,-74.54099,39.29988), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.32675,39.33247,-75.35556,39.34782), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.35556,39.34782,-74.41269,39.36082), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.41269,39.36082,-75.35556,39.34782), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.3993,39.37949,-75.41711,39.38891), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.41711,39.38891,-75.3993,39.37949), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.36699,39.40202,-75.44239,39.40229), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.44239,39.40229,-74.36699,39.40202), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.46521,39.43893,-75.53643,39.46056), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.53643,39.46056,-74.30434,39.47145), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.30434,39.47145,-75.53643,39.46056), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.52809,39.49811,-74.29159,39.50771), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.29159,39.50771,-74.29102,39.50837), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.29102,39.50837,-74.29159,39.50771), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.52768,39.53528,-74.29102,39.50837), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.51273,39.578,-75.54397,39.596), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.54397,39.596,-75.51273,39.578), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.19097,39.62512,-75.55945,39.62981), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.55945,39.62981,-74.19097,39.62512), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.55945,39.62981,-74.19097,39.62512), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.53514,39.64721,-75.55945,39.62981), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.50974,39.68611,-75.47764,39.71501), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.47764,39.71501,-75.50974,39.68611), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.10144,39.75617,-74.10137,39.75649), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.10137,39.75649,-74.10144,39.75617), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.45944,39.76581,-74.10137,39.75649), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.44104,39.78079,-75.45944,39.76581), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.09095,39.79998,-75.41506,39.80192), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.41506,39.80192,-74.09095,39.79998), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.39032,39.81683,-75.41506,39.80192), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.35165,39.84013,-75.34177,39.84608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.34177,39.84608,-75.29338,39.84878), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.29338,39.84878,-75.34177,39.84608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.25881,39.85467,-75.29338,39.84878), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.22103,39.86111,-75.2112,39.86652), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.2112,39.86652,-75.22103,39.86111), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.18302,39.88201,-75.14144,39.89392), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.14144,39.89392,-75.13342,39.89621), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.13342,39.89621,-75.14144,39.89392), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.07725,39.91099,-75.13342,39.89621), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.13572,39.94711,-75.11922,39.96541), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.11922,39.96541,-74.06414,39.97916), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.06414,39.97916,-75.06013,39.99201), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.06013,39.99201,-75.05902,39.99251), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.05902,39.99251,-75.06013,39.99201), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.98991,40.03731,-74.97285,40.04651), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.97285,40.04651,-74.98991,40.03731), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.93221,40.06841,-74.86381,40.08221), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.86381,40.08221,-74.93221,40.06841), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.03496,40.10258,-74.03018,40.12281), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.03018,40.12281,-74.82591,40.12391), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.82591,40.12391,-74.03018,40.12281), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.76949,40.12915,-74.82591,40.12391), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.72628,40.1514,-74.7216,40.15381), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.7216,40.15381,-74.72628,40.1514), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.74836,40.18474,-74.76061,40.19891), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.76061,40.19891,-74.74836,40.18474), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.00097,40.21712,-74.76061,40.19891), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.82391,40.24151,-74.00097,40.21712), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.85651,40.27741,-73.98168,40.27941), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.98168,40.27941,-74.85651,40.27741), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.90331,40.31561,-74.92811,40.33983), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.92811,40.33983,-73.97138,40.34801), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.97138,40.34801,-74.92811,40.33983), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.94601,40.35731,-73.97138,40.34801), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.9696,40.39977,-75.02478,40.40346), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.02478,40.40346,-74.9696,40.39977), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.97698,40.40851,-75.02478,40.40346), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.0561,40.41607,-74.04788,40.41891), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.04788,40.41891,-75.0561,40.41607), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.20619,40.44071,-74.1083,40.44379), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.1083,40.44379,-74.20619,40.44071), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.15709,40.44757,-74.2248,40.44873), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.2248,40.44873,-74.15709,40.44757), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.07057,40.45517,-74.2248,40.44873), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.26189,40.46471,-74.01933,40.47124), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.01933,40.47124,-73.99794,40.47667), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.99794,40.47667,-75.06223,40.48139), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.06223,40.48139,-73.99794,40.47667), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.26061,40.50244,-75.06223,40.48139), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.24921,40.54506,-75.0785,40.5483), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.0785,40.5483,-74.24921,40.54506), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.21684,40.55862,-75.0785,40.5483), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.18674,40.56941,-75.13675,40.57573), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.13675,40.57573,-75.18674,40.56941), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.1882,40.59258,-74.20369,40.59269), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.20369,40.59269,-75.1882,40.59258), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.18924,40.60906,-74.20369,40.59269), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.20225,40.6309,-74.20012,40.63187), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.20012,40.63187,-74.20225,40.6309), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.19106,40.63797,-74.20012,40.63187), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.17061,40.64529,-74.16015,40.64608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.16015,40.64608,-74.17061,40.64529), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.08681,40.6516,-74.16015,40.64608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.07094,40.66721,-74.06772,40.67038), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.06772,40.67038,-74.07094,40.66721), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.19184,40.67724,-74.06772,40.67038), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.04731,40.69047,-74.04697,40.69115), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.04697,40.69115,-74.04731,40.69047), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.04116,40.70261,-74.04697,40.69115), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.19261,40.71587,-74.03093,40.72279), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.03093,40.72279,-75.19261,40.71587), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.02546,40.73356,-74.02349,40.73745), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.02349,40.73745,-74.02546,40.73356), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.01378,40.7566,-75.17748,40.76423), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.17748,40.76423,-74.01378,40.7566), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.1107,40.79024,-75.10851,40.79109), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.10851,40.79109,-75.1107,40.79024), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.98472,40.79737,-75.10851,40.79109), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.97121,40.81632,-73.96808,40.8207), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.96808,40.8207,-73.96583,40.82475), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.96583,40.82475,-73.96808,40.8207), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.09096,40.84919,-73.94748,40.85777), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.94748,40.85777,-75.09096,40.84919), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.93808,40.8747,-73.93489,40.88265), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.93489,40.88265,-75.06544,40.88568), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.06544,40.88568,-73.93489,40.88265), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.92202,40.91474,-73.92047,40.91861), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.92047,40.91861,-73.92202,40.91474), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.09772,40.92668,-73.92047,40.91861), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.90728,40.9515,-75.12325,40.96531), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.12325,40.96531,-73.90728,40.9515), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.13309,40.98018,-75.12325,40.96531), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.89398,40.9972,-75.13309,40.98018), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-73.89398,40.9972,-75.13309,40.98018), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.07053,41.01862,-73.89398,40.9972), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.04105,41.05909,-75.01527,41.06122), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-75.01527,41.06122,-74.04105,41.05909), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.99239,41.09303,-74.983,41.10608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.983,41.10608,-74.97987,41.11042), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.97987,41.11042,-74.983,41.10608), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.21341,41.13376,-74.23447,41.14288), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.23447,41.14288,-74.21341,41.13376), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.90526,41.15567,-74.23447,41.14288), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.30199,41.17259,-74.90526,41.15567), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.36648,41.20394,-74.86741,41.22777), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.86741,41.22777,-74.45758,41.24823), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.45758,41.24823,-74.86741,41.22777), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.8157,41.29615,-74.76033,41.34033), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.76033,41.34033,-74.69491,41.35742), mapfile, tile_dir, 0, 11, "new jersey-nj")
	render_tiles((-74.69491,41.35742,-74.76033,41.34033), mapfile, tile_dir, 0, 11, "new jersey-nj")