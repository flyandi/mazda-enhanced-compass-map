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
    # Region: Minnesota
    # Region Name: MN

	render_tiles((-93.57673,43.49952,-93.49735,43.49953), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.49735,43.49953,-93.57673,43.49952), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.64853,43.49954,-92.87028,43.49955), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.87028,43.49955,-93.64853,43.49954), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.02435,43.49956,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.04919,43.49956,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.22886,43.49957,-93.02435,43.49956), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.97076,43.49961,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.97076,43.49961,-93.22886,43.49957), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.83442,43.49997,-95.86095,43.49999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.86095,43.49999,-95.83442,43.49997), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.24797,43.50018,-96.05316,43.50019), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.05316,43.50019,-94.24797,43.50018), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.4868,43.50025,-92.55316,43.5003), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.55316,43.5003,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.55313,43.5003,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.45443,43.50032,-92.55316,43.5003), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.19848,43.50034,-95.45443,43.50032), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45326,43.50039,-92.44895,43.50041), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.44895,43.50041,-96.45326,43.50039), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.3906,43.50047,-95.38779,43.50048), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.38779,43.50048,-94.3906,43.50047), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.44285,43.50048,-94.3906,43.50047), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.21771,43.50055,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.85456,43.50055,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.87424,43.50056,-91.21771,43.50055), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.91461,43.5006,-94.87424,43.50056), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.82485,43.50068,-91.49104,43.50069), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.49104,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.73022,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.61084,43.50069,-91.82485,43.50068), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.0798,43.5007,-92.17886,43.50071), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.17886,43.50071,-92.0798,43.5007), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.21494,43.50089,-92.17886,43.50071), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45332,43.5523,-91.23281,43.56484), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.23281,43.56484,-96.45332,43.5523), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.25293,43.60036,-91.23281,43.56484), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.27325,43.66662,-91.257,43.72566), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.257,43.72566,-91.24396,43.77305), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.24396,43.77305,-91.257,43.72566), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.28766,43.84707,-96.45291,43.84951), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45291,43.84951,-91.28766,43.84707), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45291,43.84951,-91.28766,43.84707), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.291,43.85273,-96.45291,43.84951), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.35743,43.91723,-91.291,43.85273), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.42357,43.9843,-91.44054,44.0015), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.44054,44.0015,-91.42357,43.9843), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.55922,44.02421,-91.57328,44.0269), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.57328,44.0269,-91.55922,44.02421), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.64787,44.06411,-91.57328,44.0269), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.7191,44.12885,-91.8173,44.16424), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.8173,44.16424,-96.45244,44.19678), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45244,44.19678,-96.45244,44.1968), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45244,44.1968,-96.45244,44.19678), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.8545,44.19723,-96.45244,44.1968), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.8927,44.23111,-91.8545,44.19723), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.91619,44.31809,-96.45221,44.36015), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45221,44.36015,-91.9636,44.36211), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.9636,44.36211,-96.45221,44.36015), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.08453,44.40461,-92.11109,44.41395), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.11109,44.41395,-92.08453,44.40461), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.23247,44.44543,-92.24536,44.45425), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.24536,44.45425,-92.23247,44.44543), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29101,44.48546,-92.24536,44.45425), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.31407,44.53801,-92.31693,44.53928), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.31693,44.53928,-92.31407,44.53801), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45329,44.54364,-92.31693,44.53928), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.39928,44.55829,-92.36152,44.55894), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.36152,44.55894,-92.39928,44.55829), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.54928,44.5777,-92.36152,44.55894), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.61803,44.61287,-96.45381,44.63134), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45381,44.63134,-92.61803,44.61287), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.69649,44.68944,-92.73162,44.71492), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.73162,44.71492,-92.69649,44.68944), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.79236,44.75898,-92.80529,44.76836), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.80529,44.76836,-92.79236,44.75898), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45483,44.80555,-92.80529,44.76836), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76857,44.85437,-92.76712,44.86152), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76712,44.86152,-92.76702,44.86198), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76702,44.86198,-92.76712,44.86152), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.7508,44.94157,-96.45584,44.97735), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45584,44.97735,-92.7508,44.94157), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.7619,45.02247,-92.80291,45.0654), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.80291,45.0654,-92.7619,45.02247), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.74051,45.1134,-92.74382,45.12365), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.74382,45.12365,-92.74051,45.1134), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76693,45.19511,-92.76609,45.21002), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76609,45.21002,-92.76693,45.19511), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45755,45.2689,-92.76187,45.28494), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.76187,45.28494,-92.74827,45.29606), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.74827,45.29606,-92.76187,45.28494), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.45778,45.30761,-92.74827,45.29606), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.47008,45.3268,-92.69897,45.33637), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.69897,45.33637,-96.47008,45.3268), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.48256,45.34627,-92.69897,45.33637), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.52179,45.37565,-96.56214,45.38609), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.56214,45.38609,-92.65849,45.39606), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.65849,45.39606,-96.56214,45.38609), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.61773,45.40809,-96.67545,45.41022), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.67545,45.41022,-96.61773,45.40809), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.71079,45.43693,-92.64677,45.43793), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.64677,45.43793,-96.71079,45.43693), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.68679,45.47227,-96.74251,45.47872), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.74251,45.47872,-92.68679,45.47227), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.72802,45.52565,-96.78104,45.53597), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.78104,45.53597,-92.72802,45.52565), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.75691,45.5575,-92.8015,45.56285), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.8015,45.56285,-92.75691,45.5575), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.88114,45.57341,-92.8015,45.56285), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.83542,45.58613,-96.84396,45.594), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.84396,45.594,-96.83542,45.58613), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.85162,45.61941,-92.88793,45.63901), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.88793,45.63901,-92.8867,45.64415), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.8867,45.64415,-92.88793,45.63901), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.82616,45.65416,-92.8867,45.64415), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.74509,45.70158,-92.86969,45.71514), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.86969,45.71514,-96.74509,45.70158), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.84074,45.7294,-96.67267,45.73234), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.67267,45.73234,-92.84074,45.7294), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.82601,45.73665,-96.67267,45.73234), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.63051,45.78116,-92.7765,45.79001), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.7765,45.79001,-96.63051,45.78116), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.58709,45.81645,-92.75946,45.83534), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.75946,45.83534,-96.58709,45.81645), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.57187,45.87185,-92.72113,45.88381), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.72113,45.88381,-96.57187,45.87185), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.65613,45.92444,-96.56367,45.93525), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.56367,45.93525,-92.65613,45.92444), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.58057,45.94625,-96.56367,45.93525), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.54568,45.97012,-92.47276,45.97295), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.47276,45.97295,-92.54568,45.97012), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.44963,46.00225,-92.35176,46.01569), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.35176,46.01569,-96.57426,46.01655), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.57426,46.01655,-92.35176,46.01569), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.39268,46.01954,-96.5727,46.02189), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.5727,46.02189,-92.39268,46.01954), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.33824,46.05215,-92.29403,46.07438), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29403,46.07438,-96.55451,46.08398), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.55451,46.08398,-92.29403,46.07438), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29383,46.15732,-96.59567,46.21985), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.59567,46.21985,-92.29362,46.24404), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29362,46.24404,-96.59567,46.21985), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.60104,46.31955,-96.6473,46.3585), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.6473,46.3585,-96.60104,46.31955), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29276,46.41722,-96.7091,46.43529), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.7091,46.43529,-92.29276,46.41722), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29237,46.49559,-96.7091,46.43529), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.74444,46.56596,-96.78579,46.62959), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.78579,46.62959,-96.78979,46.63575), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.78979,46.63575,-96.79052,46.63688), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.79052,46.63688,-96.78979,46.63575), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29219,46.66324,-92.20549,46.66474), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.20549,46.66474,-92.29219,46.66324), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.78685,46.69281,-92.18309,46.69524), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.18309,46.69524,-96.78685,46.69281), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.01529,46.70647,-92.05082,46.71052), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.05082,46.71052,-92.01529,46.70647), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.14334,46.7316,-92.10026,46.73445), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.10026,46.73445,-92.14334,46.7316), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.7888,46.77758,-92.06209,46.80404), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.06209,46.80404,-96.7888,46.77758), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.01341,46.83373,-92.06209,46.80404), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.90648,46.89124,-96.76397,46.91251), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.76397,46.91251,-91.80685,46.93373), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.80685,46.93373,-91.794,46.94278), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.794,46.94278,-91.80685,46.93373), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.7371,46.98285,-96.8335,47.01011), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.8335,47.01011,-91.64456,47.02649), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.64456,47.02649,-96.8335,47.01011), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.81908,47.08115,-91.57382,47.08992), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.57382,47.08992,-96.81908,47.08115), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.45697,47.13916,-96.82657,47.15054), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.82657,47.15054,-91.45697,47.13916), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.38702,47.18729,-96.82657,47.15054), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.83601,47.23798,-96.84022,47.27698), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.84022,47.27698,-91.26251,47.27929), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.26251,47.27929,-96.84022,47.27698), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.14696,47.38146,-96.85748,47.44046), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.85748,47.44046,-91.02312,47.46496), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.02312,47.46496,-96.85748,47.44046), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.02312,47.46496,-96.85748,47.44046), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.85596,47.49917,-91.02312,47.46496), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.86827,47.5569,-96.85407,47.57201), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.85407,47.57201,-90.86827,47.5569), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.73593,47.62434,-96.88238,47.64903), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.88238,47.64903,-90.64784,47.65618), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.64784,47.65618,-96.88238,47.64903), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.89349,47.67213,-90.64784,47.65618), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.53711,47.70306,-96.89349,47.67213), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.42139,47.73515,-96.92851,47.74488), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.92851,47.74488,-90.32345,47.75377), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.32345,47.75377,-96.92851,47.74488), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.18764,47.77813,-90.32345,47.75377), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.07203,47.81111,-89.9743,47.83051), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.9743,47.83051,-96.99636,47.8444), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.99636,47.8444,-89.9743,47.83051), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.92365,47.86206,-96.99636,47.8444), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.79354,47.89136,-89.73754,47.91818), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.73754,47.91818,-97.03735,47.93328), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.03735,47.93328,-89.73754,47.91818), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.66062,47.95122,-97.03735,47.93328), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.55502,47.97485,-89.86815,47.9899), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.86815,47.9899,-89.55502,47.97485), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.62509,48.01152,-89.48923,48.01453), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.48923,48.01453,-89.62509,48.01152), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.97343,48.02035,-89.74931,48.02333), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-89.74931,48.02333,-97.06899,48.02627), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.06899,48.02627,-89.74931,48.02333), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.54251,48.05327,-91.4655,48.06677), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.4655,48.06677,-91.33658,48.06963), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.33658,48.06963,-91.4655,48.06677), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.25011,48.08409,-90.02963,48.08759), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.02963,48.08759,-91.25011,48.08409), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.10562,48.09136,-90.02963,48.08759), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.7037,48.09601,-97.10562,48.09136), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.31723,48.10379,-90.47102,48.10608), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.47102,48.10608,-91.55927,48.10827), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.55927,48.10827,-90.47102,48.10608), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.13619,48.11214,-91.55927,48.10827), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.69237,48.11933,-90.77596,48.12223), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.77596,48.12223,-90.56611,48.12262), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.56611,48.12262,-90.77596,48.12223), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.15611,48.14048,-90.56611,48.12262), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14675,48.16856,-97.14584,48.17322), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14584,48.17322,-90.80421,48.17783), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.80421,48.17783,-97.14584,48.17322), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.03555,48.18946,-91.03254,48.19058), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.03254,48.19058,-91.03555,48.18946), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14191,48.19363,-91.03254,48.19058), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.71493,48.19913,-91.78118,48.20043), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.78118,48.20043,-91.71493,48.19913), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.79728,48.20578,-91.78118,48.20043), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.36917,48.22027,-91.79728,48.20578), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.90683,48.23734,-91.89347,48.2377), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.89347,48.2377,-90.90683,48.23734), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.31467,48.24053,-91.89347,48.2377), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-90.84362,48.24358,-92.31467,48.24053), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-91.98077,48.2478,-90.84362,48.24358), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.12953,48.25782,-91.98077,48.2478), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29567,48.27812,-92.41629,48.29546), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.41629,48.29546,-92.29567,48.27812), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.00013,48.32136,-92.29541,48.32396), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.29541,48.32396,-92.00013,48.32136), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.1379,48.34459,-92.46995,48.35184), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.46995,48.35184,-92.26228,48.35493), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.26228,48.35493,-92.46995,48.35184), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.05523,48.35921,-92.16216,48.36328), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.16216,48.36328,-92.05523,48.35921), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.45633,48.4142,-97.13917,48.43053), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.13917,48.43053,-92.57564,48.44083), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.57564,48.44083,-92.51491,48.44831), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.51491,48.44831,-92.57564,48.44083), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.6571,48.46692,-92.51491,48.44831), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.63112,48.50825,-93.67457,48.5163), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.67457,48.5163,-92.63112,48.50825), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.81518,48.52651,-93.56206,48.5289), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.56206,48.5289,-93.81518,48.52651), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14912,48.53231,-93.56206,48.5289), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.72805,48.53929,-97.1481,48.54074), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.1481,48.54074,-92.72805,48.53929), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.63493,48.54287,-97.14772,48.54389), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14772,48.54389,-92.63493,48.54287), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.4675,48.54566,-97.14772,48.54389), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.80527,48.5703,-97.14292,48.58373), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.14292,48.58373,-93.46431,48.59179), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.46431,48.59179,-92.89469,48.59492), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.89469,48.59492,-93.46431,48.59179), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.37116,48.60509,-92.89469,48.59492), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.14242,48.62492,-93.34753,48.62662), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.34753,48.62662,-93.08845,48.62681), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.08845,48.62681,-93.34753,48.62662), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.84401,48.6294,-93.927,48.63122), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.927,48.63122,-92.95488,48.63149), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-92.95488,48.63149,-93.927,48.63122), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-93.2074,48.64247,-94.09124,48.64367), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.09124,48.64367,-93.2074,48.64247), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.25019,48.65632,-97.10001,48.66793), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.10001,48.66793,-94.25019,48.65632), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.25117,48.68351,-94.4466,48.6929), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.4466,48.6929,-94.4302,48.69831), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.4302,48.69831,-94.50886,48.70036), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.50886,48.70036,-94.4302,48.69831), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.2818,48.70526,-94.50886,48.70036), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.38885,48.71195,-97.12125,48.71359), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.12125,48.71359,-94.38885,48.71195), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.61901,48.73737,-97.12125,48.71359), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.15259,48.7726,-94.69431,48.78935), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.69431,48.78935,-97.15259,48.7726), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.68568,48.84012,-97.18736,48.8676), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.18736,48.8676,-94.68307,48.88393), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.68307,48.88393,-97.18736,48.8676), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.22785,48.94586,-95.34096,48.99874), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.34096,48.99874,-95.31989,48.99876), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.31989,48.99876,-95.34096,48.99874), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.15371,48.9989,-95.31989,48.99876), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.97539,48.99998,-94.75022,48.99999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.93096,48.99998,-94.75022,48.99999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-96.40541,48.99998,-94.75022,48.99999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.75022,48.99999,-95.97539,48.99998), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.71893,48.99999,-95.97539,48.99998), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.22904,49.00069,-94.75022,48.99999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-97.22904,49.00069,-94.75022,48.99999), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.75022,49.09976,-94.77423,49.12499), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.77423,49.12499,-94.75022,49.09976), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.15331,49.18488,-94.79724,49.21428), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.79724,49.21428,-95.15331,49.18488), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.15333,49.30929,-94.81622,49.32099), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.81622,49.32099,-95.15333,49.30929), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.87845,49.33319,-94.81622,49.32099), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.0584,49.35317,-94.95211,49.36868), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.95211,49.36868,-94.98891,49.3689), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-94.98891,49.3689,-94.95211,49.36868), mapfile, tile_dir, 0, 11, "minnesota-mn")
	render_tiles((-95.15331,49.38436,-94.98891,49.3689), mapfile, tile_dir, 0, 11, "minnesota-mn")