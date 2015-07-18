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
    # Region: VC
    # Region Name: St. Vincent and the Grenadines

	render_tiles((-61.24965,12.97557,-61.23888,12.97648), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.23888,12.97648,-61.24965,12.97557), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2597,12.97768,-61.23888,12.97648), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.22926,12.98088,-61.27958,12.98098), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.27958,12.98098,-61.22926,12.98088), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.26951,12.98189,-61.27958,12.98098), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.27956,12.98563,-61.27135,12.98934), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.27135,12.98934,-61.22219,12.99018), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.22219,12.99018,-61.27135,12.98934), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.22219,12.99018,-61.27135,12.98934), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.25964,12.99211,-61.22219,12.99018), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24511,12.9951,-61.21819,12.99599), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.21819,12.99599,-61.24511,12.9951), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.23618,13.00114,-61.21674,13.00343), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.21674,13.00343,-61.23618,13.00114), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2488,13.00675,-61.24364,13.0079), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24364,13.0079,-61.2488,13.00675), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.23615,13.00951,-61.21858,13.01088), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.21858,13.01088,-61.23615,13.00951), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24596,13.01349,-61.21858,13.01088), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.21411,13.01692,-61.24596,13.01349), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.20753,13.02156,-61.23772,13.02348), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.23772,13.02348,-61.20753,13.02156), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.20421,13.03063,-61.22434,13.03113), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.22434,13.03113,-61.20421,13.03063), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.21356,13.0332,-61.22434,13.03113), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2098,13.03785,-61.2009,13.0383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2009,13.0383,-61.2098,13.03785), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.20604,13.04087,-61.2009,13.0383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.13202,12.95092,-61.12804,12.95161), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12804,12.95161,-61.13202,12.95092), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.13529,12.95255,-61.12804,12.95161), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12428,12.95509,-61.13529,12.95255), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12333,12.95812,-61.13503,12.9593), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.13503,12.9593,-61.12333,12.95812), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12167,12.96091,-61.12518,12.96208), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12167,12.96091,-61.12518,12.96208), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12518,12.96208,-61.12986,12.96232), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.12986,12.96232,-61.12518,12.96208), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.18608,12.8533,-61.19239,12.85587), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.19239,12.85587,-61.18139,12.85632), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.18139,12.85632,-61.19239,12.85587), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.19447,12.86216,-61.17854,12.86539), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.17854,12.86539,-61.19447,12.86216), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1661,12.87025,-61.19138,12.8703), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.19138,12.8703,-61.1661,12.87025), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1736,12.87049,-61.19138,12.8703), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15884,12.87303,-61.1736,12.87049), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.19066,12.87588,-61.15884,12.87303), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.19066,12.87588,-61.15884,12.87303), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15763,12.87954,-61.18806,12.87983), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.18806,12.87983,-61.15763,12.87954), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.17213,12.88329,-61.16697,12.88491), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.16697,12.88491,-61.17213,12.88329), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.18522,12.88704,-61.15713,12.88791), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15713,12.88791,-61.17538,12.88818), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.17538,12.88818,-61.15713,12.88791), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.16062,12.89118,-61.17958,12.89191), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.17958,12.89191,-61.16062,12.89118), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2594,12.94255,-61.25237,12.94323), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.25237,12.94323,-61.2594,12.94255), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24745,12.94439,-61.26711,12.94466), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.26711,12.94466,-61.24745,12.94439), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2444,12.94555,-61.26711,12.94466), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2444,12.94555,-61.26711,12.94466), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.26616,12.94954,-61.24532,12.94997), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24532,12.94997,-61.26616,12.94954), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.25935,12.95279,-61.24532,12.94997), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24295,12.95601,-61.24458,12.95858), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.24458,12.95858,-61.25043,12.95882), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.25043,12.95882,-61.24458,12.95858), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.3943,12.57705,-61.39874,12.57938), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.3943,12.57705,-61.39874,12.57938), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.39874,12.57938,-61.38562,12.58076), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38562,12.58076,-61.40224,12.58172), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.40224,12.58172,-61.38562,12.58076), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.40176,12.58474,-61.38583,12.58564), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38583,12.58564,-61.40176,12.58474), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.398,12.58706,-61.39167,12.58798), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.39167,12.58798,-61.398,12.58706), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.44976,12.58018,-61.43617,12.58434), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.43617,12.58434,-61.45864,12.58461), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45864,12.58461,-61.43617,12.58434), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.42351,12.58827,-61.45931,12.59066), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45931,12.59066,-61.45602,12.59252), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45602,12.59252,-61.41295,12.59291), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.41295,12.59291,-61.45602,12.59252), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45133,12.59507,-61.41295,12.59291), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.41175,12.59965,-61.45248,12.60066), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45248,12.60066,-61.41175,12.59965), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.41993,12.60316,-61.45574,12.60415), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.45574,12.60415,-61.41993,12.60316), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.42693,12.60852,-61.4501,12.6088), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.4501,12.6088,-61.42693,12.60852), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.43416,12.61226,-61.44189,12.61227), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.44189,12.61227,-61.43416,12.61226), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.44189,12.61227,-61.43416,12.61226), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.39737,12.62173,-61.40228,12.62383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.40228,12.62383,-61.38892,12.62543), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38892,12.62543,-61.40228,12.62383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38212,12.62821,-61.40178,12.63011), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.40178,12.63011,-61.37883,12.63193), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.37883,12.63193,-61.40178,12.63011), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38045,12.63589,-61.37883,12.63193), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.39565,12.63987,-61.38394,12.64078), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38394,12.64078,-61.39565,12.63987), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.38394,12.64078,-61.39565,12.63987), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.39375,12.64429,-61.3886,12.64498), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.3886,12.64498,-61.39375,12.64429), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.34277,12.68375,-61.33292,12.68699), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33292,12.68699,-61.34533,12.68864), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.34533,12.68864,-61.32261,12.68976), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.32261,12.68976,-61.34533,12.68864), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.35441,12.6975,-61.34386,12.7012), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.34386,12.7012,-61.35065,12.70121), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.35065,12.70121,-61.34386,12.7012), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33636,12.70258,-61.31717,12.70301), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.31717,12.70301,-61.33636,12.70258), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33165,12.70885,-61.31339,12.71022), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.31339,12.71022,-61.33165,12.70885), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33678,12.71422,-61.31339,12.71022), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33931,12.72283,-61.30958,12.72394), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.30958,12.72394,-61.33931,12.72283), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.30958,12.72394,-61.33931,12.72283), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.30815,12.72859,-61.32102,12.73047), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.32102,12.73047,-61.33694,12.7305), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.33694,12.7305,-61.32102,12.73047), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.31422,12.73279,-61.32873,12.73351), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.32873,12.73351,-61.31422,12.73279), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.30812,12.7358,-61.32873,12.73351), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14475,12.9321,-61.14076,12.93349), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14076,12.93349,-61.14475,12.9321), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14825,12.9349,-61.14076,12.93349), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15058,12.93699,-61.14825,12.9349), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15058,12.93699,-61.14825,12.9349), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.13932,12.94093,-61.15195,12.94328), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15195,12.94328,-61.13932,12.94093), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14162,12.94908,-61.15074,12.95212), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.15074,12.95212,-61.14162,12.94908), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14815,12.95584,-61.14417,12.95653), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14417,12.95653,-61.14815,12.95584), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1844,13.1303,-61.1906,13.1311), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1844,13.1303,-61.1906,13.1311), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1906,13.1311,-61.1786,13.1314), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1786,13.1314,-61.1906,13.1311), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1731,13.1325,-61.1786,13.1314), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1947,13.1342,-61.1681,13.1344), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1681,13.1344,-61.1947,13.1342), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1981,13.1378,-61.165,13.1383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.165,13.1383,-61.1981,13.1378), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2028,13.14,-61.165,13.1383), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2069,13.1428,-61.1633,13.1436), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1633,13.1436,-61.2069,13.1428), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2128,13.1444,-61.2194,13.145), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2194,13.145,-61.2128,13.1444), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1617,13.1478,-61.2194,13.145), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2236,13.1478,-61.2194,13.145), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2269,13.1514,-61.1594,13.1525), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1594,13.1525,-61.2269,13.1514), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2311,13.1542,-61.155,13.155), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.155,13.155,-61.2311,13.1542), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2367,13.1558,-61.2436,13.1561), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2436,13.1561,-61.2367,13.1558), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1503,13.1569,-61.2436,13.1561), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2464,13.1606,-61.1478,13.1617), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1478,13.1617,-61.2464,13.1606), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2481,13.1653,-61.1481,13.1678), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1481,13.1678,-61.2508,13.1694), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2508,13.1694,-61.1481,13.1678), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1464,13.1731,-61.2533,13.1739), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2533,13.1739,-61.1464,13.1731), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2583,13.1761,-61.2533,13.1739), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2631,13.1783,-61.1447,13.1786), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1447,13.1786,-61.2631,13.1783), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2656,13.1825,-61.1431,13.1839), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1431,13.1839,-61.2656,13.1825), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2669,13.1881,-61.1417,13.1894), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1417,13.1894,-61.2669,13.1881), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2686,13.1928,-61.14,13.1947), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.14,13.1947,-61.2686,13.1928), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2711,13.1972,-61.14,13.1947), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1381,13.2,-61.2747,13.2008), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2747,13.2008,-61.1381,13.2), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2789,13.2036,-61.1358,13.2047), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1358,13.2047,-61.2789,13.2036), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2814,13.2078,-61.1342,13.2103), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1342,13.2103,-61.2814,13.2078), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2803,13.2139,-61.1325,13.2156), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1325,13.2156,-61.2803,13.2139), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2792,13.22,-61.13,13.2203), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.13,13.2203,-61.2792,13.22), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1283,13.2258,-61.2783,13.2261), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2783,13.2261,-61.1283,13.2258), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1269,13.2311,-61.2772,13.2322), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2772,13.2322,-61.1269,13.2311), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1261,13.2372,-61.2778,13.2386), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2778,13.2386,-61.1261,13.2372), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1242,13.2428,-61.2783,13.2447), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2783,13.2447,-61.1242,13.2428), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1233,13.2489,-61.2781,13.2503), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2781,13.2503,-61.1233,13.2489), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.275,13.2542,-61.1236,13.255), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1236,13.255,-61.275,13.2542), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2719,13.2581,-61.1239,13.2611), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1239,13.2611,-61.2694,13.2628), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2694,13.2628,-61.1239,13.2611), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2672,13.2675,-61.1239,13.2681), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1239,13.2681,-61.2672,13.2675), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2656,13.2731,-61.1233,13.2747), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1233,13.2747,-61.2656,13.2731), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2661,13.2792,-61.1222,13.2808), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1222,13.2808,-61.2661,13.2792), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2658,13.2858,-61.255,13.2875), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.255,13.2875,-61.1219,13.2878), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1219,13.2878,-61.2489,13.2881), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2608,13.2878,-61.2489,13.2881), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2489,13.2881,-61.1219,13.2878), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2444,13.2906,-61.2489,13.2881), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1211,13.2939,-61.2414,13.2944), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2414,13.2944,-61.1211,13.2939), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2383,13.2983,-61.1208,13.3008), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1208,13.3008,-61.2383,13.2983), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2369,13.3039,-61.1208,13.3008), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1203,13.3075,-61.2344,13.3086), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2344,13.3086,-61.1203,13.3075), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1208,13.3139,-61.2344,13.3086), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2328,13.3139,-61.2344,13.3086), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1219,13.3192,-61.2308,13.3194), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2308,13.3194,-61.1219,13.3192), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2289,13.3242,-61.1222,13.3256), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1222,13.3256,-61.2289,13.3242), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2258,13.3281,-61.1222,13.3256), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1236,13.3311,-61.2233,13.3328), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2233,13.3328,-61.1236,13.3311), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1256,13.3358,-61.2217,13.3381), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2217,13.3381,-61.1281,13.34), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1281,13.34,-61.2217,13.3381), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2208,13.3442,-61.1306,13.3444), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1306,13.3444,-61.2208,13.3442), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1342,13.3478,-61.2186,13.3489), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2186,13.3489,-61.1342,13.3478), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1361,13.3528,-61.2167,13.3544), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2167,13.3544,-61.1361,13.3528), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1369,13.3583,-61.2167,13.3544), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2139,13.3583,-61.2167,13.3544), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2114,13.3631,-61.1375,13.3644), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1375,13.3644,-61.2114,13.3631), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2089,13.3678,-61.1394,13.3694), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1394,13.3694,-61.2053,13.3708), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2053,13.3708,-61.1394,13.3694), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.2006,13.3728,-61.1411,13.3742), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1411,13.3742,-61.1961,13.3753), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1961,13.3753,-61.1522,13.3761), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1522,13.3761,-61.1581,13.3764), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1581,13.3764,-61.1522,13.3761), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1644,13.3772,-61.1917,13.3778), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1917,13.3778,-61.1475,13.3781), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1475,13.3781,-61.1439,13.3783), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1439,13.3783,-61.1475,13.3781), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1867,13.3797,-61.1683,13.3803), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1683,13.3803,-61.1867,13.3797), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1825,13.3822,-61.1725,13.3831), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1725,13.3831,-61.1825,13.3822), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")
	render_tiles((-61.1772,13.3842,-61.1725,13.3831), mapfile, tile_dir, 0, 11, "vc-st.-vincent-and-the-grenadines")