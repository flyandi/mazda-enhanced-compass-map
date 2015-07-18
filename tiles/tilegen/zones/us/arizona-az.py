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
    # Region: Arizona
    # Region Name: AZ

	render_tiles((-111.07483,31.33224,-109.05004,31.3325), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.05004,31.3325,-111.07483,31.33224), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-110.46017,31.33314,-109.05004,31.3325), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.82969,31.33407,-110.46017,31.33314), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-111.36697,31.42482,-109.82969,31.33407), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.36504,31.74113,-109.0492,31.79655), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.0492,31.79655,-112.36504,31.74113), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-113.33377,32.04025,-109.0483,32.08409), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.0483,32.08409,-113.33377,32.04025), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-113.75076,32.16901,-109.0483,32.08409), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04761,32.42638,-114.81361,32.49428), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.81361,32.49428,-114.81154,32.52283), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.81154,32.52283,-114.79564,32.55096), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.79564,32.55096,-114.81419,32.56479), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.81419,32.56479,-114.79564,32.55096), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.79968,32.59362,-114.80939,32.61712), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.79968,32.59362,-114.80939,32.61712), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.80939,32.61712,-114.79968,32.59362), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.76495,32.64939,-114.80939,32.61712), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.71963,32.71876,-114.66749,32.73423), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.66749,32.73423,-114.61739,32.74105), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.61739,32.74105,-114.70572,32.74158), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70572,32.74158,-114.61739,32.74105), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.57068,32.74742,-114.70572,32.74158), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04712,32.77757,-114.53175,32.7825), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04712,32.77757,-114.53175,32.7825), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.53175,32.7825,-109.04712,32.77757), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.46897,32.84516,-114.46313,32.90188), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.46313,32.90188,-114.47664,32.92363), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.47664,32.92363,-114.46313,32.90188), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.48132,32.97206,-114.47664,32.92363), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.51134,33.02346,-114.51707,33.02463), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.51707,33.02463,-114.51134,33.02346), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62829,33.03105,-114.57516,33.03654), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.57516,33.03654,-114.6708,33.03798), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.6708,33.03798,-114.57516,33.03654), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70618,33.10534,-114.67936,33.15952), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.67936,33.15952,-109.04724,33.20897), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04724,33.20897,-114.6781,33.2303), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.6781,33.2303,-109.04724,33.20897), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.67449,33.2556,-114.6781,33.2303), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.72326,33.28808,-114.67449,33.2556), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70796,33.32342,-114.72326,33.28808), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70735,33.37663,-114.72528,33.40505), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.72528,33.40505,-109.0473,33.40978), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.0473,33.40978,-114.72528,33.40505), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.6739,33.4183,-114.63518,33.42273), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.63518,33.42273,-114.6739,33.4183), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62915,33.43355,-114.63518,33.42273), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.59728,33.49065,-114.62915,33.43355), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.5246,33.55223,-114.52919,33.60665), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.52919,33.60665,-114.5246,33.55223), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.5252,33.66158,-114.50499,33.69302), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.50499,33.69302,-114.49657,33.71916), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.49657,33.71916,-114.50499,33.69302), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.50486,33.76047,-109.04661,33.77823), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04661,33.77823,-114.50486,33.76047), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.52047,33.82778,-114.50564,33.86428), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.50564,33.86428,-109.04643,33.87505), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04643,33.87505,-114.50564,33.86428), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.50871,33.90064,-109.04643,33.87505), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.53499,33.9285,-114.50871,33.90064), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.50957,33.95726,-114.53499,33.9285), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.45481,34.01097,-114.4355,34.04262), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.4355,34.04262,-114.45481,34.01097), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.43009,34.07893,-114.42803,34.09279), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.42803,34.09279,-114.43009,34.07893), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.40594,34.11154,-114.42803,34.09279), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.34805,34.13446,-114.40594,34.11154), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.29281,34.16673,-114.22972,34.18693), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.22972,34.18693,-114.29281,34.16673), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.17805,34.23997,-114.13906,34.25954), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.13906,34.25954,-114.17805,34.23997), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.14082,34.30313,-114.14093,34.30592), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.14093,34.30592,-114.14082,34.30313), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.17285,34.34498,-114.14093,34.30592), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.26432,34.40133,-114.33537,34.45004), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.33537,34.45004,-114.37885,34.45038), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.37885,34.45038,-114.33537,34.45004), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.37822,34.51652,-109.04618,34.52239), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04618,34.52239,-114.37822,34.51652), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04614,34.57929,-114.42238,34.58071), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.42238,34.58071,-109.04614,34.57929), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.46525,34.6912,-114.49097,34.72485), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.49097,34.72485,-114.46525,34.6912), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.57645,34.8153,-114.63438,34.87289), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.63438,34.87289,-114.57645,34.8153), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62977,34.94304,-109.04585,34.95972), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04585,34.95972,-114.62977,34.94304), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.63349,35.00186,-109.04585,34.95972), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62507,35.06848,-114.59912,35.12105), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.59912,35.12105,-114.61991,35.12163), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.61991,35.12163,-114.59912,35.12105), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.57275,35.13873,-114.61991,35.12163), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04635,35.17468,-114.57275,35.13873), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.58713,35.26238,-109.04635,35.17468), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.0468,35.36361,-114.62714,35.4095), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62714,35.4095,-114.6645,35.4495), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.6645,35.4495,-114.62714,35.4095), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.66311,35.52449,-114.6645,35.4495), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.65341,35.61079,-109.0463,35.61425), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.0463,35.61425,-114.65341,35.61079), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.68941,35.65141,-109.0463,35.61425), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.69731,35.73369,-114.70371,35.81459), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70371,35.81459,-114.66969,35.86508), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.66969,35.86508,-109.04602,35.8798), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04602,35.8798,-114.66969,35.86508), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.70027,35.90177,-109.04602,35.8798), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.73116,35.94392,-114.70027,35.90177), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04587,36.00234,-114.74278,36.00996), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.74278,36.00996,-114.21369,36.01561), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.21369,36.01561,-114.74278,36.00996), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.15173,36.02456,-114.21369,36.01561), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.27065,36.03572,-114.15173,36.02456), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.1382,36.05316,-114.31611,36.06311), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.31611,36.06311,-114.7433,36.06594), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.7433,36.06594,-114.31611,36.06311), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.74334,36.07054,-114.7433,36.06594), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.73617,36.10437,-114.33727,36.10802), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.33727,36.10802,-114.73617,36.10437), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04573,36.11703,-114.66654,36.11734), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.66654,36.11734,-109.04573,36.11703), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.09987,36.12165,-114.66654,36.11734), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.44865,36.12641,-114.48703,36.1294), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.48703,36.1294,-114.44865,36.12641), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.62786,36.14101,-114.37211,36.14311), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.37211,36.14311,-114.62786,36.14101), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.41695,36.14576,-114.37211,36.14311), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.51172,36.15096,-114.57203,36.15161), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.57203,36.15161,-114.51172,36.15096), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.04684,36.19407,-114.57203,36.15161), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.04823,36.26887,-114.04758,36.32557), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.04758,36.32557,-114.04823,36.26887), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.04949,36.60406,-114.05016,36.84314), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.05016,36.84314,-109.04543,36.87459), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04543,36.87459,-114.05016,36.84314), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-110.00068,36.99797,-110.47019,36.998), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-110.47019,36.998,-110.00068,36.99797), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.04522,36.99908,-109.49534,36.99911), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-109.49534,36.99911,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-113.96591,37.00003,-112.96647,37.00022), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.96647,37.00022,-112.89919,37.0003), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.89919,37.0003,-112.96647,37.00022), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.8295,37.00039,-114.0506,37.0004), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-114.0506,37.0004,-112.8295,37.00039), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-111.27829,37.00047,-114.0506,37.0004), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.54509,37.00073,-112.53857,37.00074), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.53857,37.00074,-112.54509,37.00073), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-112.35769,37.00103,-112.53857,37.00074), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-111.40587,37.00148,-112.35769,37.00103), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-111.41278,37.00148,-112.35769,37.00103), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-111.0665,37.00239,-110.75069,37.0032), mapfile, tile_dir, 0, 11, "arizona-az")
	render_tiles((-110.75069,37.0032,-111.0665,37.00239), mapfile, tile_dir, 0, 11, "arizona-az")