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
    # Region: Missouri
    # Region Name: MO

	render_tiles((-90.36872,35.99581,-90.28895,35.99651), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.28895,35.99651,-90.36872,35.99581), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.10384,35.99814,-89.95938,35.99901), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.95938,35.99901,-89.90118,35.99937), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.90118,35.99937,-89.95938,35.99901), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.7331,36.00061,-89.90118,35.99937), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.69244,36.02051,-89.7331,36.00061), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.33934,36.04711,-89.69244,36.02051), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.68003,36.08249,-89.64302,36.10362), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.64302,36.10362,-90.29449,36.11295), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.29449,36.11295,-89.64302,36.10362), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.5921,36.13564,-90.23559,36.13947), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.23559,36.13947,-89.5921,36.13564), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.6238,36.18313,-90.22043,36.18476), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.22043,36.18476,-89.62764,36.18546), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.62764,36.18546,-90.22043,36.18476), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.18913,36.19899,-89.62764,36.18546), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.15593,36.21407,-89.69263,36.22496), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.69263,36.22496,-90.15593,36.21407), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.60237,36.23811,-89.67805,36.24828), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.67805,36.24828,-89.60237,36.23811), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.11492,36.2656,-89.55429,36.27775), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.55429,36.27775,-90.11492,36.2656), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.06398,36.30304,-89.61182,36.30909), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.61182,36.30909,-90.06398,36.30304), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.60054,36.34299,-89.54503,36.34427), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.54503,36.34427,-89.5227,36.34479), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.5227,36.34479,-89.54503,36.34427), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.06353,36.35691,-89.5227,36.34479), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.51038,36.37836,-90.06614,36.38627), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.06614,36.38627,-89.51038,36.37836), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.13104,36.41507,-89.54234,36.4201), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.54234,36.4201,-90.13104,36.41507), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.1414,36.45987,-89.52102,36.46193), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.52102,36.46193,-90.1414,36.45987), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.15387,36.49534,-90.22075,36.49594), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.22075,36.49594,-90.15387,36.49534), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.40492,36.49712,-91.40714,36.49714), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.40714,36.49714,-91.40492,36.49712), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.45,36.49754,-92.35028,36.49779), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.35028,36.49779,-91.12654,36.4978), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.12654,36.4978,-92.35028,36.49779), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.12597,36.49785,-91.12654,36.4978), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.53923,36.49793,-93.12597,36.49785), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.85405,36.49802,-92.83888,36.49803), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.83888,36.49803,-92.85405,36.49802), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.01797,36.49806,-92.77233,36.49808), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.77233,36.49808,-91.01797,36.49806), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.15031,36.49814,-92.52914,36.49817), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.52914,36.49817,-92.12043,36.49819), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.12043,36.49819,-92.52914,36.49817), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.56424,36.49824,-93.29345,36.49826), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.29345,36.49826,-92.56424,36.49824), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.31533,36.49831,-93.29345,36.49826), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.49458,36.49837,-90.57618,36.49841), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.57618,36.49841,-91.9858,36.49843), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.9858,36.49843,-90.57618,36.49841), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.78424,36.49846,-90.76567,36.49849), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.76567,36.49849,-90.78424,36.49846), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.42699,36.49859,-90.76567,36.49849), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.95919,36.49872,-93.42699,36.49859), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.86676,36.49887,-93.58428,36.4989), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.58428,36.4989,-93.86676,36.49887), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.07709,36.49898,-93.58428,36.4989), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.70017,36.49914,-91.67234,36.49926), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.67234,36.49926,-91.64259,36.49934), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.64259,36.49934,-94.61792,36.49941), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61792,36.49941,-91.64259,36.49934), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.3612,36.4996,-94.61792,36.49941), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.57148,36.53809,-89.40791,36.56235), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.40791,36.56235,-89.47935,36.56625), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.47935,36.56625,-89.22732,36.56938), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.22732,36.56938,-89.47935,36.56625), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.54443,36.57451,-89.27894,36.5777), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.27894,36.5777,-89.54443,36.57451), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61782,36.6126,-89.37869,36.62229), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.37869,36.62229,-89.32732,36.62395), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.32732,36.62395,-89.32466,36.62403), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.32466,36.62403,-89.32732,36.62395), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.19914,36.62565,-89.32466,36.62403), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.17565,36.65132,-89.16549,36.66243), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.16549,36.66243,-94.61799,36.66792), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61799,36.66792,-89.16549,36.66243), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.20251,36.71662,-89.15699,36.75597), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.15699,36.75597,-94.61831,36.76656), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61831,36.76656,-89.15699,36.75597), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.15598,36.78629,-89.15589,36.78913), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.15589,36.78913,-89.15598,36.78629), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.14767,36.84715,-89.12047,36.8919), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.12047,36.8919,-89.14767,36.84715), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.10314,36.94476,-89.09884,36.95785), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.09884,36.95785,-89.10314,36.94476), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.13292,36.98206,-89.19504,36.98977), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.19504,36.98977,-89.13292,36.98206), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61796,36.99891,-89.19504,36.98977), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.25761,37.0155,-89.30744,37.02876), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.30744,37.02876,-89.25761,37.0155), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.35946,37.04261,-89.30744,37.02876), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.6181,37.0568,-89.35946,37.04261), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.38418,37.10327,-94.6181,37.0568), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61835,37.16021,-89.45611,37.18812), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.45611,37.18812,-94.61835,37.16021), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.47053,37.25336,-89.48289,37.26095), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.48289,37.26095,-89.47053,37.25336), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.51703,37.28192,-89.48289,37.26095), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.49516,37.3248,-89.47368,37.33485), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.47368,37.33485,-94.61775,37.33842), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61775,37.33842,-89.47368,37.33485), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.42819,37.35616,-94.61767,37.36417), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61767,37.36417,-89.42819,37.35616), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.42594,37.40747,-94.61751,37.41091), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61751,37.41091,-89.42594,37.40747), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.4712,37.46647,-94.61751,37.41091), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.5124,37.52981,-89.50179,37.5589), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.50179,37.5589,-89.49775,37.56999), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.49775,37.56999,-89.49405,37.58012), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.49405,37.58012,-89.49775,37.56999), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.50656,37.62505,-94.61785,37.65358), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61785,37.65358,-94.61787,37.67311), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61787,37.67311,-94.61789,37.68221), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61789,37.68221,-94.61787,37.67311), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.52195,37.69648,-94.61789,37.68221), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.59129,37.7236,-89.52195,37.69648), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.66799,37.75948,-89.59129,37.7236), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.68722,37.79641,-89.69656,37.81434), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.69656,37.81434,-89.68722,37.79641), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.78204,37.85509,-89.92319,37.87067), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.92319,37.87067,-89.9331,37.8801), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.9331,37.8801,-89.92319,37.87067), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.85105,37.90398,-89.97422,37.91922), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.97422,37.91922,-89.85105,37.90398), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-89.95491,37.96665,-90.00835,37.97018), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.00835,37.97018,-89.95491,37.96665), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61447,37.9878,-90.00835,37.97018), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.08096,38.01543,-94.6141,38.03706), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.6141,38.03706,-90.12601,38.05057), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.12601,38.05057,-94.61393,38.06005), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61393,38.06005,-90.12601,38.05057), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.20573,38.08823,-90.21871,38.09437), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.21871,38.09437,-90.20573,38.08823), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.25248,38.12757,-90.25275,38.12777), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.25275,38.12777,-90.25248,38.12757), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.32235,38.18159,-90.35116,38.21954), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.35116,38.21954,-90.36393,38.23636), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.36393,38.23636,-94.61261,38.23777), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61261,38.23777,-90.36393,38.23636), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.37252,38.32335,-90.34974,38.37761), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.34974,38.37761,-90.34292,38.38443), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.34292,38.38443,-90.34024,38.38709), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.34024,38.38709,-94.61277,38.38872), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61277,38.38872,-90.34024,38.38709), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.28882,38.43845,-94.61287,38.47757), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61287,38.47757,-94.61287,38.4776), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61287,38.4776,-94.61287,38.47757), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.27131,38.49605,-94.61287,38.4776), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.26098,38.51853,-90.25529,38.53088), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.25529,38.53088,-90.26098,38.51853), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.24891,38.54475,-94.61196,38.54763), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.61196,38.54763,-90.24891,38.54475), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.18451,38.61155,-90.18111,38.65955), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.18111,38.65955,-90.18152,38.66037), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.18152,38.66037,-90.18111,38.65955), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.19521,38.68755,-90.18152,38.66037), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.20991,38.72605,-94.60949,38.7381), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60949,38.7381,-94.60946,38.7407), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60946,38.7407,-94.60949,38.7381), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.16659,38.77245,-90.16641,38.77265), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.16641,38.77265,-90.16659,38.77245), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.11771,38.80575,-90.16641,38.77265), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60896,38.84721,-90.11333,38.84931), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.11333,38.84931,-94.60896,38.84721), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.55569,38.87079,-90.59535,38.87505), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.59535,38.87505,-90.55569,38.87079), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.20728,38.89873,-90.50012,38.91041), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.50012,38.91041,-90.23034,38.91086), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.23034,38.91086,-90.50012,38.91041), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.27658,38.91934,-90.65725,38.92027), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.65725,38.92027,-90.27658,38.91934), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.29871,38.9234,-90.65725,38.92027), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.66158,38.9347,-90.29871,38.9234), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.39582,38.96004,-90.45097,38.9614), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.45097,38.9614,-90.46778,38.96181), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.46778,38.96181,-90.45097,38.9614), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60833,38.98181,-90.6764,38.9841), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.6764,38.9841,-94.60833,38.98181), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60787,39.04409,-90.71363,39.05398), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.71363,39.05398,-94.60787,39.04409), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.68109,39.10059,-94.60735,39.11344), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60735,39.11344,-90.68109,39.10059), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.7079,39.15086,-94.59193,39.155), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.59193,39.155,-94.60194,39.1555), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.60194,39.1555,-94.59193,39.155), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.62393,39.1566,-94.60194,39.1555), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.74194,39.1702,-94.62393,39.1566), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.68034,39.1843,-94.74194,39.1702), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.79199,39.20126,-94.79966,39.20602), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.79966,39.20602,-94.79199,39.20126), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.72328,39.2241,-94.82566,39.24173), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.82566,39.24173,-90.72996,39.25589), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.72996,39.25589,-94.82566,39.24173), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.85707,39.27383,-90.72996,39.25589), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.90807,39.32366,-90.84011,39.34044), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.84011,39.34044,-94.90807,39.32366), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.88897,39.39243,-90.93535,39.39952), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.93535,39.39952,-94.94666,39.39972), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.94666,39.39972,-90.93535,39.39952), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-90.93742,39.4008,-94.94666,39.39972), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.96575,39.42168,-94.98214,39.44055), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.98214,39.44055,-91.03827,39.44844), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.03827,39.44844,-94.98214,39.44055), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.04985,39.49442,-91.06431,39.49464), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.06431,39.49464,-95.04985,39.49442), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.09142,39.53326,-91.10031,39.5387), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.10031,39.5387,-95.09142,39.53326), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.14828,39.5458,-91.10031,39.5387), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.11356,39.55394,-91.14828,39.5458), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.07669,39.57676,-91.17423,39.59198), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.17423,39.59198,-95.04717,39.59512), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.04717,39.59512,-91.18288,39.59823), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.18288,39.59823,-95.04717,39.59512), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.04405,39.61367,-91.18288,39.59823), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.03746,39.65291,-91.27614,39.66576), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.27614,39.66576,-95.03746,39.65291), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.30576,39.68622,-94.97132,39.68641), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.97132,39.68641,-91.30576,39.68622), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.97108,39.72315,-94.89932,39.72404), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.89932,39.72404,-94.97108,39.72315), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.36775,39.72903,-94.89932,39.72404), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.86037,39.74953,-91.36462,39.75872), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.36462,39.75872,-94.86037,39.74953), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.87114,39.77299,-91.36462,39.75872), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.36157,39.78755,-94.87114,39.77299), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.87782,39.82041,-91.39785,39.82112), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.39785,39.82112,-94.87782,39.82041), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.87868,39.82652,-91.39785,39.82112), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.43605,39.84551,-95.08153,39.86172), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.08153,39.86172,-94.92847,39.87634), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.92847,39.87634,-95.08153,39.86172), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.14245,39.89542,-95.01874,39.89737), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.01874,39.89737,-94.99337,39.89857), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.99337,39.89857,-95.01874,39.89737), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.95154,39.90053,-94.99337,39.89857), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.42896,39.90773,-94.95154,39.90053), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.23111,39.94378,-91.43684,39.94524), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.43684,39.94524,-91.43709,39.94642), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.43709,39.94642,-91.43684,39.94524), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.30829,40,-91.48406,40.01933), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.48406,40.01933,-95.38296,40.02711), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.38296,40.02711,-95.34878,40.0293), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.34878,40.0293,-95.38296,40.02711), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.41473,40.06982,-91.49766,40.07826), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.49766,40.07826,-95.41473,40.06982), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.39422,40.10826,-91.49766,40.07826), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.43217,40.14103,-91.51196,40.17044), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.51196,40.17044,-95.48102,40.18852), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.48102,40.18852,-91.50617,40.20064), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.50617,40.20064,-95.48102,40.18852), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.47255,40.23608,-91.49696,40.2487), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.49696,40.2487,-95.54716,40.25907), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.54716,40.25907,-95.54787,40.26278), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.54787,40.26278,-95.54818,40.26441), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.54818,40.26441,-95.54787,40.26278), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.49289,40.26992,-95.54818,40.26441), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.55329,40.29116,-95.59866,40.30981), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.59866,40.30981,-91.46966,40.32241), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.46966,40.32241,-95.65373,40.32258), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.65373,40.32258,-91.46966,40.32241), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.64103,40.3664,-91.41942,40.37826), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.41942,40.37826,-95.64103,40.3664), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.64942,40.39615,-91.49809,40.40193), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.49809,40.40193,-95.64942,40.39615), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.51913,40.43282,-91.56384,40.46099), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.56384,40.46099,-95.68436,40.46337), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.68436,40.46337,-91.56384,40.46099), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.69473,40.4936,-91.60835,40.50004), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.60835,40.50004,-95.69473,40.4936), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.71228,40.52375,-95.75711,40.52599), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.75711,40.52599,-95.71429,40.52721), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.71429,40.52721,-95.75711,40.52599), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.619,40.53908,-91.67099,40.55094), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.67099,40.55094,-91.619,40.53908), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.53388,40.57074,-94.47121,40.57096), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.47121,40.57096,-94.53388,40.57074), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.31072,40.57152,-94.63203,40.57176), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.63203,40.57176,-94.31072,40.57152), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.23224,40.57201,-94.63203,40.57176), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.09109,40.5729,-94.81998,40.57371), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.81998,40.57371,-94.01549,40.57407), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.01549,40.57407,-94.81998,40.57371), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-94.9149,40.57492,-94.01549,40.57407), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.84093,40.57679,-95.06892,40.57688), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.06892,40.57688,-93.84093,40.57679), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.77434,40.57753,-95.06892,40.57688), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.20227,40.57838,-91.68538,40.57889), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.68538,40.57889,-95.20227,40.57838), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.59735,40.5795,-93.5569,40.57966), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.5569,40.57966,-93.59735,40.5795), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.33559,40.57987,-93.5569,40.57966), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.37393,40.58033,-93.37439,40.5804), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.37439,40.5804,-95.37393,40.58033), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.34544,40.58051,-93.37439,40.5804), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.53318,40.58225,-93.1358,40.58285), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.1358,40.58285,-95.53318,40.58225), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-93.09729,40.58382,-93.1358,40.58285), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.76565,40.58521,-93.09729,40.58382), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-95.76565,40.58521,-93.09729,40.58382), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.9416,40.58774,-92.7146,40.58958), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.7146,40.58958,-92.68669,40.58981), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.68669,40.58981,-92.7146,40.58958), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.6379,40.59096,-92.68669,40.58981), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.45375,40.59529,-92.3508,40.59726), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.3508,40.59726,-92.45375,40.59529), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-92.17978,40.60053,-91.71665,40.60374), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.71665,40.60374,-91.94312,40.60606), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.94312,40.60606,-91.93929,40.60615), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.93929,40.60615,-91.94312,40.60606), mapfile, tile_dir, 0, 11, "missouri-mo")
	render_tiles((-91.72912,40.61364,-91.93929,40.60615), mapfile, tile_dir, 0, 11, "missouri-mo")