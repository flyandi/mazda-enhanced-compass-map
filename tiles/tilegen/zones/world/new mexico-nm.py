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
    # Region: New Mexico
    # Region Name: NM

	render_tiles((-108.86103,31.33232,-109.05004,31.3325), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.05004,31.3325,-108.86103,31.33232), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.70766,31.33319,-108.20857,31.3334), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.20857,31.3334,-108.70766,31.33319), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.20857,31.49974,-108.20857,31.3334), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.52824,31.78315,-108.20839,31.7836), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.20839,31.7836,-107.29682,31.78363), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-107.42225,31.7836,-107.29682,31.78363), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-107.29682,31.78363,-108.20839,31.7836), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.99354,31.78369,-107.29682,31.78363), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.53173,31.78391,-106.99354,31.78369), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0492,31.79655,-106.53173,31.78391), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0492,31.79655,-106.53173,31.78391), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.58134,31.81391,-109.0492,31.79655), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.63588,31.87151,-106.62345,31.91403), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.62345,31.91403,-106.63588,31.87151), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.63011,31.97126,-104.02452,32.00001), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.02452,32.00001,-103.98021,32.00003), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.98021,32.00003,-104.02452,32.00001), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.72285,32.00017,-103.98021,32.00003), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.3265,32.00037,-104.64353,32.00044), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.64353,32.00044,-104.84776,32.00046), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.84776,32.00046,-104.9183,32.00047), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.9183,32.00047,-104.84776,32.00046), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.61849,32.0005,-103.06442,32.00052), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.15399,32.0005,-103.06442,32.00052), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06442,32.00052,-106.61849,32.0005), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.37717,32.00124,-106.2007,32.00179), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.2007,32.00179,-105.99797,32.00197), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.99797,32.00197,-106.2007,32.00179), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.75053,32.00221,-105.99797,32.00197), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0483,32.08409,-103.06442,32.08705), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06442,32.08705,-109.0483,32.08409), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06442,32.14501,-103.06442,32.08705), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04761,32.42638,-103.0647,32.52219), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.0647,32.52219,-103.06476,32.58798), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06476,32.58798,-103.0647,32.52219), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04712,32.77757,-103.06489,32.84936), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04712,32.77757,-103.06489,32.84936), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06489,32.84936,-109.04712,32.77757), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.06347,32.9591,-103.06489,32.84936), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04724,33.20897,-103.0601,33.21923), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.0601,33.21923,-109.04724,33.20897), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.0565,33.38841,-109.0473,33.40978), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0473,33.40978,-103.0565,33.38841), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.05261,33.5706,-109.0473,33.40978), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04661,33.77823,-103.04735,33.82468), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04735,33.82468,-109.04661,33.77823), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04643,33.87505,-103.04735,33.82468), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04352,34.07938,-103.04356,34.11283), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04356,34.11283,-103.04352,34.07938), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04384,34.30262,-103.04385,34.31275), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04385,34.31275,-103.04384,34.30262), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04395,34.37956,-103.04385,34.31275), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04618,34.52239,-109.04614,34.57929), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04614,34.57929,-103.04307,34.61978), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04307,34.61978,-109.04614,34.57929), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04277,34.74736,-103.04307,34.61978), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04274,34.9541,-109.04585,34.95972), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04585,34.95972,-103.04274,34.9541), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04271,35.14474,-109.04635,35.17468), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04635,35.17468,-103.04262,35.18316), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04262,35.18316,-109.04635,35.17468), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0468,35.36361,-103.04262,35.18316), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.0463,35.61425,-103.04155,35.62249), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04155,35.62249,-109.0463,35.61425), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04136,35.73927,-103.04155,35.62249), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04602,35.8798,-109.04587,36.00234), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04587,36.00234,-103.04082,36.05523), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04082,36.05523,-109.04587,36.00234), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04573,36.11703,-103.04082,36.05523), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.00243,36.5004,-103.04192,36.50044), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.04192,36.50044,-103.00243,36.5004), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.00219,36.60272,-103.00252,36.67519), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.00252,36.67519,-103.00219,36.60272), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04543,36.87459,-103.00196,36.90957), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.00196,36.90957,-109.04543,36.87459), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.8698,36.99243,-104.73203,36.99345), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.73203,36.99345,-104.33883,36.99354), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.33883,36.99354,-104.73203,36.99345), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.47624,36.99377,-104.33883,36.99354), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.34314,36.99423,-106.47624,36.99377), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.00663,36.99539,-105.99747,36.99542), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.99747,36.99542,-105.1208,36.99543), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.1208,36.99543,-105.99747,36.99542), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.15504,36.99547,-105.1208,36.99543), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.22061,36.99556,-105.2513,36.99561), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.2513,36.99561,-105.22061,36.99556), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.71847,36.99585,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.71647,36.99585,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.41931,36.99586,-105.71847,36.99585), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-105.53392,36.99588,-105.41931,36.99586), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-104.00785,36.99598,-105.53392,36.99588), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.73325,36.99802,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-109.04522,36.99908,-108.62031,36.99929), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.62031,36.99929,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.37926,36.99956,-108.62031,36.99929), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.0861,36.99986,-108.00062,37), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-108.00062,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-107.42092,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-107.48174,37,-107.42091,37.00001), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-107.42091,37.00001,-108.00062,37), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-103.0022,37.0001,-106.87729,37.00014), mapfile, tile_dir, 0, 11, "new mexico-nm")
	render_tiles((-106.87729,37.00014,-103.0022,37.0001), mapfile, tile_dir, 0, 11, "new mexico-nm")