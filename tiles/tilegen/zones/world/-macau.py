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
    # Region Name: Macau

	render_tiles((113.5708,22.0917,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5728,22.0917,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5661,22.0917,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5686,22.0917,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5639,22.0919,113.5708,22.0917), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5756,22.0919,113.5708,22.0917), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5617,22.0922,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5778,22.0922,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5803,22.0922,113.5639,22.0919), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5822,22.0925,113.56,22.0928), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.56,22.0928,113.5822,22.0925), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5839,22.0931,113.5858,22.0933), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5858,22.0933,113.5839,22.0931), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5583,22.0936,113.5858,22.0933), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5878,22.0939,113.5583,22.0936), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5569,22.0944,113.5878,22.0939), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5894,22.0944,113.5878,22.0939), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5911,22.0953,113.5553,22.0958), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5553,22.0958,113.5928,22.0961), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5928,22.0961,113.5553,22.0958), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5542,22.0969,113.5928,22.0961), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5939,22.0969,113.5928,22.0961), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5531,22.0981,113.5953,22.0983), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5953,22.0983,113.5531,22.0981), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5519,22.0994,113.5953,22.0983), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5964,22.0994,113.5953,22.0983), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5508,22.1006,113.5975,22.1008), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5975,22.1008,113.5508,22.1006), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.55,22.1019,113.5986,22.1022), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5986,22.1022,113.55,22.1019), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5997,22.1033,113.5494,22.1036), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5494,22.1036,113.5997,22.1033), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6003,22.105,113.5489,22.1053), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5489,22.1053,113.6003,22.105), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6011,22.1064,113.5483,22.1069), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5483,22.1069,113.6011,22.1064), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6019,22.1081,113.5475,22.1086), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5475,22.1086,113.6019,22.1081), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6025,22.11,113.5469,22.1106), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5469,22.1106,113.6025,22.11), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6031,22.1117,113.5469,22.1125), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5469,22.1125,113.6031,22.1117), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6033,22.1139,113.5464,22.1144), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5464,22.1144,113.6033,22.1139), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1156,113.5458,22.1167), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5458,22.1167,113.6036,22.1156), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6039,22.1181,113.5458,22.1183), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5458,22.1183,113.6039,22.1181), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6039,22.1197,113.5458,22.1206), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5458,22.1206,113.6039,22.1197), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1222,113.5456,22.1225), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5456,22.1225,113.6036,22.1222), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1242,113.5456,22.125), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5456,22.125,113.6036,22.1242), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1267,113.5456,22.1272), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5456,22.1272,113.6036,22.1267), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1289,113.545,22.1292), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.545,22.1292,113.6036,22.1289), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5447,22.1311,113.545,22.1292), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6039,22.1311,113.545,22.1292), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6039,22.1331,113.5442,22.135), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5444,22.1331,113.5442,22.135), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5442,22.135,113.6036,22.1353), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1353,113.5442,22.135), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5439,22.1367,113.6036,22.1372), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6036,22.1372,113.5439,22.1367), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5433,22.1386,113.6031,22.1392), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6031,22.1392,113.5433,22.1386), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5425,22.14,113.6031,22.1392), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6028,22.1411,113.5419,22.1419), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5419,22.1419,113.6028,22.1411), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6025,22.1431,113.5414,22.1436), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5414,22.1436,113.6025,22.1431), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6022,22.1453,113.6017,22.1469), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5406,22.1453,113.6017,22.1469), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6017,22.1469,113.54,22.1472), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.54,22.1472,113.6017,22.1469), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5394,22.1486,113.6014,22.1492), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6014,22.1492,113.5394,22.1486), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5386,22.1506,113.6008,22.1508), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6008,22.1508,113.5386,22.1506), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5378,22.1519,113.6003,22.1525), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.6003,22.1525,113.5378,22.1519), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5369,22.1536,113.5997,22.1544), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5997,22.1544,113.5361,22.155), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5361,22.155,113.5997,22.1544), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5992,22.1561,113.5353,22.1564), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5353,22.1564,113.5992,22.1561), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5344,22.1578,113.5992,22.1583), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5992,22.1583,113.5344,22.1578), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5336,22.1592,113.5989,22.16), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5989,22.16,113.5328,22.1606), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5328,22.1606,113.5989,22.16), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5317,22.1619,113.5328,22.1606), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5983,22.1619,113.5328,22.1606), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5311,22.1636,113.5978,22.1642), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5978,22.1642,113.5311,22.1636), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.53,22.165,113.5978,22.1642), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5972,22.1661,113.5292,22.1664), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5292,22.1664,113.5972,22.1661), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5969,22.1678,113.5286,22.1683), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5286,22.1683,113.5969,22.1678), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5961,22.1694,113.5281,22.1697), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5281,22.1697,113.5961,22.1694), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5956,22.1711,113.5275,22.1717), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5275,22.1717,113.5956,22.1711), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.595,22.1725,113.5275,22.1717), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5275,22.1739,113.5942,22.1742), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5942,22.1742,113.5275,22.1739), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5933,22.1758,113.5275,22.1764), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5275,22.1764,113.5933,22.1758), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5928,22.1775,113.5278,22.1783), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5278,22.1783,113.5917,22.1789), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5917,22.1789,113.5278,22.1783), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5283,22.1803,113.52873,22.18151), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5911,22.1803,113.52873,22.18151), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.52873,22.18151,113.5289,22.1819), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.52873,22.18151,113.5289,22.1819), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5289,22.1819,113.52873,22.18151), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5903,22.1819,113.52873,22.18151), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5894,22.1833,113.5294,22.1839), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5294,22.1839,113.5894,22.1833), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5883,22.1847,113.5294,22.1839), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5297,22.1858,113.5875,22.1861), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5875,22.1861,113.5297,22.1858), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5864,22.1875,113.53,22.1878), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.53,22.1878,113.5864,22.1875), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5856,22.1889,113.5306,22.1894), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5306,22.1894,113.5856,22.1889), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5847,22.1903,113.5311,22.1911), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5311,22.1911,113.5833,22.1914), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5833,22.1914,113.5311,22.1911), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5825,22.1928,113.5317,22.1933), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5317,22.1933,113.5825,22.1928), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5814,22.1942,113.5317,22.1933), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5803,22.1953,113.5317,22.1956), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5317,22.1956,113.5803,22.1953), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5792,22.1964,113.5317,22.1956), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5317,22.1978,113.5767,22.1989), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5778,22.1978,113.5767,22.1989), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5767,22.1989,113.5317,22.1978), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5317,22.2,113.5756,22.2006), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5756,22.2006,113.5317,22.2), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5744,22.2017,113.5314,22.2019), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5314,22.2019,113.5744,22.2017), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5733,22.2028,113.57274,22.20308), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.57274,22.20308,113.5733,22.2028), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5725,22.2036,113.5319,22.2039), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5319,22.2039,113.5725,22.2036), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5714,22.2047,113.57,22.2053), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.57,22.2053,113.5325,22.2058), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5325,22.2058,113.5686,22.2061), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5686,22.2061,113.5325,22.2058), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5669,22.2075,113.5653,22.2083), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5333,22.2075,113.5653,22.2083), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5653,22.2083,113.5342,22.2089), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5342,22.2089,113.5639,22.2092), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5639,22.2092,113.5342,22.2089), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5622,22.21,113.56054,22.21042), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.56054,22.21042,113.535,22.2106), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.535,22.2106,113.56054,22.21042), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5606,22.2106,113.56054,22.21042), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5589,22.2114,113.5361,22.2117), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5361,22.2117,113.5569,22.2119), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5569,22.2119,113.5361,22.2117), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5378,22.2122,113.5569,22.2119), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5553,22.2125,113.5378,22.2122), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5394,22.2131,113.5411,22.2136), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5533,22.2131,113.5411,22.2136), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5411,22.2136,113.5394,22.2131), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5514,22.2136,113.5394,22.2131), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5431,22.2142,113.5472,22.2144), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5494,22.2142,113.5472,22.2144), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5472,22.2144,113.5431,22.2142), mapfile, tile_dir, 0, 11, "-macau")
	render_tiles((113.5453,22.2144,113.5431,22.2142), mapfile, tile_dir, 0, 11, "-macau")