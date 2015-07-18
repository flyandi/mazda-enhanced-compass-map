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
    # Region: Wyoming
    # Region Name: WY

	render_tiles((-110.53982,40.99635,-110.12164,40.9971), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.12164,40.9971,-110.04848,40.9973), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.04848,40.9973,-110.00072,40.99743), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.00072,40.99743,-110.04848,40.9973), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.21757,40.99773,-106.19055,40.99775), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.19055,40.99775,-106.21757,40.99773), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04672,40.99796,-104.85527,40.99805), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.85527,40.99805,-104.94337,40.99807), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.94337,40.99807,-104.85527,40.99805), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.27686,40.99817,-109.71541,40.99819), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.27714,40.99817,-109.71541,40.99819), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.71541,40.99819,-105.27686,40.99817), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.32117,40.99822,-109.71541,40.99819), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-108.25065,41.00011,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.05008,41.00066,-106.86038,41.00072), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.86038,41.00072,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.25074,41.00101,-107.91842,41.00123), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.91842,41.00123,-104.05325,41.00141), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05325,41.00141,-107.91842,41.00123), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.49706,41.00181,-104.05325,41.00141), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.3178,41.00284,-107.36744,41.00307), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.36744,41.00307,-107.3178,41.00284), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05314,41.11446,-107.36744,41.00307), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04664,41.25163,-104.05245,41.2782), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05245,41.2782,-111.04664,41.25163), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.0466,41.36069,-104.05229,41.39321), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05229,41.39321,-104.05229,41.39331), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05229,41.39331,-104.05229,41.39321), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05263,41.56428,-111.04579,41.56557), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04579,41.56557,-104.05263,41.56428), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04582,41.57984,-111.04579,41.56557), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05274,41.61368,-111.04582,41.57984), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05283,41.69795,-104.05274,41.61368), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05303,41.88546,-111.04669,42.00157), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04669,42.00157,-104.05276,42.00172), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05276,42.00172,-111.04669,42.00157), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05273,42.01632,-104.05276,42.00172), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05279,42.24996,-111.04708,42.34942), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04708,42.34942,-104.05279,42.24996), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05311,42.49996,-111.04554,42.51311), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04554,42.51311,-104.05311,42.49996), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05266,42.61177,-104.05259,42.63092), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05259,42.63092,-104.05266,42.61177), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04356,42.72262,-104.05259,42.63092), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04396,42.96445,-104.05313,43.00059), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05313,43.00059,-111.04405,43.02005), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04405,43.02005,-104.05313,43.00059), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04414,43.07236,-111.04405,43.02005), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05388,43.2898,-111.04462,43.31572), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04462,43.31572,-104.05388,43.2898), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05468,43.47782,-111.04536,43.50114), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04536,43.50114,-104.05479,43.50333), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05479,43.50333,-111.04536,43.50114), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05503,43.5586,-104.05479,43.50333), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04611,43.68785,-104.05503,43.5586), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05549,43.85348,-111.04652,43.90838), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05549,43.85348,-111.04652,43.90838), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04652,43.90838,-104.05549,43.85348), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04722,43.98345,-111.04652,43.90838), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04845,44.11483,-104.05542,44.14108), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05542,44.14108,-111.04845,44.11483), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05541,44.18038,-104.05542,44.14108), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05539,44.24998,-104.05541,44.18038), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04915,44.37493,-111.04897,44.47407), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04897,44.47407,-104.0557,44.57099), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.0557,44.57099,-111.05521,44.62493), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.05521,44.62493,-111.05533,44.66626), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.05533,44.66626,-104.05581,44.69134), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.05581,44.69134,-111.05533,44.66626), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.05551,44.72534,-104.05581,44.69134), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.05689,44.86666,-110.70527,44.99232), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.05689,44.86666,-110.70527,44.99232), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.70527,44.99232,-106.26359,44.99379), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.26359,44.99379,-110.70527,44.99232), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.88877,44.99589,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-104.0577,44.99743,-106.02488,44.99758), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-106.02488,44.99758,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.32444,44.99916,-109.06226,44.99962), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.06226,44.99962,-108.62149,44.99968), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-108.62149,44.99968,-108.50068,44.99969), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-108.50068,44.99969,-108.62149,44.99968), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.03841,45.00029,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.02527,45.00029,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.07661,45.0003,-105.03841,45.00029), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-105.84807,45.0004,-105.07661,45.0003), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-108.24853,45.00063,-105.84807,45.0004), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-111.04428,45.00135,-107.35144,45.00141), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.35144,45.00141,-111.04428,45.00135), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.91175,45.00154,-107.99735,45.00157), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-107.99735,45.00157,-107.91175,45.00154), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.57432,45.00263,-109.79869,45.00292), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.79869,45.00292,-110.78501,45.00295), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-110.78501,45.00295,-109.79869,45.00292), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.99505,45.00317,-110.78501,45.00295), mapfile, tile_dir, 0, 11, "wyoming-wy")
	render_tiles((-109.10345,45.0059,-109.99505,45.00317), mapfile, tile_dir, 0, 11, "wyoming-wy")