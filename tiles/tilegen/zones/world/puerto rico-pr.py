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
    # Region: Puerto Rico
    # Region Name: PR

	render_tiles((-65.28327,18.28021,-65.24126,18.30108), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.24126,18.30108,-65.33745,18.30831), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.33745,18.30831,-65.24126,18.30108), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.22157,18.32096,-65.33745,18.30831), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.25593,18.34212,-65.34207,18.34529), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.34207,18.34529,-65.25593,18.34212), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.34207,18.34529,-65.25593,18.34212), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.54209,18.08118,-65.45138,18.0861), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.45138,18.0861,-65.54209,18.08118), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.57686,18.10322,-65.29124,18.10347), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.57686,18.10322,-65.29124,18.10347), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.29124,18.10347,-65.57686,18.10322), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.37442,18.10804,-65.29124,18.10347), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.28796,18.1481,-65.50592,18.15261), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.50592,18.15261,-65.28796,18.1481), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.39817,18.16172,-65.50592,18.15261), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.24324,17.91377,-66.22053,17.91781), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.22053,17.91781,-66.24324,17.91377), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.92726,17.92688,-66.15539,17.92941), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.15539,17.92941,-67.18346,17.93114), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.18346,17.93114,-66.95558,17.93156), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.95558,17.93156,-67.18346,17.93114), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.0417,17.93494,-66.95558,17.93156), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.38506,17.939,-66.0417,17.93494), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.0681,17.9456,-66.83858,17.94993), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.83858,17.94993,-66.85791,17.95105), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.85791,17.95105,-66.85832,17.95107), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.85832,17.95107,-66.85791,17.95105), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.08983,17.95142,-67.10781,17.95162), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.10781,17.95162,-67.08983,17.95142), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.13373,17.95192,-67.10781,17.95162), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.88344,17.95253,-67.13373,17.95192), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.9787,17.95729,-66.09863,17.95794), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.09863,17.95794,-66.9787,17.95729), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.29768,17.95915,-66.09863,17.95794), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.98221,17.96119,-66.58323,17.96123), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.58323,17.96123,-66.98221,17.96119), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.66439,17.96826,-67.01474,17.96847), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.01474,17.96847,-66.66439,17.96826), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.98455,17.96941,-67.01474,17.96847), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.05446,17.97317,-66.01795,17.9749), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.01795,17.9749,-66.54054,17.97548), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.54054,17.97548,-66.024,17.9759), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.024,17.9759,-66.54054,17.97548), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.33814,17.97635,-66.33839,17.97646), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.33839,17.97646,-66.33814,17.97635), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.78495,17.97833,-66.44548,17.97938), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.44548,17.97938,-66.4533,17.98013), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.4533,17.98013,-66.64565,17.98026), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.64565,17.98026,-66.4533,17.98013), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.69703,17.98197,-65.91176,17.98338), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.91176,17.98338,-66.77536,17.98443), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.77536,17.98443,-65.91176,17.98338), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.51014,17.98562,-66.77536,17.98443), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.88494,17.98852,-66.71696,17.99034), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.71696,17.99034,-66.74625,17.99035), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.74625,17.99035,-66.71696,17.99034), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.21197,17.99299,-66.75847,17.99518), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.75847,17.99518,-67.21197,17.99299), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.85092,18.01197,-65.83314,18.02422), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.83314,18.02422,-67.20989,18.03544), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.20989,18.03544,-65.83314,18.02422), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.80917,18.05682,-65.80291,18.07119), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.80291,18.07119,-65.80917,18.05682), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.19931,18.09114,-65.80291,18.07119), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.77758,18.12924,-65.75873,18.1566), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.75873,18.1566,-65.73336,18.16577), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.73336,18.16577,-67.18082,18.16806), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.18082,18.16806,-67.18075,18.1682), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.18075,18.1682,-67.18082,18.16806), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.69586,18.17932,-67.18075,18.1682), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.65993,18.19157,-65.63528,18.19998), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.63528,18.19998,-65.65993,18.19157), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.59907,18.21296,-67.158,18.21672), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.158,18.21672,-65.59907,18.21296), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.58832,18.25426,-67.19122,18.26675), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.19122,18.26675,-65.58832,18.25426), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.61395,18.29382,-67.20996,18.29497), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.20996,18.29497,-65.61395,18.29382), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.22524,18.29798,-67.23514,18.29994), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.23514,18.29994,-67.22524,18.29798), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.62487,18.31067,-67.23514,18.29994), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.56493,18.32504,-65.62487,18.31067), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.56595,18.35833,-67.27135,18.36233), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.27135,18.36233,-65.56595,18.35833), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.23913,18.37383,-67.22674,18.37825), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.23913,18.37383,-67.22674,18.37825), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.22674,18.37825,-67.23913,18.37383), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.62402,18.38717,-65.66135,18.38904), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.66135,18.38904,-65.62402,18.38717), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.71862,18.39191,-65.58623,18.39338), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.58623,18.39338,-65.71862,18.39191), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.7418,18.39818,-65.58623,18.39338), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.7717,18.40628,-65.7418,18.39818), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.16017,18.4156,-67.15961,18.41592), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.15961,18.41592,-67.16017,18.4156), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.82774,18.42556,-65.83148,18.42685), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.83148,18.42685,-65.82774,18.42556), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.90499,18.45093,-66.03944,18.45444), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.03944,18.45444,-66.03432,18.45507), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.03432,18.45507,-66.03944,18.45444), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.9928,18.46017,-65.99079,18.46042), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-65.99079,18.46042,-65.9928,18.46017), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.16901,18.46635,-66.47029,18.46907), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.47029,18.46907,-66.18672,18.46973), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.18672,18.46973,-66.47029,18.46907), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.19588,18.47065,-66.18672,18.46973), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.12926,18.47217,-66.73399,18.47346), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.73399,18.47346,-66.13796,18.47389), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.13796,18.47389,-66.73399,18.47346), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.31548,18.47472,-66.31502,18.47474), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.31502,18.47474,-66.31548,18.47472), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.25802,18.47691,-66.31502,18.47474), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.3495,18.47921,-66.53426,18.47949), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.53426,18.47949,-66.3495,18.47921), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.43895,18.48149,-66.76557,18.4828), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.76557,18.4828,-66.43895,18.48149), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.92409,18.48727,-66.58625,18.48795), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.58625,18.48795,-66.90157,18.48826), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.90157,18.48826,-66.58625,18.48795), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.42092,18.48864,-66.90157,18.48826), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.83659,18.49113,-66.79932,18.49278), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.79932,18.49278,-66.95632,18.4939), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.95632,18.4939,-66.62462,18.4942), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-66.62462,18.4942,-66.95632,18.4939), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.04228,18.51159,-67.0973,18.51167), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.0973,18.51167,-67.12566,18.51171), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.12566,18.51171,-67.0973,18.51167), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.88555,18.03647,-67.85063,18.04627), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.85063,18.04627,-67.88555,18.03647), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.95581,18.07423,-67.82092,18.08471), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.82092,18.08471,-67.95581,18.07423), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.94122,18.12693,-67.84622,18.12758), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.94122,18.12693,-67.84622,18.12758), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.84622,18.12758,-67.94122,18.12693), mapfile, tile_dir, 0, 11, "puerto rico-pr")
	render_tiles((-67.89629,18.1368,-67.84622,18.12758), mapfile, tile_dir, 0, 11, "puerto rico-pr")