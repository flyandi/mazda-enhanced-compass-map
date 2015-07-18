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
    # Region: South Carolina
    # Region Name: SC

	render_tiles((-80.88552,32.0346,-80.94323,32.05782), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.94323,32.05782,-80.86743,32.07849), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.86743,32.07849,-81.03827,32.08447), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.03827,32.08447,-80.86743,32.07849), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.85874,32.09958,-81.00675,32.10115), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.00675,32.10115,-80.85874,32.09958), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.8125,32.10975,-81.11333,32.11321), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.11333,32.11321,-80.8125,32.10975), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.72146,32.16043,-81.11936,32.17714), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.11936,32.17714,-80.72146,32.16043), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.66917,32.21678,-81.1476,32.22717), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.1476,32.22717,-80.66917,32.21678), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.15353,32.23769,-81.1476,32.22717), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.72697,32.26571,-80.59639,32.27355), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.59639,32.27355,-81.12803,32.2763), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.12803,32.2763,-80.59639,32.27355), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.53943,32.28702,-80.64479,32.2915), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.64479,32.2915,-80.76604,32.29261), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.76604,32.29261,-80.64479,32.2915), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.7146,32.32566,-80.45519,32.32646), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.45519,32.32646,-80.7146,32.32566), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.13303,32.33479,-80.45519,32.32646), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.4343,32.37519,-81.17347,32.3849), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.17347,32.3849,-80.4343,32.37519), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.4575,32.41026,-81.19493,32.41149), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.19493,32.41149,-80.4575,32.41026), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.48462,32.46098,-81.19483,32.46509), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.19483,32.46509,-80.48462,32.46098), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.33835,32.47873,-80.38683,32.47881), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.38683,32.47881,-80.33835,32.47873), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.47229,32.48335,-80.41591,32.48489), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.41591,32.48489,-80.47136,32.48504), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.47136,32.48504,-80.41591,32.48489), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.46911,32.48913,-80.47136,32.48504), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.46571,32.4953,-80.46911,32.48913), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.24944,32.52936,-80.24636,32.53111), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.24636,32.53111,-80.24944,32.52936), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.27493,32.54416,-80.19011,32.54684), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.19011,32.54684,-81.28424,32.54711), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.28424,32.54711,-80.19011,32.54684), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.32875,32.56123,-81.28424,32.54711), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.14841,32.57848,-81.32875,32.56123), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.3869,32.59896,-80.07704,32.60332), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.07704,32.60332,-81.39711,32.60559), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.39711,32.60559,-80.0008,32.60589), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.0008,32.60589,-81.39711,32.60559), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.96847,32.63973,-81.39382,32.65349), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.39382,32.65349,-79.96847,32.63973), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.88496,32.6844,-81.39382,32.65349), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.86835,32.73485,-81.41267,32.73908), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.41267,32.73908,-79.86835,32.73485), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.41312,32.74426,-81.41267,32.73908), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.81824,32.76635,-81.41312,32.74426), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.72639,32.806,-81.42062,32.83122), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.42062,32.83122,-79.69514,32.8504), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.69514,32.8504,-81.42062,32.83122), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.46407,32.89781,-79.60131,32.89815), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.60131,32.89815,-81.46407,32.89781), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.56976,32.92669,-81.49957,32.94372), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.49957,32.94372,-79.56976,32.92669), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.60662,32.97225,-81.49957,32.94372), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.4835,33.00127,-79.58073,33.00645), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.58073,33.00645,-79.35996,33.00667), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.35996,33.00667,-79.58073,33.00645), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.42345,33.01509,-81.50203,33.01511), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.50203,33.01511,-79.42345,33.01509), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.52245,33.03535,-81.54397,33.0444), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.54397,33.0444,-79.33931,33.05034), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.33931,33.05034,-81.54397,33.0444), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.60166,33.08469,-81.61596,33.08934), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.61596,33.08934,-79.32991,33.08999), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.32991,33.08999,-81.61596,33.08934), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.65843,33.10315,-79.29159,33.10977), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.29159,33.10977,-81.65843,33.10315), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.27449,33.12006,-79.29159,33.10977), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.75514,33.15155,-79.21545,33.15557), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.21545,33.15557,-81.75514,33.15155), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.76251,33.19727,-81.76354,33.20365), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.76354,33.20365,-79.17239,33.20658), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.17239,33.20658,-81.76354,33.20365), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.18056,33.23796,-81.84654,33.24175), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.84654,33.24175,-79.18056,33.23796), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.8465,33.24725,-81.84654,33.24175), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.84614,33.30384,-79.16233,33.32725), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.16233,33.32725,-81.93274,33.34354), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.93274,33.34354,-79.16233,33.32725), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.13544,33.40387,-81.92012,33.41075), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.92012,33.41075,-79.13544,33.40387), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.92634,33.46294,-79.08459,33.48367), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.08459,33.48367,-81.99094,33.49424), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.99094,33.49424,-79.08459,33.48367), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.01656,33.52906,-79.02852,33.53337), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.02852,33.53337,-82.01656,33.52906), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.02824,33.54493,-79.02852,33.53337), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.99563,33.57207,-82.10624,33.59564), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.10624,33.59564,-82.11465,33.59791), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.11465,33.59791,-82.10624,33.59564), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.14246,33.6054,-82.16191,33.61064), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.16191,33.61064,-82.14246,33.6054), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.93808,33.63983,-82.19975,33.65761), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.19975,33.65761,-78.93808,33.63983), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.21594,33.68775,-82.22372,33.70224), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.22372,33.70224,-78.86293,33.70565), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.86293,33.70565,-82.22372,33.70224), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.2391,33.73087,-78.86293,33.70565), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.77274,33.76851,-82.2391,33.73087), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.67226,33.81759,-82.32448,33.82003), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.32448,33.82003,-78.67226,33.81759), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.54109,33.85111,-82.43115,33.86705), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.43115,33.86705,-78.54109,33.85111), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.61593,33.91552,-82.51295,33.93697), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.51295,33.93697,-78.65089,33.94507), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.65089,33.94507,-82.55684,33.94535), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.55684,33.94535,-78.65089,33.94507), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.563,33.95655,-82.55684,33.94535), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.59186,34.00902,-82.59503,34.01352), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.59503,34.01352,-82.59186,34.00902), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-78.81171,34.08101,-82.6428,34.08131), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.6428,34.08131,-78.81171,34.08101), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.71537,34.14817,-82.73511,34.21261), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.73511,34.21261,-82.74498,34.24486), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.74498,34.24486,-82.73511,34.21261), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.77463,34.28837,-82.78031,34.2967), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.78031,34.2967,-79.07117,34.29924), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.07117,34.29924,-82.78031,34.2967), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.82342,34.35887,-82.842,34.39977), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.842,34.39977,-82.82342,34.35887), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.87383,34.47151,-82.99509,34.47248), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.99509,34.47248,-82.99139,34.47298), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.99139,34.47298,-82.99509,34.47248), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.92577,34.4818,-82.99139,34.47298), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.04829,34.49325,-83.05057,34.49505), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.05057,34.49505,-83.04829,34.49325), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.09686,34.53152,-83.10287,34.53743), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.10287,34.53743,-83.09686,34.53152), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.35832,34.54536,-83.10287,34.53743), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.15458,34.5882,-83.2214,34.60995), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.2214,34.60995,-79.45028,34.62061), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.45028,34.62061,-79.46197,34.63017), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.46197,34.63017,-79.45028,34.62061), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.27796,34.64485,-79.46197,34.63017), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.33869,34.682,-83.34004,34.68633), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.34004,34.68633,-83.33869,34.682), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.34961,34.71701,-83.35324,34.72865), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.35324,34.72865,-83.34961,34.71701), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.35324,34.72865,-83.34961,34.71701), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.32006,34.75962,-83.32387,34.78971), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.32387,34.78971,-79.6753,34.80474), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.6753,34.80474,-79.69295,34.80496), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.69295,34.80496,-79.6753,34.80474), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.92431,34.80782,-79.9276,34.80787), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-79.9276,34.80787,-79.92431,34.80782), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.07722,34.80972,-79.9276,34.80787), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.32042,34.81361,-80.56167,34.81748), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.56167,34.81748,-80.62578,34.81922), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.56166,34.81748,-80.62578,34.81922), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.62578,34.81922,-80.56167,34.81748), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.28481,34.82304,-80.797,34.82387), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.797,34.82387,-83.28481,34.82304), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.25258,34.85348,-80.797,34.82387), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.20118,34.88465,-83.25258,34.85348), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.14062,34.92492,-80.78204,34.93578), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.78204,34.93578,-83.14062,34.92492), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.12438,34.95524,-80.78204,34.93578), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.10861,35.00066,-80.84057,35.00147), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.84057,35.00147,-83.10861,35.00066), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-83.00843,35.02693,-81.04149,35.0447), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.04149,35.0447,-82.8975,35.05602), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.8975,35.05602,-81.04149,35.0447), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.05803,35.07319,-80.90624,35.07518), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.90624,35.07518,-81.05803,35.07319), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.74643,35.07913,-82.76206,35.08187), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.76206,35.08187,-82.74643,35.07913), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.78328,35.0856,-82.76206,35.08187), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-80.93495,35.10741,-81.03676,35.12255), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.03676,35.12255,-82.68604,35.12455), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.68604,35.12455,-81.03676,35.12255), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.57772,35.14648,-81.04227,35.14661), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.04227,35.14661,-82.57772,35.14648), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.04287,35.14925,-81.04227,35.14661), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.53256,35.15562,-81.04287,35.14925), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.32809,35.16229,-81.36761,35.16409), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.36761,35.16409,-81.32809,35.16229), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.49427,35.16988,-81.36761,35.16409), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.45561,35.17743,-81.76809,35.17971), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.76809,35.17971,-82.45561,35.17743), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.87411,35.18351,-81.96934,35.18693), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-81.96934,35.18693,-82.03965,35.18945), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.03965,35.18945,-82.04839,35.18964), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.04839,35.18964,-82.03965,35.18945), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.21625,35.19326,-82.29535,35.19497), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.29535,35.19497,-82.21625,35.19326), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.35302,35.1987,-82.29535,35.19497), mapfile, tile_dir, 0, 11, "south carolina-sc")
	render_tiles((-82.4113,35.20248,-82.35302,35.1987), mapfile, tile_dir, 0, 11, "south carolina-sc")