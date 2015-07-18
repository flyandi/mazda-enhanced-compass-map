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
    # Region: Pennsylvania
    # Region Name: PA

	render_tiles((-76.99106,39.72006,-76.99932,39.72007), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.99932,39.72007,-76.99106,39.72006), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.21702,39.72022,-77.46915,39.72023), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.46915,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.46927,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.45943,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.23995,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.76377,39.72078,-76.7871,39.72105), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.7871,39.72105,-79.91602,39.72106), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.91602,39.72106,-76.7871,39.72105), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.47666,39.72108,-79.91602,39.72106), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.42139,39.72119,-80.0417,39.72129), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.0417,39.72129,-80.07595,39.72135), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.07595,39.72135,-76.71577,39.72139), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.71577,39.72139,-80.51934,39.7214), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51934,39.7214,-76.71577,39.72139), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.39246,39.72144,-76.56948,39.72146), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.56948,39.72146,-79.39246,39.72144), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.41898,39.72153,-77.76864,39.72154), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.76864,39.72154,-76.41898,39.72153), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.23968,39.72164,-76.23349,39.72165), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.23349,39.72165,-76.23968,39.72164), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.23328,39.72165,-76.23968,39.72164), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.1357,39.72177,-76.23349,39.72165), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.81208,39.72217,-75.77379,39.7222), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.77379,39.7222,-75.81208,39.72217), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.7886,39.7222,-75.81208,39.72217), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.07586,39.72245,-78.09897,39.72247), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.09897,39.72247,-78.07586,39.72245), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.34259,39.72266,-78.38048,39.7227), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.34283,39.72266,-78.38048,39.7227), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.38048,39.7227,-78.34259,39.72266), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.04558,39.72293,-78.93118,39.723), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.93118,39.723,-79.04558,39.72293), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.8083,39.72307,-78.72358,39.72312), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.72358,39.72312,-78.8083,39.72307), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.75323,39.75799,-75.71706,39.79233), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.71706,39.79233,-75.41506,39.80192), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.41506,39.80192,-75.71706,39.79233), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.39032,39.81683,-75.66285,39.82143), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.66285,39.82143,-75.39032,39.81683), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.48121,39.82919,-75.59432,39.83459), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.59432,39.83459,-75.57043,39.83919), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.57043,39.83919,-75.35165,39.84013), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.35165,39.84013,-75.57043,39.83919), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.34177,39.84608,-75.29338,39.84878), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.29338,39.84878,-75.34177,39.84608), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.25881,39.85467,-75.29338,39.84878), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.22103,39.86111,-75.2112,39.86652), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.2112,39.86652,-75.22103,39.86111), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.18302,39.88201,-75.14144,39.89392), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.14144,39.89392,-75.13342,39.89621), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.13342,39.89621,-75.14144,39.89392), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.13572,39.94711,-80.51916,39.9622), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51916,39.9622,-75.11922,39.96541), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.11922,39.96541,-80.51916,39.9622), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.06013,39.99201,-75.05902,39.99251), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.05902,39.99251,-75.06013,39.99201), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51912,40.01641,-74.98991,40.03731), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.98991,40.03731,-74.97285,40.04651), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.97285,40.04651,-74.98991,40.03731), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.93221,40.06841,-74.86381,40.08221), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.86381,40.08221,-74.93221,40.06841), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.82591,40.12391,-74.76949,40.12915), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.76949,40.12915,-74.82591,40.12391), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.72628,40.1514,-74.7216,40.15381), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.7216,40.15381,-74.72628,40.1514), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51908,40.15967,-74.7216,40.15381), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.74836,40.18474,-74.76061,40.19891), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.76061,40.19891,-74.74836,40.18474), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.82391,40.24151,-74.85651,40.27741), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.85651,40.27741,-74.82391,40.24151), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.90331,40.31561,-74.92811,40.33983), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.92811,40.33983,-80.51904,40.3421), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51904,40.3421,-74.92811,40.33983), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.94601,40.35731,-80.51904,40.3421), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51903,40.39964,-74.9696,40.39977), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.9696,40.39977,-80.51903,40.39964), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.02478,40.40346,-74.9696,40.39977), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.0561,40.41607,-75.02478,40.40346), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.07057,40.45517,-80.51902,40.47736), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51902,40.47736,-75.06223,40.48139), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.06223,40.48139,-80.51902,40.47736), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.0785,40.5483,-75.18674,40.56941), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.18674,40.56941,-75.13675,40.57573), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.13675,40.57573,-75.18674,40.56941), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.1882,40.59258,-75.18924,40.60906), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.18924,40.60906,-75.1882,40.59258), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.19106,40.63797,-80.51899,40.6388), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51899,40.6388,-75.19106,40.63797), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.19184,40.67724,-80.51899,40.6388), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.19261,40.71587,-75.19184,40.67724), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.17748,40.76423,-75.1107,40.79024), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.1107,40.79024,-75.10851,40.79109), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.10851,40.79109,-75.1107,40.79024), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.09096,40.84919,-80.51971,40.85134), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51971,40.85134,-75.09096,40.84919), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.06544,40.88568,-80.51987,40.90032), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51987,40.90032,-80.51988,40.90245), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51988,40.90245,-80.51987,40.90032), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51989,40.90666,-80.51988,40.90245), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51989,40.90666,-80.51988,40.90245), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.09772,40.92668,-80.51989,40.90666), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.12325,40.96531,-75.13309,40.98018), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.13309,40.98018,-80.51964,40.98739), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51964,40.98739,-75.13309,40.98018), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.07053,41.01862,-80.51964,40.98739), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.01527,41.06122,-74.99239,41.09303), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.99239,41.09303,-74.983,41.10608), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.983,41.10608,-74.97987,41.11042), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.97987,41.11042,-74.983,41.10608), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51922,41.12509,-80.5192,41.13339), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.5192,41.13339,-80.51922,41.12509), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.90526,41.15567,-80.5192,41.13339), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.86741,41.22777,-80.51889,41.23256), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51889,41.23256,-74.86741,41.22777), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.8157,41.29615,-80.519,41.3335), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.519,41.3335,-74.76033,41.34033), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.76033,41.34033,-80.519,41.3335), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.69491,41.35742,-74.76033,41.34033), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.73489,41.42582,-74.75627,41.42763), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.75627,41.42763,-74.73489,41.42582), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.79955,41.43129,-74.75627,41.42763), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.89036,41.45532,-74.79955,41.43129), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51917,41.48901,-74.98246,41.49647), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-74.98246,41.49647,-80.51918,41.49992), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51918,41.49992,-74.98246,41.49647), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.04388,41.57509,-75.0462,41.60376), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.0462,41.60376,-75.04388,41.57509), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.04928,41.64186,-80.51936,41.66977), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51936,41.66977,-75.04928,41.64186), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.05343,41.75254,-75.07441,41.80219), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.07441,41.80219,-75.11337,41.8407), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.11337,41.8407,-80.5194,41.84956), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.5194,41.84956,-75.14666,41.85013), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.14666,41.85013,-80.5194,41.84956), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.1902,41.86245,-75.14666,41.85013), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.26301,41.88511,-75.1902,41.86245), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.29176,41.94709,-80.51943,41.97752), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.51943,41.97752,-75.34113,41.99277), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.34113,41.99277,-75.35986,41.99369), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.35986,41.99369,-75.34113,41.99277), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.47247,41.99826,-79.61084,41.99852), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.61084,41.99852,-77.74993,41.99876), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.83203,41.99852,-77.74993,41.99876), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.74993,41.99876,-79.76131,41.99881), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.76131,41.99881,-75.87068,41.99883), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.87068,41.99883,-79.06126,41.99884), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.06126,41.99884,-75.87068,41.99883), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.10584,41.99886,-76.14552,41.99887), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.14552,41.99887,-76.10584,41.99886), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.46216,41.99893,-78.98307,41.99895), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.98307,41.99895,-76.46216,41.99893), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.2712,41.99897,-78.98307,41.99895), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.30813,41.99907,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.47303,41.99907,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.2066,41.99909,-78.91886,41.9991), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.91886,41.9991,-78.2066,41.99909), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.61002,41.99915,-78.91886,41.9991), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.55311,41.9993,-75.48315,41.9994), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.48315,41.9994,-75.47714,41.99941), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-75.47714,41.99941,-75.48315,41.9994), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.03118,41.99942,-75.47714,41.99941), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-78.59665,41.99988,-76.55762,42.00015), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.55762,42.00015,-76.55812,42.00016), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.55812,42.00016,-76.55762,42.00015), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.92685,42.00072,-76.96573,42.00078), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-76.96573,42.00078,-76.92685,42.00072), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-77.00764,42.00085,-76.96573,42.00078), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.32998,42.03617,-77.00764,42.00085), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.15408,42.11476,-79.76212,42.13125), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.76212,42.13125,-80.15408,42.11476), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.13621,42.14994,-80.02032,42.16312), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.02032,42.16312,-80.02116,42.16324), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.02116,42.16324,-80.02032,42.16312), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-80.08851,42.17318,-80.02116,42.16324), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.92392,42.20755,-79.84466,42.23549), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.84466,42.23549,-79.92392,42.20755), mapfile, tile_dir, 0, 11, "pennsylvania-pa")
	render_tiles((-79.76195,42.26986,-79.84466,42.23549), mapfile, tile_dir, 0, 11, "pennsylvania-pa")