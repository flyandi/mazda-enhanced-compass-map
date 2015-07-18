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
    # Region: Hawaii
    # Region Name: HI

	render_tiles((-155.67201,18.91747,-155.63805,18.94172), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.63805,18.94172,-155.67201,18.91747), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.72604,18.96944,-155.61397,18.9704), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.61397,18.9704,-155.72604,18.96944), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.5907,19.00767,-155.80611,19.01397), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.80611,19.01397,-155.5907,19.00767), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.88155,19.03664,-155.80611,19.01397), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.55533,19.06938,-155.91422,19.09915), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.91422,19.09915,-155.55533,19.06938), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.50528,19.13791,-155.45352,19.15195), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.45352,19.15195,-155.50528,19.13791), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.91207,19.17911,-155.3907,19.20117), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.3907,19.20117,-155.36063,19.20893), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.36063,19.20893,-155.3907,19.20117), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.31337,19.2507,-155.90257,19.25843), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.90257,19.25843,-155.20589,19.26091), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.20589,19.26091,-155.90257,19.25843), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.15964,19.26838,-155.26462,19.27421), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.26462,19.27421,-155.15964,19.26838), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.11327,19.29061,-155.89084,19.29891), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.89084,19.29891,-155.11327,19.29061), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.02054,19.33132,-155.8887,19.34803), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.8887,19.34803,-155.02054,19.33132), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.94419,19.38185,-155.90909,19.41546), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.90909,19.41546,-154.87662,19.43322), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.87662,19.43322,-155.90909,19.41546), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.92473,19.45391,-154.87662,19.43322), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.95149,19.48665,-154.81601,19.50065), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.81601,19.50065,-155.95149,19.48665), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.81442,19.53009,-154.85262,19.54917), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.85262,19.54917,-155.96935,19.55596), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.96935,19.55596,-154.85262,19.54917), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.94711,19.60486,-155.97821,19.60816), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.97821,19.60816,-154.94711,19.60486), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.97434,19.6332,-155.99773,19.64282), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.99773,19.64282,-156.02898,19.6501), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.02898,19.6501,-155.99773,19.64282), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.03333,19.66923,-156.02898,19.6501), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-154.9811,19.69069,-156.03333,19.66923), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.08712,19.72801,-156.06436,19.73077), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.06436,19.73077,-155.08712,19.72801), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.00642,19.73929,-155.04538,19.73982), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.04538,19.73982,-155.00642,19.73929), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.09122,19.77637,-156.04965,19.78045), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.04965,19.78045,-155.09122,19.77637), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.04965,19.78045,-155.09122,19.77637), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.00627,19.81758,-155.97665,19.85053), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.97665,19.85053,-155.08634,19.8554), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.08634,19.8554,-155.94925,19.85703), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.94925,19.85703,-155.08634,19.8554), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.91566,19.88713,-155.12462,19.89729), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.12462,19.89729,-155.91566,19.88713), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.89253,19.93216,-155.16663,19.93789), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.16663,19.93789,-155.89253,19.93216), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.85659,19.96889,-155.83195,19.98278), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.83195,19.98278,-155.85659,19.96889), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.27032,20.01453,-155.82547,20.02594), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.82547,20.02594,-155.27032,20.01453), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.85039,20.06251,-155.38758,20.06712), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.38758,20.06712,-155.85039,20.06251), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.50256,20.11416,-155.89065,20.12358), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.89065,20.12358,-155.59803,20.12454), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.59803,20.12454,-155.89065,20.12358), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.55893,20.13157,-155.59803,20.12454), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.63746,20.15305,-155.55893,20.13157), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.90278,20.17707,-155.70433,20.1917), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.70433,20.1917,-155.90278,20.17707), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.737,20.22277,-155.70433,20.1917), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.79888,20.25412,-155.89066,20.25524), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.89066,20.25524,-155.79888,20.25412), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.85329,20.27155,-155.89066,20.25524), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.37763,20.57843,-156.28439,20.59649), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.28439,20.59649,-156.43187,20.59814), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.43187,20.59814,-156.28439,20.59649), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.1299,20.62752,-156.21026,20.62852), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.21026,20.62852,-156.1299,20.62752), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.44367,20.65602,-156.04379,20.6649), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.04379,20.6649,-156.44367,20.65602), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.00187,20.69806,-156.04379,20.6649), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.45844,20.73668,-155.98541,20.74425), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-155.98541,20.74425,-156.45844,20.73668), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.46224,20.75395,-155.98541,20.74425), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.53775,20.77841,-156.55462,20.7861), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.55462,20.7861,-156.47356,20.79076), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.47356,20.79076,-156.55462,20.7861), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.00353,20.79555,-156.50603,20.79946), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.50603,20.79946,-156.00353,20.79555), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.05979,20.81054,-156.63179,20.82124), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.63179,20.82124,-156.05979,20.81054), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.13267,20.86137,-156.6878,20.89072), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.6878,20.89072,-156.19471,20.89198), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.19471,20.89198,-156.6878,20.89072), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.48106,20.8982,-156.19471,20.89198), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.4033,20.91583,-156.69989,20.92063), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.69989,20.92063,-156.4033,20.91583), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.69989,20.92063,-156.4033,20.91583), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.24256,20.93784,-156.33282,20.94645), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.33282,20.94645,-156.51871,20.95466), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.51871,20.95466,-156.33282,20.94645), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.68091,20.98026,-156.51871,20.95466), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.56277,21.01617,-156.61958,21.02779), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.61958,21.02779,-156.56277,21.01617), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.66881,20.50474,-156.58624,20.51171), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.58624,20.51171,-156.66881,20.50474), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.53964,20.52764,-156.70227,20.53245), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.70227,20.53245,-156.53964,20.52764), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.67047,20.55991,-156.54303,20.58012), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.67047,20.55991,-156.54303,20.58012), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.54303,20.58012,-156.61073,20.59377), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.61073,20.59377,-156.56714,20.6049), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.56714,20.6049,-156.61073,20.59377), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.96789,20.73508,-156.90908,20.73953), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.90908,20.73953,-156.96789,20.73508), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.8903,20.74486,-156.90908,20.73953), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.83832,20.76458,-156.99068,20.7759), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.99068,20.7759,-156.83832,20.76458), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.80847,20.8204,-156.99183,20.8266), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.99183,20.8266,-156.80847,20.8204), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.01091,20.85448,-156.83705,20.86358), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.83705,20.86358,-157.01091,20.85448), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.05966,20.88463,-156.87313,20.89468), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.87313,20.89468,-157.05966,20.88463), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.05913,20.91341,-156.93753,20.92527), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.05913,20.91341,-156.93753,20.92527), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.93753,20.92527,-157.01,20.92976), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.01,20.92976,-156.93753,20.92527), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.87714,21.0493,-156.95387,21.06613), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.95387,21.06613,-156.8022,21.0671), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.8022,21.0671,-156.95387,21.06613), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.25253,21.08767,-157.02617,21.08902), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.02617,21.08902,-157.25253,21.08767), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.17161,21.0907,-157.02617,21.08902), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.31075,21.10163,-157.08066,21.10198), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.08066,21.10198,-157.31075,21.10163), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.73934,21.11134,-157.08066,21.10198), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.27722,21.15843,-156.70911,21.15866), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.27722,21.15843,-156.70911,21.15866), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.70911,21.15866,-157.27722,21.15843), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.84159,21.16793,-156.91786,21.16902), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.91786,21.16902,-156.92111,21.16907), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.92111,21.16907,-156.91786,21.16902), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.74223,21.17621,-156.92111,21.16907), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.2497,21.1844,-157.03999,21.19091), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.03999,21.19091,-157.2497,21.1844), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.01427,21.20069,-157.12821,21.20149), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.12821,21.20149,-157.01427,21.20069), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.96285,21.21213,-156.98403,21.2122), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-156.98403,21.2122,-156.96285,21.21213), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.20213,21.2193,-157.26069,21.22568), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.26069,21.22568,-157.20213,21.2193), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.8096,21.2577,-157.7001,21.264), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.7001,21.264,-157.77994,21.26525), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.77994,21.26525,-157.7001,21.264), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.7572,21.278,-157.67307,21.2842), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.67307,21.2842,-157.85105,21.28453), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.85105,21.28453,-157.67307,21.2842), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.1033,21.2979,-158.0883,21.2988), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.0883,21.2988,-158.1033,21.2979), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.89,21.3065,-157.65511,21.30928), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.65511,21.30928,-158.0245,21.3093), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.0245,21.3093,-157.65511,21.30928), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.95074,21.31251,-157.6518,21.3139), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.6518,21.3139,-157.95074,21.31251), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.98153,21.3159,-157.6518,21.3139), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.12937,21.34482,-158.13093,21.34896), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.13093,21.34896,-158.12937,21.34482), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.7106,21.3585,-158.13093,21.34896), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.1403,21.3738,-157.7106,21.3585), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.72432,21.40331,-158.1792,21.4043), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.1792,21.4043,-157.72432,21.40331), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.18265,21.43007,-157.8139,21.4403), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.8139,21.4403,-158.18265,21.43007), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.72251,21.45923,-157.76457,21.46134), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.76457,21.46134,-157.72251,21.45923), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.84549,21.46675,-157.76457,21.46134), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.233,21.4876,-157.84549,21.46675), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.23117,21.52386,-157.83695,21.52995), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.83695,21.52995,-158.23117,21.52386), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.87735,21.57528,-158.27768,21.57879), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.27768,21.57879,-157.87735,21.57528), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.23219,21.58381,-158.12561,21.58674), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.23219,21.58381,-158.12561,21.58674), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.12561,21.58674,-158.23219,21.58381), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.0799,21.6281,-157.92459,21.65118), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.92459,21.65118,-158.05069,21.67122), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-158.05069,21.67122,-157.92459,21.65118), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.9923,21.708,-157.96863,21.7127), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-157.96863,21.7127,-157.9923,21.708), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.44487,21.86863,-159.52692,21.88389), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.52692,21.88389,-159.60328,21.89225), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.60328,21.89225,-159.57452,21.89281), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.57452,21.89281,-159.60328,21.89225), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.38527,21.91244,-159.57452,21.89281), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.64977,21.93385,-159.33768,21.95117), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.33768,21.95117,-159.7078,21.96123), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.7078,21.96123,-159.33768,21.95117), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.7548,21.97777,-159.7078,21.96123), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.33256,21.99935,-159.7867,22.0188), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.7867,22.0188,-159.33256,21.99935), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.33449,22.0417,-159.31828,22.06142), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.31828,22.06142,-159.78375,22.0649), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.78375,22.0649,-159.31828,22.06142), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.78375,22.0649,-159.31828,22.06142), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.74525,22.09751,-159.29301,22.12296), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.29301,22.12296,-159.73054,22.13995), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.73054,22.13995,-159.29301,22.12296), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.70553,22.15932,-159.73054,22.13995), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.31229,22.18308,-159.61165,22.20139), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.61165,22.20139,-159.51076,22.20355), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.51076,22.20355,-159.61165,22.20139), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.36151,22.21409,-159.43171,22.22002), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.43171,22.22002,-159.54392,22.2217), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.54392,22.2217,-159.43171,22.22002), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.58106,22.22349,-159.54392,22.2217), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.48794,22.22951,-159.40247,22.2326), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-159.40247,22.2326,-159.48794,22.22951), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.20585,21.77952,-160.23037,21.78968), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.23037,21.78968,-160.20585,21.77952), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.24961,21.81515,-160.18978,21.82245), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.24961,21.81515,-160.18978,21.82245), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.18978,21.82245,-160.24961,21.81515), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.1748,21.84692,-160.15609,21.86793), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.15609,21.86793,-160.12428,21.87679), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.12428,21.87679,-160.15609,21.86793), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.22897,21.88912,-160.07907,21.89608), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.07907,21.89608,-160.22897,21.88912), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.19396,21.92239,-160.08579,21.9273), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.08579,21.9273,-160.19396,21.92239), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.13705,21.94863,-160.12226,21.96288), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.12226,21.96288,-160.07029,21.96395), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.07029,21.96395,-160.12226,21.96288), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.05113,21.98106,-160.11275,21.99525), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.11275,21.99525,-160.05854,21.99638), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.05854,21.99638,-160.11275,21.99525), mapfile, tile_dir, 0, 11, "hawaii-hi")
	render_tiles((-160.07212,22.00333,-160.05854,21.99638), mapfile, tile_dir, 0, 11, "hawaii-hi")