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
    # Region: Utah
    # Region Name: UT

	render_tiles((-110.00068,36.99797,-110.47019,36.998), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.47019,36.998,-110.00068,36.99797), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04522,36.99908,-109.49534,36.99911), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.49534,36.99911,-109.04522,36.99908), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.96591,37.00003,-112.96647,37.00022), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.96647,37.00022,-112.89919,37.0003), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.89919,37.0003,-112.96647,37.00022), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.8295,37.00039,-114.0506,37.0004), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.0506,37.0004,-112.8295,37.00039), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.27829,37.00047,-114.0506,37.0004), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.54509,37.00073,-112.53857,37.00074), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.53857,37.00074,-112.54509,37.00073), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.35769,37.00103,-112.53857,37.00074), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.41278,37.00148,-112.35769,37.00103), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.40587,37.00148,-112.35769,37.00103), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.0665,37.00239,-110.75069,37.0032), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.75069,37.0032,-111.0665,37.00239), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05175,37.08843,-110.75069,37.0032), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05197,37.28451,-114.05175,37.08843), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04378,37.48468,-114.0527,37.49201), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.0527,37.49201,-109.04378,37.48468), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05247,37.60478,-114.0527,37.49201), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05247,37.60478,-114.0527,37.49201), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05247,37.60478,-114.0527,37.49201), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05173,37.746,-109.0426,37.88117), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.0426,37.88117,-114.04966,37.88137), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04966,37.88137,-109.0426,37.88117), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.0499,38.1486,-109.0418,38.15302), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.0418,38.15302,-114.0499,38.1486), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04176,38.16469,-109.0418,38.15302), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05014,38.24996,-109.06006,38.27549), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.06006,38.27549,-114.05014,38.24996), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05012,38.40454,-109.05996,38.49999), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05996,38.49999,-114.05015,38.57292), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05996,38.49999,-114.05015,38.57292), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.05015,38.57292,-109.05996,38.49999), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04944,38.67736,-114.05015,38.57292), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04805,38.87869,-114.0491,39.00551), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.0491,39.00551,-109.05151,39.1261), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05151,39.1261,-114.0491,39.00551), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05122,39.36668,-109.05107,39.49774), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05107,39.49774,-114.04708,39.49994), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04708,39.49994,-109.05107,39.49774), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04718,39.54274,-114.04708,39.49994), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05087,39.66047,-114.04718,39.54274), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04778,39.79416,-109.05062,39.87497), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05062,39.87497,-114.04727,39.90604), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04727,39.90604,-109.05062,39.87497), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04639,40.0979,-114.04637,40.11693), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04637,40.11693,-114.04639,40.0979), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05097,40.18085,-109.05073,40.22266), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05073,40.22266,-109.05097,40.18085), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04618,40.39831,-114.04558,40.4958), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04558,40.4958,-114.04618,40.39831), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04825,40.6536,-109.04826,40.6626), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04826,40.6626,-109.04825,40.6536), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04351,40.72629,-109.04826,40.6626), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.04846,40.82608,-114.04351,40.72629), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.53982,40.99635,-110.12164,40.9971), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.12164,40.9971,-110.04848,40.9973), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.04848,40.9973,-110.00072,40.99743), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-110.00072,40.99743,-110.04848,40.9973), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.04672,40.99796,-109.71541,40.99819), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.71541,40.99819,-111.04672,40.99796), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04215,40.99993,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.05008,41.00066,-109.25074,41.00101), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-109.25074,41.00101,-109.05008,41.00066), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04145,41.20775,-111.04664,41.25163), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.04664,41.25163,-114.04145,41.20775), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.0466,41.36069,-111.04664,41.25163), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04023,41.49169,-111.04579,41.56557), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.04579,41.56557,-111.04582,41.57984), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.04582,41.57984,-111.04579,41.56557), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.0399,41.75378,-111.04582,41.57984), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.81796,41.98858,-113.49655,41.99331), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.49655,41.99331,-114.04172,41.99372), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-114.04172,41.99372,-113.49655,41.99331), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.24916,41.9962,-112.10953,41.9976), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.10953,41.9976,-113.00082,41.99822), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.10951,41.9976,-113.00082,41.99822), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.00082,41.99822,-112.16464,41.9988), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-113.00082,41.99822,-112.16464,41.9988), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.16464,41.9988,-111.75078,41.99933), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.75078,41.99933,-111.50781,41.99969), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.50781,41.99969,-111.47138,41.99974), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.50785,41.99969,-111.47138,41.99974), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.47138,41.99974,-111.50781,41.99969), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.64802,42.00031,-111.47138,41.99974), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.37413,42.00089,-112.26494,42.00099), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-112.26494,42.00099,-111.37413,42.00089), mapfile, tile_dir, 0, 11, "utah-ut")
	render_tiles((-111.04669,42.00157,-112.26494,42.00099), mapfile, tile_dir, 0, 11, "utah-ut")