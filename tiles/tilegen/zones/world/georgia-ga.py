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
    # Region: Georgia
    # Region Name: GA

	render_tiles((-82.09471,30.36077,-82.14331,30.36338), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.14331,30.36338,-82.09471,30.36077), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.05098,30.36837,-82.18004,30.36861), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.18004,30.36861,-82.05098,30.36837), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.04077,30.37014,-82.18004,30.36861), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.04201,30.40325,-82.21032,30.42458), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.21032,30.42458,-82.04201,30.40325), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.02823,30.44739,-82.21032,30.42458), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.20097,30.47443,-82.02823,30.44739), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.22943,30.52081,-82.01838,30.53118), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.01838,30.53118,-82.22943,30.52081), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.21861,30.5644,-82.41898,30.58092), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.41898,30.58092,-82.45958,30.58426), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.45958,30.58426,-82.45979,30.58428), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.45979,30.58428,-82.45958,30.58426), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.58401,30.59164,-82.68953,30.59789), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.68953,30.59789,-82.01573,30.6017), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.01573,30.6017,-82.68953,30.59789), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.87731,30.60902,-82.01573,30.6017), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.13143,30.62358,-83.13662,30.62389), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.13662,30.62389,-83.13143,30.62358), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.30935,30.63424,-83.35772,30.63714), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.35772,30.63714,-83.30935,30.63424), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.44893,30.64261,-83.49995,30.64566), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.49995,30.64566,-83.44893,30.64261), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.6117,30.65156,-82.04953,30.65554), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.04953,30.65554,-83.74373,30.65853), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.74373,30.65853,-82.04953,30.65554), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.82097,30.6626,-83.74373,30.65853), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.00745,30.67207,-84.08375,30.67594), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.08375,30.67594,-84.12499,30.67804), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.12499,30.67804,-84.08375,30.67594), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.28551,30.68481,-84.38075,30.68883), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.38075,30.68883,-82.04183,30.69237), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.04183,30.69237,-84.47452,30.69278), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.47452,30.69278,-82.04183,30.69237), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.813,30.70965,-81.44412,30.70971), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.44412,30.70971,-84.813,30.70965), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.86346,30.7115,-84.86469,30.71154), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.86469,30.71154,-84.86346,30.7115), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.56171,30.7156,-84.86469,30.71154), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.50722,30.72294,-81.52828,30.72336), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.52828,30.72336,-81.50722,30.72294), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.63327,30.7296,-81.52828,30.72336), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.66828,30.74464,-81.73224,30.74964), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.73224,30.74964,-82.03267,30.75067), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.03267,30.75067,-81.73224,30.74964), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.46006,30.76991,-84.91815,30.77208), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.91815,30.77208,-81.76338,30.77382), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.76338,30.77382,-84.91815,30.77208), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.99499,30.78607,-81.80854,30.79002), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.80854,30.79002,-81.86862,30.79276), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.86862,30.79276,-81.80854,30.79002), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.90235,30.82082,-81.44013,30.82137), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.44013,30.82137,-81.90598,30.82141), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.90598,30.82141,-81.44013,30.82137), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.94319,30.82744,-81.90598,30.82141), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.93428,30.83403,-81.94319,30.82744), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.9357,30.8787,-81.40515,30.9082), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.40515,30.9082,-84.98376,30.93698), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.98376,30.93698,-81.40515,30.9082), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.00606,30.97704,-81.40848,30.97772), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.40848,30.97772,-85.00606,30.97704), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.41252,30.99083,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.0025,31.00068,-81.41252,30.99083), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.42047,31.0167,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.01139,31.05355,-81.40127,31.07278), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.40127,31.07278,-85.02111,31.07546), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.02111,31.07546,-81.40127,31.07278), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.03562,31.10819,-81.4021,31.12538), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.4021,31.12538,-81.36824,31.13653), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.36824,31.13653,-81.4021,31.12538), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.10752,31.18645,-81.30496,31.20617), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.30496,31.20617,-85.10752,31.18645), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.28284,31.24433,-85.10819,31.25859), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.10819,31.25859,-81.28284,31.24433), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.26438,31.2946,-85.08977,31.29503), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.08977,31.29503,-81.26438,31.2946), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.26096,31.30391,-85.08883,31.30865), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.08883,31.30865,-81.26096,31.30391), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.08793,31.32165,-85.08883,31.30865), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.27934,31.35113,-85.09249,31.36288), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.09249,31.36288,-81.27934,31.35113), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.25862,31.40443,-85.06601,31.43136), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.06601,31.43136,-81.25862,31.40443), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.21349,31.46282,-85.07162,31.46838), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.07162,31.46838,-81.21349,31.46282), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.17725,31.51707,-85.05168,31.51954), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.05168,31.51954,-81.17725,31.51707), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.17483,31.5396,-85.04188,31.54468), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.04188,31.54468,-81.17483,31.5396), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.17308,31.55591,-85.04188,31.54468), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.05796,31.57084,-81.17308,31.55591), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.05817,31.62023,-81.13349,31.62335), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.13349,31.62335,-85.05817,31.62023), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.12553,31.69497,-81.13939,31.69992), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.13939,31.69992,-85.12553,31.69497), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.1353,31.71056,-81.13939,31.69992), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.13063,31.72269,-85.11893,31.73266), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.11893,31.73266,-81.13063,31.72269), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.07706,31.76126,-85.12544,31.76297), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.12544,31.76297,-81.07706,31.76126), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.12916,31.78028,-85.12544,31.76297), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.03687,31.81272,-85.14183,31.83926), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.14183,31.83926,-81.00032,31.85674), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.00032,31.85674,-85.14183,31.83926), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.11403,31.89336,-80.94136,31.91298), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.94136,31.91298,-85.11403,31.89336), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.91121,31.94377,-85.06783,31.96736), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.06783,31.96736,-80.84844,31.98828), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.84844,31.98828,-85.06359,31.99186), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.06359,31.99186,-80.84844,31.98828), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.84313,32.02423,-80.88552,32.0346), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.88552,32.0346,-80.84313,32.02423), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-80.94323,32.05782,-85.05141,32.06226), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.05141,32.06226,-80.94323,32.05782), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.03827,32.08447,-85.04706,32.08739), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.04706,32.08739,-81.03827,32.08447), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.00675,32.10115,-81.11333,32.11321), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.11333,32.11321,-81.00675,32.10115), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.05875,32.13602,-81.11333,32.11321), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.11936,32.17714,-84.99777,32.18545), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.99777,32.18545,-81.11936,32.17714), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.93013,32.21905,-81.1476,32.22717), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.1476,32.22717,-84.91994,32.23085), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.91994,32.23085,-81.1476,32.22717), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.15353,32.23769,-84.91994,32.23085), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.89184,32.2634,-81.12803,32.2763), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.12803,32.2763,-84.89184,32.2634), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.9557,32.30591,-81.13303,32.33479), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.13303,32.33479,-85.0081,32.33668), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.0081,32.33668,-81.13303,32.33479), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.98347,32.36319,-84.98115,32.37904), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.98115,32.37904,-81.17347,32.3849), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.17347,32.3849,-84.98115,32.37904), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.19493,32.41149,-81.17347,32.3849), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.97183,32.44284,-81.19483,32.46509), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.19483,32.46509,-84.97183,32.44284), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.99979,32.50707,-85.00113,32.51015), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.00113,32.51015,-84.99979,32.50707), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.0071,32.52387,-85.00113,32.51015), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.27493,32.54416,-81.28424,32.54711), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.28424,32.54711,-81.27493,32.54416), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.32875,32.56123,-81.28424,32.54711), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.06985,32.58315,-81.3869,32.59896), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.3869,32.59896,-81.39711,32.60559), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.39711,32.60559,-85.07607,32.60807), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.07607,32.60807,-81.39711,32.60559), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.39382,32.65349,-85.08853,32.65796), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.08853,32.65796,-81.39382,32.65349), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.11425,32.73045,-81.41267,32.73908), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.41267,32.73908,-81.41312,32.74426), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.41312,32.74426,-81.41267,32.73908), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.12453,32.75163,-81.41312,32.74426), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.16096,32.82667,-81.42062,32.83122), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.42062,32.83122,-85.16096,32.82667), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.1844,32.86132,-85.18612,32.87014), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.18612,32.87014,-85.1844,32.86132), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.46407,32.89781,-85.18612,32.87014), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.49957,32.94372,-81.46407,32.89781), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.50203,33.01511,-81.54397,33.0444), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.54397,33.0444,-81.50203,33.01511), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.60166,33.08469,-81.61596,33.08934), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.61596,33.08934,-81.60166,33.08469), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.65843,33.10315,-85.23244,33.10808), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.23244,33.10808,-81.65843,33.10315), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.23244,33.10808,-81.65843,33.10315), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.2366,33.12954,-85.23244,33.10808), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.75514,33.15155,-85.2366,33.12954), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.76251,33.19727,-81.76354,33.20365), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.76354,33.20365,-81.76251,33.19727), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.84654,33.24175,-81.8465,33.24725), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.8465,33.24725,-81.84654,33.24175), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.84614,33.30384,-81.93274,33.34354), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.93274,33.34354,-81.84614,33.30384), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.92012,33.41075,-85.29435,33.42799), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.29435,33.42799,-81.92012,33.41075), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.92634,33.46294,-85.30494,33.48276), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.30494,33.48276,-81.99094,33.49424), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-81.99094,33.49424,-85.30494,33.48276), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.01656,33.52906,-85.31405,33.52981), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.31405,33.52981,-82.01656,33.52906), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.02824,33.54493,-85.31405,33.52981), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.10624,33.59564,-82.11465,33.59791), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.11465,33.59791,-82.10624,33.59564), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.14246,33.6054,-82.16191,33.61064), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.16191,33.61064,-82.14246,33.6054), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.33812,33.65311,-82.19975,33.65761), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.19975,33.65761,-85.33812,33.65311), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.21594,33.68775,-82.22372,33.70224), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.22372,33.70224,-82.21594,33.68775), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.2391,33.73087,-82.22372,33.70224), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.36053,33.76796,-82.2391,33.73087), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.32448,33.82003,-82.43115,33.86705), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.43115,33.86705,-85.38667,33.9017), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.38667,33.9017,-82.43115,33.86705), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.51295,33.93697,-82.55684,33.94535), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.55684,33.94535,-82.51295,33.93697), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.563,33.95655,-85.39887,33.96413), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.39887,33.96413,-82.563,33.95655), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.39887,33.96413,-82.563,33.95655), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.59186,34.00902,-82.59503,34.01352), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.59503,34.01352,-82.59186,34.00902), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.42107,34.08081,-82.6428,34.08131), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.6428,34.08131,-85.42107,34.08081), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.4295,34.1251,-82.71537,34.14817), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.71537,34.14817,-85.4295,34.1251), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.73511,34.21261,-82.74498,34.24486), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.74498,34.24486,-82.73511,34.21261), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.46314,34.28619,-82.77463,34.28837), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.77463,34.28837,-85.46314,34.28619), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.78031,34.2967,-82.77463,34.28837), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.47515,34.34368,-82.82342,34.35887), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.82342,34.35887,-85.47515,34.34368), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.842,34.39977,-82.82342,34.35887), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.87383,34.47151,-82.99509,34.47248), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.99509,34.47248,-82.99139,34.47298), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.99139,34.47298,-82.99509,34.47248), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.50247,34.47453,-82.99139,34.47298), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-82.92577,34.4818,-85.50247,34.47453), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.04829,34.49325,-83.05057,34.49505), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.05057,34.49505,-83.04829,34.49325), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.51304,34.52395,-83.09686,34.53152), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.09686,34.53152,-83.10287,34.53743), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.10287,34.53743,-83.09686,34.53152), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.15458,34.5882,-85.52689,34.58869), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.52689,34.58869,-83.15458,34.5882), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.2214,34.60995,-85.53441,34.62379), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.53441,34.62379,-83.2214,34.60995), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.27796,34.64485,-85.53441,34.62379), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.33869,34.682,-83.34004,34.68633), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.34004,34.68633,-83.33869,34.682), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.34961,34.71701,-83.35324,34.72865), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.35324,34.72865,-83.34961,34.71701), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.56142,34.75008,-83.32006,34.75962), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.32006,34.75962,-85.56142,34.75008), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.32387,34.78971,-83.32006,34.75962), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.28481,34.82304,-83.25258,34.85348), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.25258,34.85348,-85.58281,34.86044), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.58281,34.86044,-83.25258,34.85348), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.20118,34.88465,-85.58281,34.86044), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.59517,34.92417,-83.14062,34.92492), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.14062,34.92492,-85.59517,34.92417), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.12438,34.95524,-85.38497,34.98299), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.38497,34.98299,-85.36392,34.98338), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.36392,34.98338,-85.47434,34.98367), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.47434,34.98367,-85.36392,34.98338), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.60517,34.98468,-85.27756,34.98498), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.60517,34.98468,-85.27756,34.98498), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.27756,34.98498,-85.26506,34.98508), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.26506,34.98508,-85.27756,34.98498), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.61999,34.98659,-85.04518,34.98688), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-85.04518,34.98688,-83.61999,34.98659), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.97985,34.98721,-84.97697,34.98722), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.97697,34.98722,-84.97985,34.98721), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.93641,34.98748,-83.93665,34.98749), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.93665,34.98749,-83.93641,34.98748), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.00551,34.98765,-84.86131,34.98779), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.86131,34.98779,-84.81048,34.98788), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.81048,34.98788,-84.77584,34.98794), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.77584,34.98794,-84.12944,34.98795), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.12944,34.98795,-84.77584,34.98794), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.72743,34.98802,-84.50905,34.98803), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.50905,34.98803,-84.72743,34.98802), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.62148,34.98833,-84.32187,34.98841), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-84.32187,34.98841,-84.62148,34.98833), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.54918,34.9888,-84.32187,34.98841), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.4827,34.99088,-83.54918,34.9888), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.32277,34.99587,-83.10861,35.00066), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.10861,35.00066,-83.32277,34.99587), mapfile, tile_dir, 0, 11, "georgia-ga")
	render_tiles((-83.10861,35.00066,-83.32277,34.99587), mapfile, tile_dir, 0, 11, "georgia-ga")