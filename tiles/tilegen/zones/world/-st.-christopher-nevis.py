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
    # Region: 
    # Region Name: St. Christopher-Nevis

	render_tiles((-62.585,17.0917,-62.5914,17.0919), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.585,17.0917,-62.5914,17.0919), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5914,17.0919,-62.585,17.0917), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5972,17.0928,-62.5914,17.0919), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5803,17.0939,-62.5528,17.0942), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5528,17.0942,-62.5803,17.0939), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6033,17.0942,-62.5803,17.0939), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5583,17.0947,-62.5528,17.0942), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6106,17.0953,-62.5756,17.0958), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5756,17.0958,-62.5478,17.0961), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5478,17.0961,-62.5756,17.0958), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5636,17.0969,-62.5703,17.0972), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5703,17.0972,-62.5636,17.0969), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6142,17.0986,-62.5444,17.0997), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5444,17.0997,-62.6142,17.0986), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6172,17.1025,-62.5414,17.1042), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5414,17.1042,-62.6172,17.1025), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6197,17.1069,-62.5386,17.1086), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5386,17.1086,-62.6197,17.1069), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6219,17.1114,-62.5386,17.1086), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5372,17.1147,-62.6239,17.1158), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6239,17.1158,-62.5372,17.1147), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6253,17.1208,-62.6239,17.1158), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5353,17.1208,-62.6239,17.1158), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6261,17.1267,-62.5344,17.1275), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5344,17.1275,-62.6261,17.1267), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6258,17.1328,-62.5342,17.1336), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5342,17.1336,-62.6258,17.1328), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6256,17.1392,-62.5347,17.1394), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5347,17.1394,-62.6256,17.1392), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5353,17.145,-62.6244,17.1458), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6244,17.1458,-62.5353,17.145), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5367,17.15,-62.6236,17.1511), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6236,17.1511,-62.5367,17.15), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5381,17.155,-62.6219,17.1572), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6219,17.1572,-62.5381,17.155), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5394,17.1603,-62.6203,17.1633), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6203,17.1633,-62.54,17.1658), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.54,17.1658,-62.6203,17.1633), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6181,17.1686,-62.5406,17.1714), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5406,17.1714,-62.6158,17.1739), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6158,17.1739,-62.5406,17.1714), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5414,17.1769,-62.6136,17.1789), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6136,17.1789,-62.5414,17.1769), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5425,17.1822,-62.6114,17.1842), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6114,17.1842,-62.5425,17.1822), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5447,17.1867,-62.6114,17.1842), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5486,17.19,-62.5553,17.1903), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5553,17.1903,-62.5486,17.19), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.61,17.1911,-62.5614,17.1917), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5614,17.1917,-62.61,17.1911), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5661,17.1944,-62.5928,17.1961), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5928,17.1961,-62.5708,17.1972), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5708,17.1972,-62.5875,17.1975), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5997,17.1972,-62.5875,17.1975), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.61,17.1972,-62.5875,17.1975), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5875,17.1975,-62.5708,17.1972), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5761,17.1994,-62.5828,17.1997), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.5828,17.1997,-62.5761,17.1994), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6014,17.2008,-62.6072,17.2017), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6072,17.2017,-62.6014,17.2008), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6539,17.2089,-62.6594,17.2111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6539,17.2089,-62.6594,17.2111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6594,17.2111,-62.6539,17.2089), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6492,17.2111,-62.6539,17.2089), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6444,17.2133,-62.6594,17.2111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6617,17.2156,-62.6403,17.2161), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6403,17.2161,-62.6617,17.2156), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6358,17.2181,-62.63,17.2189), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.63,17.2189,-62.6358,17.2181), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6253,17.2208,-62.6622,17.2211), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6622,17.2211,-62.6253,17.2208), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6217,17.2244,-62.6628,17.2269), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6628,17.2269,-62.6217,17.2244), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6206,17.2314,-62.6625,17.2331), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6625,17.2331,-62.6206,17.2314), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6214,17.2369,-62.6622,17.2392), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6622,17.2392,-62.6214,17.2369), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6225,17.2419,-62.6644,17.2436), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6644,17.2436,-62.6225,17.2419), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6264,17.2453,-62.6328,17.2469), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6328,17.2469,-62.6667,17.2483), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6667,17.2483,-62.6381,17.2492), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6381,17.2492,-62.6667,17.2483), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6689,17.2528,-62.6414,17.2531), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6414,17.2531,-62.6689,17.2528), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.645,17.2564,-62.6711,17.2572), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6711,17.2572,-62.645,17.2564), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6489,17.2594,-62.6742,17.2611), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6742,17.2611,-62.6489,17.2594), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6522,17.2636,-62.6772,17.265), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6772,17.265,-62.6522,17.2636), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.655,17.2675,-62.6811,17.2683), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6811,17.2683,-62.655,17.2675), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6589,17.2708,-62.6847,17.2717), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6847,17.2717,-62.6589,17.2708), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6625,17.2739,-62.6886,17.275), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6886,17.275,-62.6625,17.2739), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6675,17.2767,-62.6925,17.2783), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6925,17.2783,-62.6722,17.2794), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6722,17.2794,-62.6925,17.2783), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6972,17.2808,-62.6722,17.2794), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6761,17.2828,-62.7019,17.2836), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7019,17.2836,-62.725,17.2842), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.725,17.2842,-62.7317,17.2844), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7317,17.2844,-62.725,17.2842), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7381,17.2847,-62.7317,17.2844), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7197,17.2856,-62.7075,17.2858), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.745,17.2856,-62.7075,17.2858), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7075,17.2858,-62.7197,17.2856), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6797,17.2861,-62.7075,17.2858), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7139,17.2861,-62.7075,17.2858), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7525,17.2867,-62.6797,17.2861), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7594,17.2875,-62.7525,17.2867), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7658,17.2892,-62.6828,17.29), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6828,17.29,-62.7658,17.2892), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7719,17.2908,-62.6828,17.29), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7783,17.2922,-62.7719,17.2908), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6858,17.2939,-62.7831,17.295), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7831,17.295,-62.6858,17.2939), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7886,17.2972,-62.6881,17.2983), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6881,17.2983,-62.7886,17.2972), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7933,17.3,-62.6881,17.2983), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6903,17.3028,-62.7972,17.3033), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7972,17.3033,-62.6903,17.3028), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6925,17.3072,-62.8031,17.3111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8,17.3072,-62.8031,17.3111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8031,17.3111,-62.6939,17.3125), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6939,17.3125,-62.8031,17.3111), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8056,17.3156,-62.6953,17.3175), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6953,17.3175,-62.81,17.3183), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.81,17.3183,-62.6953,17.3175), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8164,17.3197,-62.8236,17.3208), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8236,17.3208,-62.8164,17.3197), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8306,17.3219,-62.6967,17.3225), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6967,17.3225,-62.8306,17.3219), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8353,17.3244,-62.6967,17.3225), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8392,17.3278,-62.6972,17.3281), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6972,17.3281,-62.8392,17.3278), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8414,17.3325,-62.6986,17.3331), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.6986,17.3331,-62.8414,17.3325), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8442,17.3364,-62.7017,17.3369), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7017,17.3369,-62.8442,17.3364), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7064,17.3397,-62.8458,17.3414), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8458,17.3414,-62.7108,17.3425), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7108,17.3425,-62.8458,17.3414), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7167,17.3447,-62.8472,17.3464), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8472,17.3464,-62.7167,17.3447), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7194,17.3486,-62.8472,17.3464), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8483,17.3514,-62.7208,17.3536), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7208,17.3536,-62.8483,17.3514), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8497,17.3564,-62.7231,17.3581), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7231,17.3581,-62.8539,17.3597), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8539,17.3597,-62.7231,17.3581), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7264,17.3619,-62.8578,17.3631), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8578,17.3631,-62.7264,17.3619), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.73,17.3653,-62.8617,17.3664), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8617,17.3664,-62.73,17.3653), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7347,17.3681,-62.8617,17.3664), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7403,17.3703,-62.8639,17.3708), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8639,17.3708,-62.7403,17.3703), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7456,17.3725,-62.8639,17.3708), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7511,17.3744,-62.8608,17.3753), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8608,17.3753,-62.7511,17.3744), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.755,17.3778,-62.8575,17.3789), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8575,17.3789,-62.755,17.3778), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7597,17.3806,-62.8575,17.3789), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7644,17.3833,-62.7697,17.3856), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7697,17.3856,-62.8564,17.3858), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8564,17.3858,-62.7697,17.3856), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7744,17.3883,-62.8528,17.3894), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8528,17.3894,-62.78,17.3903), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.78,17.3903,-62.8528,17.3894), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7847,17.3931,-62.7911,17.3947), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8494,17.3931,-62.7911,17.3947), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7911,17.3947,-62.8453,17.3961), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8453,17.3961,-62.7964,17.3969), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.7964,17.3969,-62.8453,17.3961), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8414,17.3989,-62.8019,17.3992), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8019,17.3992,-62.8414,17.3989), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8331,17.4,-62.8286,17.4006), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8286,17.4006,-62.8331,17.4), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8386,17.4019,-62.8058,17.4022), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8058,17.4022,-62.8386,17.4019), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8244,17.4036,-62.8058,17.4022), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8097,17.4056,-62.8211,17.4072), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8211,17.4072,-62.8097,17.4056), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8128,17.4094,-62.8175,17.4108), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")
	render_tiles((-62.8175,17.4108,-62.8128,17.4094), mapfile, tile_dir, 0, 11, "-st.-christopher-nevis")