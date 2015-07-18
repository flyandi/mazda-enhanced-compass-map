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
    # Region: Montana
    # Region Name: MT

	render_tiles((-112.88177,44.38032,-112.82669,44.40527), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.82669,44.40527,-112.8219,44.40744), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.8219,44.40744,-112.82669,44.40527), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.95115,44.4167,-112.8219,44.40744), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.82819,44.44247,-112.38739,44.44806), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.38739,44.44806,-112.82819,44.44247), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.00685,44.47172,-111.04897,44.47407), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.04897,44.47407,-113.00685,44.47172), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.47321,44.48003,-111.04897,44.47407), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.60186,44.49102,-111.12265,44.49366), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.12265,44.49366,-112.60186,44.49102), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.73508,44.49916,-112.70782,44.50302), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.70782,44.50302,-112.73508,44.49916), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.80791,44.51172,-113.00683,44.51844), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.00683,44.51844,-111.80791,44.51172), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.1251,44.52853,-112.35892,44.52885), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.35892,44.52885,-112.1251,44.52853), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.14356,44.53573,-112.03413,44.53772), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.03413,44.53772,-111.14356,44.53573), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.2217,44.54352,-112.03413,44.53772), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.56281,44.55521,-111.61712,44.55713), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.61712,44.55713,-111.56281,44.55521), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.70422,44.56021,-111.61712,44.55713), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.8705,44.56403,-111.70422,44.56021), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.28619,44.56847,-111.8705,44.56403), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.20146,44.5757,-113.06107,44.57733), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.06107,44.57733,-111.20146,44.5757), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.51913,44.58292,-113.06107,44.57733), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.22416,44.6234,-111.05521,44.62493), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.05521,44.62493,-111.22416,44.6234), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.04935,44.62938,-111.05521,44.62493), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.05533,44.66626,-111.26875,44.66828), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.26875,44.66828,-111.05533,44.66626), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.46883,44.67934,-111.26875,44.66828), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.45603,44.6969,-113.10115,44.70858), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.10115,44.70858,-111.45603,44.6969), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.43879,44.72055,-111.32367,44.72447), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.32367,44.72447,-111.05551,44.72534), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.05551,44.72534,-111.32367,44.72447), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.37779,44.75152,-111.38501,44.75513), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.38501,44.75513,-111.37779,44.75152), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.13139,44.76474,-111.38501,44.75513), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.30151,44.79899,-113.24717,44.82295), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.24717,44.82295,-113.37715,44.83486), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.37715,44.83486,-113.42238,44.8426), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.42238,44.8426,-113.37715,44.83486), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.05689,44.86666,-113.42238,44.8426), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.47457,44.91085,-113.44896,44.95354), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.44896,44.95354,-110.70527,44.99232), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.70527,44.99232,-106.26359,44.99379), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.26359,44.99379,-110.70527,44.99232), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.88877,44.99589,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.0577,44.99743,-106.02488,44.99758), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.02488,44.99758,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.03914,44.99852,-110.32444,44.99916), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.32444,44.99916,-109.06226,44.99962), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.06226,44.99962,-108.62149,44.99968), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-108.62149,44.99968,-108.50068,44.99969), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-108.50068,44.99969,-108.62149,44.99968), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.02527,45.00029,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.03841,45.00029,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.07661,45.0003,-105.02527,45.00029), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.84807,45.0004,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-108.24853,45.00063,-105.84807,45.0004), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.04428,45.00135,-107.35144,45.00141), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.35144,45.00141,-111.04428,45.00135), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.91175,45.00154,-107.99735,45.00157), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.99735,45.00157,-107.91175,45.00154), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.57432,45.00263,-109.79869,45.00292), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.79869,45.00292,-110.78501,45.00295), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.78501,45.00295,-109.79869,45.00292), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.99505,45.00317,-110.78501,45.00295), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.10345,45.0059,-113.43773,45.00697), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.43773,45.00697,-109.10345,45.0059), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.45197,45.05925,-113.51082,45.0999), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.51082,45.0999,-104.03998,45.12499), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.03998,45.12499,-113.57467,45.12841), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.57467,45.12841,-104.03998,45.12499), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04014,45.21289,-113.65006,45.23471), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.65006,45.23471,-104.04014,45.21289), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.7356,45.32527,-104.04036,45.33595), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04036,45.33595,-113.7356,45.32527), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.73239,45.38506,-113.76337,45.42773), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.76337,45.42773,-113.73239,45.38506), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.27922,45.48062,-113.75999,45.48074), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.75999,45.48074,-114.27922,45.48062), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04176,45.49079,-114.36852,45.49272), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.36852,45.49272,-104.04176,45.49079), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.80285,45.52316,-114.25184,45.53781), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.25184,45.53781,-114.45676,45.54398), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.45676,45.54398,-114.18647,45.54554), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.18647,45.54554,-114.45676,45.54398), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04194,45.55792,-114.50634,45.55922), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.50634,45.55922,-104.04194,45.55792), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.52369,45.5852,-113.80673,45.60215), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.80673,45.60215,-114.08315,45.604), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.08315,45.604,-113.80673,45.60215), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.53813,45.60683,-114.08315,45.604), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.8614,45.62366,-114.53813,45.60683), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.89888,45.64417,-114.53577,45.65061), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.53577,45.65061,-114.01497,45.65401), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.01497,45.65401,-114.53577,45.65061), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.49964,45.66904,-113.94825,45.68252), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.94825,45.68252,-114.49964,45.66904), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.01563,45.69613,-113.97157,45.70064), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.97157,45.70064,-114.01563,45.69613), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.50487,45.72218,-113.97157,45.70064), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.0426,45.75,-114.50487,45.72218), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.56251,45.77993,-104.0426,45.75), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.51714,45.83599,-114.42296,45.85538), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.42296,45.85538,-104.04378,45.86471), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04378,45.86471,-114.42296,45.85538), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04413,45.88198,-114.38824,45.88234), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.38824,45.88234,-104.04413,45.88198), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.41317,45.91148,-114.38824,45.88234), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04544,45.94531,-114.40226,45.96149), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.40226,45.96149,-104.04544,45.94531), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.44119,45.98845,-114.40226,45.96149), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.48024,46.03033,-114.44119,45.98845), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.46005,46.0971,-114.5213,46.12529), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.5213,46.12529,-114.46005,46.0971), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.51471,46.16773,-114.44593,46.17393), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.44593,46.17393,-114.51471,46.16773), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.44982,46.23712,-114.44133,46.2738), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.44133,46.2738,-104.04547,46.28019), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04547,46.28019,-114.44133,46.2738), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.43171,46.31074,-104.04547,46.32455), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04547,46.32455,-114.43171,46.31074), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.42246,46.3871,-114.38476,46.41178), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.38476,46.41178,-114.42246,46.3871), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.40302,46.49868,-114.35166,46.50812), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.35166,46.50812,-104.04505,46.50979), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04505,46.50979,-114.35166,46.50812), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04513,46.54093,-104.04505,46.50979), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.33134,46.57778,-104.04513,46.54093), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04538,46.64144,-114.54732,46.64449), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.54732,46.64449,-114.32067,46.64696), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.32067,46.64696,-114.45324,46.64927), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.45324,46.64927,-114.32067,46.64696), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.59121,46.65257,-114.33587,46.65535), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.33587,46.65535,-114.59121,46.65257), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.62148,46.65814,-114.33587,46.65535), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.36071,46.66906,-114.62148,46.65814), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.6267,46.71289,-104.04557,46.71388), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04557,46.71388,-114.6267,46.71289), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.67689,46.73186,-114.76718,46.73883), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.76718,46.73883,-114.69901,46.74022), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.69901,46.74022,-114.76718,46.73883), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.79004,46.77873,-114.88059,46.81179), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.88059,46.81179,-114.79004,46.77873), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.94328,46.86797,-114.92743,46.91419), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.92743,46.91419,-114.96142,46.93289), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.96142,46.93289,-104.04554,46.93389), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04554,46.93389,-114.96142,46.93289), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.03165,46.97155,-104.04554,46.93389), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.07125,47.02208,-115.12092,47.06124), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.12092,47.06124,-115.07125,47.02208), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04479,47.12743,-115.18945,47.13103), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.18945,47.13103,-104.04479,47.12743), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.25579,47.17473,-115.29211,47.20986), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.29211,47.20986,-115.25579,47.17473), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.3269,47.25591,-115.37183,47.26521), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.37183,47.26521,-115.3269,47.25591), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.47096,47.28487,-115.37183,47.26521), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.53197,47.31412,-104.04531,47.33013), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04531,47.33013,-104.04531,47.33196), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04531,47.33196,-104.04531,47.33013), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.57862,47.36701,-104.04497,47.39746), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04497,47.39746,-115.71034,47.41778), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.71034,47.41778,-104.04497,47.39746), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.69293,47.45724,-115.63468,47.48176), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.63468,47.48176,-115.69293,47.45724), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.71702,47.53269,-115.72121,47.57632), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.72121,47.57632,-104.04391,47.60323), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04391,47.60323,-115.69428,47.62346), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.69428,47.62346,-104.04391,47.60323), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.73627,47.65476,-115.69428,47.62346), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.72377,47.69667,-115.73627,47.65476), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.83537,47.76096,-104.04238,47.80326), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04238,47.80326,-115.84547,47.81497), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.84547,47.81497,-104.04238,47.80326), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.90093,47.84306,-115.84547,47.81497), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.95995,47.89814,-115.90093,47.84306), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04393,47.97152,-116.03075,47.97335), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.03075,47.97335,-104.04393,47.97152), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.03838,47.98437,-116.03075,47.97335), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04409,47.9961,-104.04409,47.99611), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04409,47.99611,-104.04409,47.9961), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04915,47.99992,-104.04409,47.99611), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04891,48.12493,-116.04893,48.21584), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04893,48.21584,-104.04569,48.24142), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04569,48.24142,-116.04893,48.21584), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04895,48.30985,-104.04569,48.24142), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04678,48.38943,-116.04895,48.30985), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04916,48.48125,-104.04756,48.49414), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04756,48.49414,-116.04916,48.50206), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04916,48.50206,-104.04756,48.49414), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04809,48.63401,-116.04916,48.50206), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.0489,48.84739,-111.50081,48.99696), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.50081,48.99696,-111.2707,48.99723), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.2707,48.99723,-111.50081,48.99696), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.00392,48.99754,-113.69298,48.99763), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.69298,48.99763,-111.00392,48.99754), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.74321,48.99801,-111.85409,48.99807), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-111.85409,48.99807,-110.74321,48.99801), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.53162,48.99839,-113.11636,48.99846), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.11636,48.99846,-110.53162,48.99839), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.37593,48.99856,-113.11636,48.99846), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-113.90749,48.99886,-112.19359,48.99889), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.19359,48.99889,-112.14377,48.99892), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-112.14377,48.99892,-112.19359,48.99889), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.87553,48.99899,-112.14377,48.99892), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.43815,48.99919,-106.05054,48.99921), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.05054,48.99921,-110.43815,48.99919), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.00071,48.99923,-106.05054,48.99921), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.05751,48.99923,-106.05054,48.99921), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.20791,48.99923,-106.05054,48.99921), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-110.1716,48.99926,-106.11211,48.99928), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.11211,48.99928,-110.1716,48.99926), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.06815,48.99936,-108.54319,48.99938), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.35589,48.99936,-108.54319,48.99938), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.44102,48.99936,-108.54319,48.99938), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-108.54319,48.99938,-114.06815,48.99936), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.23399,48.99942,-108.54319,48.99938), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.26519,48.9995,-104.54364,48.99954), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.54364,48.99954,-108.2365,48.99956), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-108.2365,48.99956,-104.54364,48.99954), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-106.61754,48.99958,-108.2365,48.99956), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-105.77581,48.99964,-106.61754,48.99958), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.18021,48.9997,-105.77581,48.99964), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.7047,48.99987,-104.04874,48.99988), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-104.04874,48.99988,-107.7047,48.99987), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.17986,48.99991,-104.04874,48.99988), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.25072,49.00001,-107.36358,49.00002), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-107.36358,49.00002,-109.25072,49.00001), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.48969,49.00042,-109.50074,49.00044), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-109.50074,49.00044,-109.48969,49.00042), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.72821,49.00058,-115.50102,49.00069), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-115.50102,49.00069,-114.67822,49.00073), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.67822,49.00073,-115.50102,49.00069), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04919,49.00091,-114.67822,49.00073), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-116.04919,49.00091,-114.67822,49.00073), mapfile, tile_dir, 0, 11, "montana-mt")
	render_tiles((-114.37598,49.00139,-116.04919,49.00091), mapfile, tile_dir, 0, 11, "montana-mt")