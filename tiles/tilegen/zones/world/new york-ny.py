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
    # Region: New York
    # Region Name: NY

	render_tiles((-72.03475,41.23482,-71.91728,41.25133), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.91728,41.25133,-72.03475,41.23482), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.01893,41.27411,-71.9268,41.29012), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.01893,41.27411,-71.9268,41.29012), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.9268,41.29012,-72.01893,41.27411), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.26061,40.50244,-74.19992,40.51173), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.19992,40.51173,-74.26061,40.50244), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.94059,40.5429,-74.24921,40.54506), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.24921,40.54506,-73.94059,40.5429), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.11259,40.5476,-74.24921,40.54506), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.21684,40.55862,-73.97379,40.56085), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.97379,40.56085,-74.21684,40.55862), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.99135,40.57035,-73.82549,40.57615), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.82549,40.57615,-73.99135,40.57035), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.6409,40.58282,-74.03656,40.58899), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.03656,40.58899,-73.75062,40.58932), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.75062,40.58932,-74.03797,40.58957), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.03797,40.58957,-73.75062,40.58932), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.77493,40.59076,-74.03797,40.58957), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.20369,40.59269,-73.50733,40.59341), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.50733,40.59341,-74.20369,40.59269), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.05732,40.59755,-73.48487,40.59875), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.48487,40.59875,-74.05732,40.59755), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.42398,40.61324,-73.3064,40.62076), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.3064,40.62076,-73.42398,40.61324), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.35147,40.6305,-73.20844,40.63088), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.20844,40.63088,-74.20225,40.6309), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.20225,40.6309,-73.20844,40.63088), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.20012,40.63187,-74.20225,40.6309), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.17061,40.64529,-74.16015,40.64608), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.16015,40.64608,-73.14608,40.64641), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.14608,40.64641,-74.16015,40.64608), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.08681,40.6516,-73.14608,40.64641), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.07094,40.66721,-74.06772,40.67038), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.06772,40.67038,-74.07094,40.66721), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.01255,40.67965,-74.06772,40.67038), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.04731,40.69047,-74.04697,40.69115), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.04697,40.69115,-74.04731,40.69047), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.04116,40.70261,-72.92321,40.71328), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.92321,40.71328,-74.03093,40.72279), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.03093,40.72279,-72.92321,40.71328), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.02546,40.73356,-74.02349,40.73745), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.02349,40.73745,-74.02546,40.73356), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.01378,40.7566,-72.75718,40.76437), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.75718,40.76437,-74.01378,40.7566), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.98472,40.79737,-73.97121,40.81632), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.97121,40.81632,-73.96808,40.8207), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.96808,40.8207,-73.96583,40.82475), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.96583,40.82475,-73.96808,40.8207), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.94748,40.85777,-72.39585,40.86666), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.39585,40.86666,-73.71367,40.8701), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.71367,40.8701,-72.39585,40.86666), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.93808,40.8747,-73.7412,40.87585), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.7412,40.87585,-73.93808,40.8747), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.65437,40.8782,-73.7412,40.87585), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.76628,40.8811,-73.93489,40.88265), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.93489,40.88265,-73.76628,40.8811), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.61757,40.8979,-72.29873,40.90315), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.29873,40.90315,-73.22929,40.90512), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.22929,40.90512,-73.23583,40.90669), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.23583,40.90669,-73.22929,40.90512), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.75678,40.9126,-73.92202,40.91474), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.92202,40.91474,-73.75678,40.9126), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.49994,40.91817,-73.92047,40.91861), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.92047,40.91861,-73.49994,40.91817), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.49735,40.92318,-73.92047,40.91861), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.14899,40.9289,-73.33136,40.9296), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.33136,40.9296,-73.14899,40.9289), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.43666,40.9349,-73.69797,40.9396), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.69797,40.9396,-73.43666,40.9349), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.48537,40.9464,-73.90728,40.9515), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.90728,40.9515,-73.39286,40.9553), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.39286,40.9553,-73.14467,40.95584), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.14467,40.95584,-73.39286,40.9553), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.09737,40.95888,-73.14467,40.95584), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.04045,40.9645,-72.85983,40.96609), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.85983,40.96609,-73.04045,40.9645), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.11037,40.97194,-72.85983,40.96609), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.70807,40.97785,-73.11037,40.97194), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.65734,40.98517,-73.65737,40.98552), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.65737,40.98552,-73.65734,40.98517), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.89398,40.9972,-72.58533,40.99759), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.89398,40.9972,-72.58533,40.99759), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.58533,40.99759,-73.89398,40.9972), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.65936,41.00403,-71.93698,41.00614), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.93698,41.00614,-73.65936,41.00403), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.65953,41.01786,-72.05193,41.02051), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.05193,41.02051,-73.65953,41.01786), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.21748,41.04061,-72.50431,41.04333), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.50431,41.04333,-72.21748,41.04061), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.1629,41.05319,-72.09571,41.05402), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.09571,41.05402,-72.1629,41.05319), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.04105,41.05909,-72.09571,41.05402), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.28309,41.06787,-71.85621,41.0706), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.85621,41.0706,-71.9596,41.07124), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.9596,41.07124,-71.85621,41.0706), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-71.91939,41.08052,-72.44524,41.08612), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.44524,41.08612,-71.91939,41.08052), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.72778,41.1007,-72.08421,41.10152), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.08421,41.10152,-73.72778,41.1007), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.38981,41.1083,-72.2547,41.11085), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.2547,41.11085,-72.38981,41.1083), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.1267,41.11514,-73.69594,41.11526), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.69594,41.11526,-72.1267,41.11514), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.21341,41.13376,-72.35412,41.13995), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.35412,41.13995,-74.23447,41.14288), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.23447,41.14288,-72.35412,41.13995), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.29111,41.15587,-74.23447,41.14288), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.30199,41.17259,-72.18203,41.17835), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.18203,41.17835,-74.30199,41.17259), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-72.18916,41.19355,-74.36648,41.20394), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.36648,41.20394,-73.48271,41.21276), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.48271,41.21276,-74.36648,41.20394), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.45758,41.24823,-73.48271,41.21276), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.55096,41.29542,-74.45758,41.24823), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.69491,41.35742,-73.54415,41.36632), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.54415,41.36632,-73.54331,41.37511), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.54331,41.37511,-73.54318,41.3764), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.54318,41.3764,-73.54315,41.37677), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.54315,41.37677,-73.54318,41.3764), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.73489,41.42582,-74.75627,41.42763), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.75627,41.42763,-74.73489,41.42582), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.79955,41.43129,-74.75627,41.42763), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.53697,41.44109,-74.79955,41.43129), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.89036,41.45532,-73.53697,41.44109), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.98246,41.49647,-73.52968,41.52716), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.52968,41.52716,-74.98246,41.49647), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.04388,41.57509,-75.0462,41.60376), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.0462,41.60376,-75.04388,41.57509), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.52002,41.6412,-75.04928,41.64186), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.04928,41.64186,-73.52002,41.6412), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.51792,41.66672,-75.04928,41.64186), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.05343,41.75254,-75.07441,41.80219), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.07441,41.80219,-73.50501,41.82377), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.50501,41.82377,-75.11337,41.8407), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.11337,41.8407,-75.14666,41.85013), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.14666,41.85013,-75.11337,41.8407), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.1902,41.86245,-75.14666,41.85013), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.26301,41.88511,-75.1902,41.86245), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.29176,41.94709,-75.34113,41.99277), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.34113,41.99277,-75.35986,41.99369), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.35986,41.99369,-75.34113,41.99277), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.47247,41.99826,-77.83203,41.99852), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.83203,41.99852,-77.74993,41.99876), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.61084,41.99852,-77.74993,41.99876), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.74993,41.99876,-79.76131,41.99881), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.76131,41.99881,-75.87068,41.99883), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.87068,41.99883,-79.06126,41.99884), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.06126,41.99884,-75.87068,41.99883), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.10584,41.99886,-76.14552,41.99887), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.14552,41.99887,-76.10584,41.99886), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.46216,41.99893,-78.98307,41.99895), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.98307,41.99895,-76.46216,41.99893), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.2712,41.99897,-78.98307,41.99895), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.30813,41.99907,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.47303,41.99907,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.2066,41.99909,-78.91886,41.9991), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.91886,41.9991,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.61002,41.99915,-78.91886,41.9991), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.55311,41.9993,-75.48315,41.9994), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.48315,41.9994,-75.47714,41.99941), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.47714,41.99941,-75.48315,41.9994), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.03118,41.99942,-75.47714,41.99941), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.59665,41.99988,-76.55762,42.00015), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.55762,42.00015,-76.55812,42.00016), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.55812,42.00016,-76.55762,42.00015), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.92685,42.00072,-76.96573,42.00078), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.96573,42.00078,-76.92685,42.00072), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.00764,42.00085,-76.96573,42.00078), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.48731,42.04964,-73.48967,42.05378), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.48967,42.05378,-73.48731,42.04964), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.50814,42.08626,-73.48967,42.05378), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.76212,42.13125,-73.50814,42.08626), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.76195,42.26986,-79.62748,42.32469), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.76195,42.26986,-79.62748,42.32469), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.62748,42.32469,-73.41065,42.35174), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.41065,42.35174,-79.62748,42.32469), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.45353,42.41116,-73.38356,42.42551), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.38356,42.42551,-79.45353,42.41116), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.38194,42.46649,-73.38356,42.42551), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.35253,42.51,-79.28336,42.51123), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.28336,42.51123,-73.35253,42.51), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.19323,42.54588,-79.13857,42.56446), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.13857,42.56446,-79.13594,42.56918), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.13594,42.56918,-79.13857,42.56446), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.11136,42.61336,-73.307,42.63265), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.307,42.63265,-79.06376,42.64476), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.06376,42.64476,-73.307,42.63265), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.04886,42.68916,-78.97238,42.71599), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.97238,42.71599,-79.04886,42.68916), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.26496,42.74594,-78.90484,42.74612), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.90484,42.74612,-73.26496,42.74594), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.85136,42.79176,-73.29094,42.80192), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.29094,42.80192,-78.85688,42.80529), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.85688,42.80529,-73.29094,42.80192), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.86566,42.82676,-73.27867,42.83341), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.27867,42.83341,-78.86566,42.82676), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.88256,42.86726,-78.91246,42.88656), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.91246,42.88656,-78.88256,42.86726), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.90916,42.93326,-73.27383,42.94363), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.27383,42.94363,-78.92796,42.95292), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.92796,42.95292,-78.96176,42.95776), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.96176,42.95776,-78.92796,42.95292), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.01996,42.99476,-73.27001,43.03071), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.27001,43.03071,-73.26978,43.03592), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.26978,43.03592,-73.27001,43.03071), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.00545,43.05723,-79.01825,43.06602), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.01825,43.06602,-79.01958,43.0663), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.01958,43.0663,-79.01825,43.06602), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.07447,43.07786,-79.01958,43.0663), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.06021,43.1248,-79.05525,43.13381), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.05525,43.13381,-79.06021,43.1248), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.04457,43.15326,-79.05525,43.13381), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.05287,43.22205,-77.55102,43.23576), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.55102,43.23576,-79.05287,43.22205), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.50092,43.25036,-79.07047,43.26245), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-79.07047,43.26245,-76.95217,43.27069), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.95217,43.27069,-76.99969,43.27146), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.99969,43.27146,-76.95217,43.27069), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.37605,43.27403,-76.99969,43.27146), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.26418,43.27736,-77.34109,43.28066), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.34109,43.28066,-77.66036,43.283), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.66036,43.283,-77.34109,43.28066), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.13043,43.28564,-77.66036,43.283), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.84168,43.3054,-73.25536,43.31471), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.25536,43.31471,-78.83406,43.31756), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.83406,43.31756,-76.76903,43.31845), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.76903,43.31845,-78.83406,43.31756), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.722,43.33758,-77.76023,43.34116), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.76023,43.34116,-77.80826,43.34321), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.80826,43.34321,-77.81653,43.34356), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.81653,43.34356,-77.80826,43.34321), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.68486,43.35269,-77.81653,43.34356), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.25283,43.36349,-77.99484,43.36526), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.99484,43.36526,-77.99559,43.36531), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-77.99559,43.36531,-77.99484,43.36526), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.5474,43.36954,-78.46555,43.3709), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.46555,43.3709,-78.5474,43.36954), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.32937,43.37315,-78.46555,43.3709), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-78.1452,43.37551,-78.32937,43.37315), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.63077,43.41336,-76.61721,43.42018), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.61721,43.42018,-76.63077,43.41336), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.51588,43.47114,-76.3197,43.51228), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.3197,43.51228,-76.41758,43.52129), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.41758,43.52129,-76.37094,43.52563), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.37094,43.52563,-76.36885,43.52582), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.36885,43.52582,-76.37094,43.52563), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.23583,43.52926,-76.36885,43.52582), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.24204,43.53493,-76.23583,43.52926), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.39577,43.56809,-76.20347,43.57498), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.20347,43.57498,-73.39577,43.56809), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.29211,43.58451,-76.20347,43.57498), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.42498,43.59878,-73.29211,43.58451), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.3277,43.62591,-76.1966,43.64976), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.1966,43.64976,-73.41455,43.65821), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.41455,43.65821,-76.1966,43.64976), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.20148,43.68029,-73.39372,43.6992), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.39372,43.6992,-76.20148,43.68029), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.36111,43.75323,-76.21321,43.75351), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.21321,43.75351,-73.36111,43.75323), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.35071,43.77046,-76.21321,43.75351), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.22927,43.80414,-73.38253,43.80816), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.38253,43.80816,-76.22927,43.80414), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.3903,43.81737,-73.38253,43.80816), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.29676,43.85708,-76.36104,43.87259), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.36104,43.87259,-73.37405,43.87556), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.37405,43.87556,-76.36104,43.87259), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.44185,43.88286,-73.37405,43.87556), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.41214,43.92568,-73.40774,43.92989), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.40774,43.92989,-76.41214,43.92568), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.27931,43.97246,-73.41125,43.9756), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.41125,43.9756,-76.27931,43.97246), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.40598,44.01149,-76.30767,44.02528), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.30767,44.02528,-76.37556,44.03154), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.37556,44.03154,-76.30767,44.02528), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.43688,44.04258,-76.37556,44.03154), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.36184,44.07272,-73.41632,44.09942), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.41632,44.09942,-76.37071,44.1005), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.37071,44.1005,-73.41632,44.09942), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.35568,44.13326,-73.39987,44.15249), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.39987,44.15249,-76.33458,44.16495), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.33458,44.16495,-73.3954,44.1669), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.3954,44.1669,-76.33458,44.16495), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.28655,44.20377,-76.20678,44.21454), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.20678,44.21454,-76.28655,44.20377), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.34989,44.23036,-76.16427,44.2396), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.16427,44.2396,-73.34989,44.23036), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.31662,44.25777,-73.31746,44.26352), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.31746,44.26352,-73.31662,44.25777), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.16183,44.28078,-73.31746,44.26352), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.09735,44.29955,-73.32423,44.31002), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.32423,44.31002,-76.09735,44.29955), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-76.001,44.34753,-75.94954,44.34913), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.94954,44.34913,-76.001,44.34753), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.33464,44.35688,-75.94954,44.34913), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.32095,44.38267,-75.86127,44.40519), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.86127,44.40519,-75.83413,44.42243), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.83413,44.42243,-75.86127,44.40519), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.29361,44.44056,-75.83413,44.42243), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.80778,44.47164,-73.29361,44.44056), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.31287,44.50725,-75.76623,44.51585), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.76623,44.51585,-73.31287,44.50725), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.34798,44.54616,-73.36268,44.56246), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.36268,44.56246,-73.36728,44.56755), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.36728,44.56755,-73.36268,44.56246), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.38997,44.61962,-75.56741,44.65871), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.56741,44.65871,-73.38997,44.61962), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.36556,44.7003,-75.56741,44.65871), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.35767,44.75102,-75.42394,44.75633), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.42394,44.75633,-73.35767,44.75102), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.33443,44.80219,-75.33374,44.80638), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.33374,44.80638,-73.34201,44.80808), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.34201,44.80808,-75.33374,44.80638), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.36568,44.82645,-73.34201,44.80808), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.37982,44.85704,-75.25552,44.85765), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.25552,44.85765,-73.37982,44.85704), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.14296,44.90024,-73.33898,44.91768), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.33898,44.91768,-75.06625,44.93017), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.06625,44.93017,-73.33898,44.91768), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-75.00516,44.9584,-73.34474,44.97047), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.34474,44.97047,-74.99276,44.97745), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.99276,44.97745,-74.90796,44.98336), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.90796,44.98336,-74.99276,44.97745), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.74464,44.99058,-74.72581,44.99179), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.72581,44.99179,-74.23414,44.99215), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.23414,44.99215,-74.72581,44.99179), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.43693,44.99618,-74.64474,44.99703), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.64474,44.99703,-74.02743,44.99737), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.02743,44.99737,-74.64474,44.99703), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.61105,44.9992,-74.02743,44.99737), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.8746,45.00122,-74.61105,44.9992), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.63972,45.00346,-73.8746,45.00122), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-73.34312,45.01084,-74.83467,45.01468), mapfile, tile_dir, 0, 11, "new york-ny")
	render_tiles((-74.83467,45.01468,-73.34312,45.01084), mapfile, tile_dir, 0, 11, "new york-ny")