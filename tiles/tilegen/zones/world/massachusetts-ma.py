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
    # Region: Massachusetts
    # Region Name: MA

	render_tiles((-70.01523,41.23796,-70.09697,41.24085), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.09697,41.24085,-70.01523,41.23796), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.21148,41.24877,-70.09697,41.24085), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.96018,41.26455,-70.21148,41.24877), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.27553,41.31046,-70.19371,41.31379), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.27553,41.31046,-70.19371,41.31379), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.19371,41.31379,-70.27553,41.31046), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.07913,41.3195,-70.19371,41.31379), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.98487,41.35882,-70.04905,41.3917), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.04905,41.3917,-69.98487,41.35882), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.82128,41.25101,-70.76869,41.3037), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.76869,41.3037,-70.8334,41.31678), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.8334,41.31678,-70.76869,41.3037), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.69364,41.34283,-70.57745,41.34916), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.57745,41.34916,-70.8338,41.35339), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.8338,41.35339,-70.44826,41.35365), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.8338,41.35339,-70.44826,41.35365), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.44826,41.35365,-70.8338,41.35339), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.7578,41.3657,-70.44826,41.35365), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.46383,41.41915,-70.4962,41.42491), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.4962,41.42491,-70.46383,41.41915), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.68688,41.44133,-70.55328,41.45296), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.55328,41.45296,-70.68688,41.44133), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.60356,41.48238,-70.55328,41.45296), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.94843,41.40919,-70.85753,41.42577), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.85753,41.42577,-70.94843,41.40919), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.79027,41.44634,-70.93499,41.4547), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.93499,41.4547,-70.79027,41.44634), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.73431,41.48634,-71.12057,41.49745), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.12057,41.49745,-70.80686,41.49758), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.80686,41.49758,-71.12057,41.49745), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.03551,41.49905,-70.80686,41.49758), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.02032,41.50216,-71.03551,41.49905), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.08566,41.50929,-70.98171,41.51007), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.98171,41.51007,-71.08566,41.50929), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.66906,41.51293,-70.98171,41.51007), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.6541,41.51903,-70.66906,41.51293), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.94179,41.54012,-70.72609,41.54324), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.72609,41.54324,-70.01123,41.54393), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.01123,41.54393,-70.72609,41.54324), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.99542,41.54638,-70.55969,41.54833), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.55969,41.54833,-69.99542,41.54638), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.96498,41.55111,-70.55969,41.54833), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.47626,41.5585,-70.6982,41.559), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.6982,41.559,-70.47626,41.5585), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.91017,41.57707,-70.90909,41.57727), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.90909,41.57727,-70.91017,41.57707), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.82191,41.58284,-70.8574,41.58655), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.8574,41.58655,-70.85312,41.58732), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.85312,41.58732,-70.8574,41.58655), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.44529,41.59182,-70.85312,41.58732), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.80396,41.60152,-70.69539,41.60255), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.69539,41.60255,-71.13749,41.60256), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.13749,41.60256,-70.69539,41.60255), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.40058,41.60638,-71.13749,41.60256), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.71598,41.61401,-70.26969,41.61778), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.26969,41.61778,-70.01196,41.6198), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.01196,41.6198,-70.26969,41.61778), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.93113,41.62266,-70.01196,41.6198), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.32159,41.63051,-69.93113,41.62266), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.76177,41.63952,-70.76546,41.64158), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.76546,41.64158,-70.76177,41.63952), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.15862,41.65044,-70.76546,41.64158), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.13289,41.6601,-70.05552,41.66484), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.05552,41.66484,-71.13289,41.6601), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.00701,41.67158,-71.19564,41.67509), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.19564,41.67509,-70.00701,41.67158), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.20133,41.68177,-71.19564,41.67509), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.2086,41.69031,-69.92826,41.6917), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.92826,41.6917,-69.92828,41.69202), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.92828,41.69202,-69.92826,41.6917), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.27229,41.72135,-70.32382,41.73606), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.32382,41.73606,-70.21607,41.74298), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.21607,41.74298,-70.32382,41.73606), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.26139,41.7523,-70.44172,41.7529), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.44172,41.7529,-71.26139,41.7523), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.12198,41.75884,-70.44172,41.7529), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.49405,41.77388,-71.31728,41.7772), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.31728,41.7772,-70.49405,41.77388), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.3294,41.7826,-70.02473,41.78736), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.02473,41.78736,-71.3294,41.7826), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.00384,41.80852,-69.93595,41.80942), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.93595,41.80942,-70.00384,41.80852), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.53641,41.81163,-69.93595,41.80942), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.54103,41.81575,-70.53641,41.81163), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.3396,41.832,-70.54103,41.81575), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.00611,41.8524,-70.52557,41.85873), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.52557,41.85873,-70.00611,41.8524), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.06901,41.88492,-71.3817,41.8932), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.3817,41.8932,-71.3393,41.8934), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.3393,41.8934,-71.3817,41.8932), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.54639,41.91675,-69.97478,41.92511), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.97478,41.92511,-70.54639,41.91675), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.07401,41.93865,-70.60817,41.9407), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.60817,41.9407,-70.07401,41.93865), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.98023,41.94599,-70.58357,41.95001), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.58357,41.95001,-71.38146,41.95214), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.38146,41.95214,-70.58357,41.95001), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.07657,41.95794,-70.66248,41.96059), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.66248,41.96059,-70.07657,41.95794), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.38143,41.985,-72.81008,41.99832), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.81008,41.99832,-69.99414,41.99926), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-69.99414,41.99926,-72.81008,41.99832), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.77476,42.00213,-72.76674,42.003), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.76674,42.003,-72.77476,42.00213), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.6788,42.00551,-72.76674,42.003), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.79924,42.00807,-70.6788,42.00551), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.08378,42.01204,-71.6062,42.01312), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.6062,42.01312,-71.5911,42.01351), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.5911,42.01351,-71.6062,42.01312), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.55944,42.01434,-71.5911,42.01351), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.49822,42.01587,-71.45808,42.01688), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.45808,42.01688,-71.49822,42.01587), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.3814,42.0188,-70.19083,42.02003), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.19083,42.02003,-71.3814,42.0188), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.80065,42.02357,-71.88513,42.02507), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.88513,42.02507,-71.80065,42.02357), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.15076,42.02657,-71.98733,42.02688), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.98733,42.02688,-70.15076,42.02657), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.10216,42.02863,-72.13573,42.02914), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.13573,42.02914,-72.10216,42.02863), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.19884,42.0301,-72.60793,42.0308), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.60793,42.0308,-72.19884,42.0301), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.31715,42.03191,-72.39748,42.03282), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.39748,42.03282,-72.31715,42.03191), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.50918,42.03408,-72.52813,42.0343), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.52813,42.0343,-72.50918,42.03408), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.7355,42.0364,-72.84714,42.03689), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.84714,42.03689,-72.7355,42.0364), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.99955,42.03865,-73.00874,42.0389), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.00874,42.0389,-72.99955,42.03865), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.05341,42.04012,-73.00874,42.0389), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.12725,42.04212,-73.05341,42.04012), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.23106,42.04495,-70.64434,42.0459), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.64434,42.0459,-73.23106,42.04495), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.48731,42.04964,-70.64434,42.0459), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.48967,42.05378,-73.48731,42.04964), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.24539,42.06373,-70.04938,42.06469), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.04938,42.06469,-70.24539,42.06373), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.63848,42.08158,-70.18931,42.08234), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.18931,42.08234,-70.63848,42.08158), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.50814,42.08626,-70.18931,42.08234), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.50814,42.08626,-70.18931,42.08234), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.13894,42.09291,-73.50814,42.08626), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.67924,42.12635,-70.68532,42.13303), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.68532,42.13303,-70.67924,42.12635), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.73056,42.21094,-70.78157,42.24864), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.78157,42.24864,-70.78872,42.25392), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.78872,42.25392,-70.78157,42.24864), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.83585,42.26476,-70.96735,42.26817), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.96735,42.26817,-70.85109,42.26827), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.85109,42.26827,-70.96735,42.26817), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.9485,42.28235,-70.85109,42.26827), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.88124,42.30066,-70.91749,42.30569), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.91749,42.30569,-70.88124,42.30066), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.99306,42.31289,-70.91749,42.30569), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.99784,42.32121,-70.99306,42.31289), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.98309,42.34347,-73.41065,42.35174), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.41065,42.35174,-70.97589,42.35434), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.97589,42.35434,-70.9749,42.35584), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.9749,42.35584,-70.97589,42.35434), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.98255,42.42024,-70.98299,42.424), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.98299,42.424,-70.95521,42.42547), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.95521,42.42547,-73.38356,42.42551), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.38356,42.42551,-70.95521,42.42547), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.91319,42.4277,-73.38356,42.42551), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.90599,42.43916,-70.89923,42.44992), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.89923,42.44992,-70.90599,42.43916), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.88649,42.4702,-70.89923,42.44992), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.83599,42.4905,-73.35253,42.51), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.35253,42.51,-70.83599,42.4905), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.84849,42.5502,-70.80409,42.5616), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.80409,42.5616,-70.84849,42.5502), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.69857,42.57739,-70.65473,42.58223), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.65473,42.58223,-70.69857,42.57739), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.307,42.63265,-70.59401,42.63503), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.59401,42.63503,-73.307,42.63265), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.68159,42.66234,-70.72982,42.6696), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.72982,42.6696,-70.68159,42.66234), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.60251,42.6777,-70.72982,42.6696), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.6451,42.68942,-71.29421,42.69699), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.29421,42.69699,-71.35187,42.69815), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.35187,42.69815,-71.29421,42.69699), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.63621,42.70489,-71.74582,42.70729), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.74582,42.70729,-71.80542,42.70892), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.80542,42.70892,-71.74582,42.70729), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.77227,42.71106,-71.89871,42.71147), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.89871,42.71147,-70.77227,42.71106), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.92882,42.71229,-71.89871,42.71147), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.08138,42.71646,-72.12453,42.71764), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.12453,42.71764,-72.08138,42.71646), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.20369,42.71982,-72.12453,42.71764), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.28297,42.72201,-72.20369,42.71982), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.41201,42.72557,-72.45126,42.72665), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.45126,42.72665,-72.45852,42.72685), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.45852,42.72685,-72.45126,42.72665), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.51675,42.72847,-72.45852,42.72685), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.25561,42.73639,-71.25511,42.7364), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.25511,42.7364,-71.25561,42.73639), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.24541,42.73655,-72.80911,42.73658), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.80911,42.73658,-71.24541,42.73655), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.1818,42.73759,-72.86429,42.73771), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.86429,42.73771,-71.1818,42.73759), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-72.93026,42.73907,-72.86429,42.73771), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.01865,42.74088,-73.02291,42.74097), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.02291,42.74097,-73.01865,42.74088), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.14249,42.74343,-73.02291,42.74097), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-73.26496,42.74594,-73.14249,42.74343), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.80522,42.7818,-71.1861,42.79069), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.1861,42.79069,-70.80522,42.7818), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.0642,42.80629,-71.11674,42.81194), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.11674,42.81194,-71.1497,42.81549), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.1497,42.81549,-71.11674,42.81194), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.04871,42.83108,-71.1497,42.81549), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-71.0312,42.85909,-70.9665,42.86899), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.9665,42.86899,-70.86475,42.87026), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.86475,42.87026,-70.9665,42.86899), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.8173,42.87229,-70.86475,42.87026), mapfile, tile_dir, 0, 11, "massachusetts-ma")
	render_tiles((-70.9308,42.88459,-70.8173,42.87229), mapfile, tile_dir, 0, 11, "massachusetts-ma")