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
    # Region: North Dakota
    # Region Name: ND

	render_tiles((-96.56367,45.93525,-97.5426,45.93526), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.5426,45.93526,-96.56367,45.93525), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.77704,45.93539,-97.5426,45.93526), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.22829,45.93566,-97.08209,45.93584), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.08209,45.93584,-97.97878,45.93593), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.97878,45.93593,-98.0081,45.93601), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.0081,45.93601,-97.97878,45.93593), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.07052,45.93618,-98.0081,45.93601), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.41452,45.9365,-98.07052,45.93618), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.62538,45.93823,-98.72437,45.93867), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.72437,45.93867,-98.62538,45.93823), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.00575,45.93994,-99.09287,45.94018), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.00564,45.93994,-99.09287,45.94018), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.09287,45.94018,-99.34496,45.9403), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.34496,45.9403,-99.49025,45.94036), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.49025,45.94036,-99.34496,45.9403), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.71807,45.94091,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.71807,45.94091,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.61116,45.9411,-99.71807,45.94091), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.88006,45.94167,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.88029,45.94167,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.15208,45.94249,-100.29413,45.94327), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.29413,45.94327,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.49935,45.94363,-100.51195,45.94365), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.51195,45.94365,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.51179,45.94365,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.76211,45.94377,-100.51195,45.94365), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.10683,45.94398,-101.36528,45.94409), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.36528,45.94409,-101.55728,45.9441), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.55728,45.9441,-101.36528,45.94409), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.79461,45.9444,-102.00068,45.94454), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.00068,45.94454,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.99862,45.94454,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.08756,45.9446,-102.00068,45.94454), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.32823,45.94481,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.55095,45.94502,-102.88025,45.94507), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.88025,45.94507,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.70487,45.94507,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.94207,45.94509,-102.88025,45.94507), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.99567,45.94512,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-103.2184,45.94521,-103.66078,45.94524), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-103.66078,45.94524,-103.2184,45.94521), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-103.43485,45.94529,-104.04544,45.94531), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04544,45.94531,-103.43485,45.94529), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.57426,46.01655,-96.5727,46.02189), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.5727,46.02189,-96.57426,46.01655), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.55451,46.08398,-96.5727,46.02189), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.59567,46.21985,-104.04547,46.28019), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04547,46.28019,-96.60104,46.31955), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.60104,46.31955,-104.04547,46.32455), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04547,46.32455,-96.60104,46.31955), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.6473,46.3585,-104.04547,46.32455), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.7091,46.43529,-104.04505,46.50979), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04505,46.50979,-104.04513,46.54093), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04513,46.54093,-96.74444,46.56596), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.74444,46.56596,-104.04513,46.54093), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.78579,46.62959,-96.78979,46.63575), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.78979,46.63575,-96.79052,46.63688), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.79052,46.63688,-96.78979,46.63575), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04538,46.64144,-96.79052,46.63688), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.78685,46.69281,-104.04557,46.71388), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04557,46.71388,-96.78685,46.69281), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.7888,46.77758,-104.04557,46.71388), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.76397,46.91251,-104.04554,46.93389), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04554,46.93389,-96.76397,46.91251), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.8335,47.01011,-96.81908,47.08115), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.81908,47.08115,-104.04479,47.12743), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04479,47.12743,-96.82657,47.15054), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.82657,47.15054,-104.04479,47.12743), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.83601,47.23798,-96.84022,47.27698), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.84022,47.27698,-96.83601,47.23798), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04531,47.33013,-104.04531,47.33196), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04531,47.33196,-104.04531,47.33013), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04497,47.39746,-96.85748,47.44046), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.85748,47.44046,-104.04497,47.39746), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.85596,47.49917,-96.85748,47.44046), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.85407,47.57201,-104.04391,47.60323), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04391,47.60323,-96.85407,47.57201), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.88238,47.64903,-96.89349,47.67213), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.89349,47.67213,-96.88238,47.64903), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.92851,47.74488,-104.04238,47.80326), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04238,47.80326,-96.99636,47.8444), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-96.99636,47.8444,-104.04238,47.80326), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.03735,47.93328,-104.04393,47.97152), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04393,47.97152,-104.04409,47.9961), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04409,47.9961,-104.04409,47.99611), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04409,47.99611,-104.04409,47.9961), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.06899,48.02627,-104.04409,47.99611), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.10562,48.09136,-97.06899,48.02627), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14675,48.16856,-97.14584,48.17322), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14584,48.17322,-97.14675,48.16856), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14191,48.19363,-97.14584,48.17322), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04569,48.24142,-97.12953,48.25782), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.12953,48.25782,-104.04569,48.24142), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.1379,48.34459,-104.04678,48.38943), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04678,48.38943,-97.13917,48.43053), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.13917,48.43053,-104.04678,48.38943), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04756,48.49414,-97.14912,48.53231), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14912,48.53231,-97.1481,48.54074), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.1481,48.54074,-97.14772,48.54389), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14772,48.54389,-97.1481,48.54074), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.14292,48.58373,-97.14772,48.54389), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04809,48.63401,-97.10001,48.66793), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.10001,48.66793,-104.04809,48.63401), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.12125,48.71359,-97.10001,48.66793), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.15259,48.7726,-97.12125,48.71359), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.0489,48.84739,-97.18736,48.8676), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.18736,48.8676,-104.0489,48.84739), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.22785,48.94586,-102.21699,48.99855), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.21699,48.99855,-102.02115,48.99876), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.02115,48.99876,-103.37547,48.99895), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-103.37547,48.99895,-99.91378,48.99905), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.91378,48.99905,-101.12543,48.99908), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.12543,48.99908,-99.91378,48.99905), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.49674,48.99914,-101.62544,48.99917), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-101.62544,48.99917,-101.49674,48.99914), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.18271,48.99923,-99.5257,48.99927), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.5257,48.99927,-100.18271,48.99923), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.93878,48.99935,-99.37607,48.99936), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-99.37607,48.99936,-102.93878,48.99935), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-100.43168,48.9994,-102.85046,48.99943), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-102.85046,48.99943,-100.43168,48.9994), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04874,48.99988,-98.9998,48.99999), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-104.04874,48.99988,-98.9998,48.99999), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.9998,48.99999,-104.04874,48.99988), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-98.86904,49.00021,-98.9998,48.99999), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.95021,49.00052,-97.77575,49.00057), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.77575,49.00057,-97.95021,49.00052), mapfile, tile_dir, 0, 11, "north dakota-nd")
	render_tiles((-97.22904,49.00069,-97.77575,49.00057), mapfile, tile_dir, 0, 11, "north dakota-nd")