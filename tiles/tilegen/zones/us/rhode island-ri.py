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
    # Region: Rhode Island
    # Region Name: RI

	render_tiles((-71.5937,41.14634,-71.51921,41.14962), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.51921,41.14962,-71.5937,41.14634), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.63147,41.16668,-71.53408,41.18186), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.63147,41.16668,-71.53408,41.18186), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.53408,41.18186,-71.63147,41.16668), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.59334,41.23743,-71.54541,41.24273), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.54541,41.24273,-71.59334,41.23743), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.86277,41.30979,-71.86051,41.32025), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.86051,41.32025,-71.78596,41.32574), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.86051,41.32025,-71.78596,41.32574), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78596,41.32574,-71.86051,41.32025), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.7293,41.33328,-71.70163,41.33697), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.70163,41.33697,-71.7293,41.33328), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.83595,41.35394,-71.62451,41.36087), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.62451,41.36087,-71.83595,41.35394), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.4833,41.37172,-71.55538,41.37332), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.55538,41.37332,-71.4833,41.37172), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.45537,41.40796,-71.83965,41.41212), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.83965,41.41212,-71.45537,41.40796), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.79768,41.41671,-71.83965,41.41212), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3511,41.4508,-71.31269,41.4514), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.31269,41.4514,-71.3511,41.4508), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.42865,41.45416,-71.41721,41.45603), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.41721,41.45603,-71.42865,41.45416), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.19302,41.45793,-71.41721,41.45603), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.38928,41.46061,-71.19302,41.45793), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.21267,41.4666,-71.38928,41.46061), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.2398,41.47857,-71.24599,41.4813), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.24599,41.4813,-71.2398,41.47857), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.14022,41.48586,-71.28564,41.48781), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.28564,41.48781,-71.14022,41.48586), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.12057,41.49745,-71.28564,41.48781), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.79172,41.54577,-71.12057,41.49745), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78936,41.59685,-71.78936,41.59691), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78936,41.59691,-71.78936,41.59685), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.13749,41.60256,-71.78936,41.59691), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78946,41.64002,-71.13289,41.6601), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.13289,41.6601,-71.19564,41.67509), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.19564,41.67509,-71.20133,41.68177), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.20133,41.68177,-71.19564,41.67509), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.2086,41.69031,-71.20133,41.68177), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78968,41.72457,-71.78968,41.72473), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.78968,41.72473,-71.78968,41.72457), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.26139,41.7523,-71.31728,41.7772), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.31728,41.7772,-71.3294,41.7826), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3294,41.7826,-71.31728,41.7772), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.79277,41.807,-71.3294,41.7826), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3396,41.832,-71.79277,41.807), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3817,41.8932,-71.3393,41.8934), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3393,41.8934,-71.3817,41.8932), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.38146,41.95214,-71.38143,41.985), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.38143,41.985,-71.79924,42.00807), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.79924,42.00807,-71.6062,42.01312), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.6062,42.01312,-71.5911,42.01351), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.5911,42.01351,-71.6062,42.01312), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.55944,42.01434,-71.5911,42.01351), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.49822,42.01587,-71.45808,42.01688), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.45808,42.01688,-71.49822,42.01587), mapfile, tile_dir, 0, 11, "rhode island-ri")
	render_tiles((-71.3814,42.0188,-71.45808,42.01688), mapfile, tile_dir, 0, 11, "rhode island-ri")