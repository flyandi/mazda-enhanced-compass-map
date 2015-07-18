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
    # Region: Oklahoma
    # Region Name: OK

	render_tiles((-94.52893,33.62184,-94.48588,33.63787), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.48588,33.63787,-94.52893,33.62184), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.57287,33.66989,-94.63059,33.6734), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.63059,33.6734,-94.57287,33.66989), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.36314,33.69422,-94.71487,33.70726), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.71487,33.70726,-96.37966,33.71553), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.37966,33.71553,-94.73193,33.72083), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.73193,33.72083,-97.14939,33.72197), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.14939,33.72197,-94.73193,33.72083), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.30739,33.73501,-97.09107,33.73512), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.09107,33.73512,-96.30739,33.73501), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.84163,33.73943,-97.09107,33.73512), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.40351,33.74629,-96.22902,33.74802), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.22902,33.74802,-94.76615,33.74803), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.76615,33.74803,-96.22902,33.74802), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.27727,33.76974,-96.50229,33.77346), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.50229,33.77346,-97.08785,33.7741), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.08785,33.7741,-96.50229,33.77346), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.90228,33.77629,-97.08785,33.7741), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.43646,33.78005,-94.90228,33.77629), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.48184,33.78901,-96.43646,33.78005), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.17303,33.80056,-97.20565,33.80982), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.20565,33.80982,-94.93956,33.8105), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.93956,33.8105,-97.20565,33.80982), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.07859,33.81276,-94.93956,33.8105), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.52386,33.81811,-96.57294,33.8191), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.57294,33.8191,-97.37294,33.81945), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.37294,33.81945,-96.57294,33.8191), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.44419,33.82377,-97.37294,33.81945), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.59293,33.83092,-96.71242,33.83163), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.71242,33.83163,-96.15163,33.83195), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.15163,33.83195,-96.71242,33.83163), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.06392,33.84152,-96.77677,33.84198), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.77677,33.84198,-96.06392,33.84152), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.85059,33.84721,-97.16663,33.84731), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.16663,33.84731,-96.85059,33.84721), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.86577,33.84939,-97.16663,33.84731), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.98165,33.85228,-97.86577,33.84939), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.05584,33.85574,-95.8206,33.85847), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.8206,33.85847,-95.84488,33.86042), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.84488,33.86042,-95.8206,33.85847), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.04657,33.86257,-95.88749,33.86386), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.88749,33.86386,-97.31824,33.86512), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.31824,33.86512,-95.88749,33.86386), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.35234,33.86779,-95.44737,33.86885), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.44737,33.86885,-96.79428,33.86889), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.79428,33.86889,-95.44737,33.86885), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.45147,33.87093,-95.78964,33.87244), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.78964,33.87244,-95.31045,33.87384), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.31045,33.87384,-95.93533,33.8751), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.93533,33.8751,-95.31045,33.87384), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.6821,33.87665,-95.28345,33.87775), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.28345,33.87775,-97.95122,33.87842), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.95122,33.87842,-95.28345,33.87775), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.80347,33.88019,-96.59011,33.88067), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.59011,33.88067,-97.80347,33.88019), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.59467,33.88302,-96.59011,33.88067), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.52532,33.88549,-96.98557,33.88652), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.98557,33.88652,-95.52532,33.88549), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.95191,33.89123,-96.98557,33.88652), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.73751,33.89597,-97.55827,33.8971), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.55827,33.8971,-95.73751,33.89597), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.56124,33.89906,-97.24618,33.90034), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.24618,33.90034,-97.56124,33.89906), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.89719,33.90295,-95.095,33.90482), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.095,33.90482,-95.66998,33.90584), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.66998,33.90584,-95.095,33.90482), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.48414,33.91389,-97.20614,33.91428), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.20614,33.91428,-97.48414,33.91389), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.6599,33.91667,-97.48651,33.91699), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.48651,33.91699,-96.6599,33.91667), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.98875,33.91847,-97.48651,33.91699), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.59616,33.92211,-97.9537,33.92437), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.9537,33.92437,-97.75983,33.92521), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.75983,33.92521,-97.9537,33.92437), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.55692,33.92702,-95.60366,33.9272), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.60366,33.9272,-95.55692,33.92702), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.25362,33.92971,-95.60366,33.9272), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.14946,33.93634,-95.15591,33.93848), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.15591,33.93848,-95.14946,33.93634), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.47727,33.94091,-95.15591,33.93848), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.95231,33.94458,-96.94462,33.94501), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.94462,33.94501,-96.95231,33.94458), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.93434,33.94559,-96.94462,33.94501), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.90525,33.94722,-96.93434,33.94559), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.22639,33.96195,-97.60909,33.96809), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.60909,33.96809,-95.22639,33.96195), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.94757,33.99105,-97.67177,33.99137), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.67177,33.99137,-97.94757,33.99105), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.00567,33.99596,-97.67177,33.99137), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.08284,34.00241,-98.00567,33.99596), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.4749,34.01966,-98.08284,34.00241), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.0991,34.04864,-98.47507,34.06427), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.47507,34.06427,-98.0991,34.04864), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.42353,34.08195,-98.41443,34.08507), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.41443,34.08507,-98.42353,34.08195), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.5282,34.09496,-98.09933,34.1043), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.09933,34.1043,-98.5282,34.09496), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.16912,34.11417,-98.09933,34.1043), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.22528,34.12725,-98.39844,34.12846), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.39844,34.12846,-98.22528,34.12725), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.73723,34.13099,-98.69007,34.13316), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.69007,34.13316,-98.73723,34.13099), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.13849,34.14121,-98.31875,34.14642), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.31875,34.14642,-98.57714,34.14896), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.57714,34.14896,-98.31875,34.14642), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.12338,34.15454,-98.80681,34.1559), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.80681,34.1559,-98.61035,34.15621), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.61035,34.15621,-98.80681,34.1559), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.36402,34.15711,-98.61035,34.15621), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.87223,34.16045,-98.36402,34.15711), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.64807,34.16444,-98.87223,34.16045), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.47015,34.18986,-99.0588,34.20126), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.0588,34.20126,-98.94022,34.20369), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.94022,34.20369,-98.95232,34.20467), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.95232,34.20467,-98.94022,34.20369), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.00292,34.20878,-99.13155,34.20935), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.13155,34.20935,-99.00292,34.20878), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.18951,34.21431,-99.13155,34.20935), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.2116,34.31397,-99.22161,34.32537), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.22161,34.32537,-99.2116,34.31397), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.46543,34.35955,-99.60003,34.37469), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.60003,34.37469,-99.42043,34.38046), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.42043,34.38046,-99.69646,34.38104), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.69646,34.38104,-99.42043,34.38046), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.27534,34.3866,-99.69646,34.38104), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.47097,34.39647,-99.47502,34.39687), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.47502,34.39687,-99.47097,34.39647), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.58448,34.40767,-99.47502,34.39687), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.76488,34.43527,-99.35041,34.43708), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.35041,34.43708,-99.76488,34.43527), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.39496,34.4421,-99.35041,34.43708), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.81819,34.48784,-99.84206,34.50693), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.84206,34.50693,-94.46117,34.50746), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.46117,34.50746,-99.84206,34.50693), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00038,34.56051,-99.99763,34.56114), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.99763,34.56114,-100.00038,34.56051), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.92933,34.57671,-99.99763,34.56114), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.4575,34.63495,-99.92933,34.57671), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.4544,34.72896,-100.00038,34.74636), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00038,34.74636,-94.4544,34.72896), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.44906,34.89056,-94.44751,34.93398), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.44751,34.93398,-94.44906,34.89056), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00038,35.03038,-94.44293,35.06251), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.44293,35.06251,-100.00038,35.03038), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00039,35.1827,-94.43532,35.27589), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.43532,35.27589,-100.00039,35.1827), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.43152,35.36959,-94.43391,35.38636), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.43391,35.38636,-94.43489,35.39319), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.43489,35.39319,-94.43391,35.38636), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00039,35.42236,-94.43489,35.39319), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.4497,35.49672,-100.00039,35.42236), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00039,35.61912,-94.47312,35.63855), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.47312,35.63855,-100.00039,35.61912), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.49304,35.75917,-94.49455,35.7683), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.49455,35.7683,-94.49304,35.75917), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.0004,35.88095,-94.53207,35.98785), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.53207,35.98785,-100.0004,36.05568), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.0004,36.05568,-94.55191,36.10223), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.55191,36.10223,-100.0004,36.05568), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.56227,36.16197,-94.55191,36.10223), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.5862,36.29997,-94.56227,36.16197), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.08516,36.49924,-94.61792,36.49941), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.61792,36.49941,-100.59261,36.49947), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.59261,36.49947,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.54615,36.49951,-100.95415,36.49953), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.95415,36.49953,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.62392,36.49953,-100.54615,36.49951), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.82657,36.49965,-100.88417,36.49968), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.88417,36.49968,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.31102,36.49969,-100.00376,36.4997), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00376,36.4997,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00041,36.4997,-100.31102,36.49969), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.03234,36.50007,-102.16246,36.50033), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.16246,36.50033,-103.00243,36.5004), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.00243,36.5004,-102.16246,36.50033), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.00219,36.60272,-94.61782,36.6126), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.61782,36.6126,-103.00219,36.60272), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.61799,36.66792,-103.00252,36.67519), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.00252,36.67519,-94.61799,36.66792), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.00252,36.67519,-94.61799,36.66792), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.61831,36.76656,-103.00252,36.67519), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.00196,36.90957,-102.04224,36.99308), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.04224,36.99308,-102.0282,36.99315), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.0282,36.99315,-102.04224,36.99308), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.90244,36.9937,-102.0282,36.99315), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.35529,36.99451,-102.69814,36.99515), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.69814,36.99515,-101.55526,36.99529), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.55526,36.99529,-102.69814,36.99515), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.48533,36.99561,-101.55526,36.99529), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.21149,36.99712,-101.06645,36.99774), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-101.06645,36.99774,-98.35407,36.99796), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.35407,36.99796,-98.34715,36.99797), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.34715,36.99797,-98.35407,36.99796), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.94547,36.99825,-98.04534,36.99833), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.11199,36.99825,-98.04534,36.99833), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.04534,36.99833,-100.94547,36.99825), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.54466,36.99852,-100.85563,36.99863), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.85563,36.99863,-96.50029,36.99864), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.50029,36.99864,-100.85563,36.99863), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.52558,36.99868,-97.80231,36.9987), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.80231,36.9987,-96.52558,36.99868), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.7687,36.99875,-94.71277,36.99879), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.71277,36.99879,-97.46235,36.99882), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.46235,36.99882,-97.38493,36.99884), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.38493,36.99884,-97.46235,36.99882), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.61796,36.99891,-97.14772,36.99897), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.14772,36.99897,-96.74984,36.99899), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.74984,36.99899,-97.10065,36.999), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-97.10065,36.999,-96.74984,36.99899), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.21757,36.99907,-97.10065,36.999), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-96.00081,36.9992,-95.96427,36.99922), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.96427,36.99922,-96.00081,36.9992), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.92812,36.99925,-98.79194,36.99926), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-98.79194,36.99926,-95.92812,36.99925), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.78676,36.99927,-98.79194,36.99926), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.5736,36.99931,-95.52241,36.99932), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.52241,36.99932,-95.5736,36.99931), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.40762,36.99934,-95.52241,36.99932), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.32257,36.99936,-95.40762,36.99934), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.0003,36.99936,-95.40762,36.99934), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.12945,36.99942,-95.32257,36.99936), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.0735,36.99949,-95.00762,36.99952), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-95.00762,36.99952,-94.99529,36.99953), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-94.99529,36.99953,-95.00762,36.99952), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.40702,36.99958,-102.84199,36.9996), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-102.84199,36.9996,-99.40702,36.99958), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.4562,36.9997,-102.84199,36.9996), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.54111,36.99991,-103.0022,37.0001), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-103.0022,37.0001,-100.63332,37.00017), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.63332,37.00017,-99.65766,37.0002), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.65766,37.0002,-100.63332,37.00017), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.55268,37.00074,-99.65766,37.0002), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.08948,37.00148,-100.00257,37.00162), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-100.00257,37.00162,-99.9952,37.00163), mapfile, tile_dir, 0, 11, "oklahoma-ok")
	render_tiles((-99.9952,37.00163,-100.00257,37.00162), mapfile, tile_dir, 0, 11, "oklahoma-ok")