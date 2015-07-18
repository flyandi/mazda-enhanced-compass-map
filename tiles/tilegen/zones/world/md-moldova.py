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
    # Region: MD
    # Region Name: Moldova

	render_tiles((28.21202,45.4482,28.2381,45.5033), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.2381,45.5033,28.4775,45.5064), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4775,45.5064,28.2381,45.5033), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5228,45.5178,28.2839,45.5253), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.2839,45.5253,28.5228,45.5178), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.165,45.5336,28.2839,45.5253), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5303,45.5436,28.165,45.5336), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5114,45.5692,28.5561,45.5867), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5561,45.5867,28.06194,45.58998), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.06194,45.58998,28.5561,45.5867), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5336,45.6072,28.06194,45.58998), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.16138,45.63943,28.5283,45.6447), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5283,45.6447,28.16138,45.63943), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4894,45.665,28.5283,45.6447), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5269,45.7142,28.5825,45.7197), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5825,45.7197,28.5269,45.7142), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5836,45.7631,28.6753,45.7753), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.6753,45.7753,28.5836,45.7631), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.6947,45.7944,28.6753,45.7753), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.6889,45.8189,28.10749,45.83582), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.10749,45.83582,28.7547,45.8386), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.7547,45.8386,28.10749,45.83582), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.7689,45.8669,28.12972,45.86749), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.12972,45.86749,28.7689,45.8669), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.7478,45.9292,28.7558,45.96), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.7558,45.96,28.7478,45.9292), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9719,46.0067,28.08444,46.01166), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.08444,46.01166,28.9719,46.0067), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9806,46.0297,28.08444,46.01166), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9572,46.1056,28.13555,46.16915), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.13555,46.16915,29.0411,46.1939), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.0411,46.1939,28.13555,46.16915), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9494,46.2708,28.965,46.3119), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.965,46.3119,29.8483,46.3483), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8483,46.3483,29.8847,46.3511), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8847,46.3511,29.8483,46.3483), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8272,46.3617,29.2242,46.3653), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2242,46.3653,29.8272,46.3617), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.6267,46.3692,29.2242,46.3653), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2008,46.3814,30.1039,46.3819), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((30.1039,46.3819,29.2008,46.3814), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5908,46.3833,29.93,46.3836), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.93,46.3836,29.5908,46.3833), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8303,46.3881,29.6728,46.3914), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.6728,46.3914,29.8303,46.3881), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2639,46.3986,29.3067,46.4022), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3067,46.4022,29.2639,46.3986), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((30.1342,46.4156,29.3931,46.4161), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3931,46.4161,30.1342,46.4156), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2264,46.4169,29.3931,46.4161), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5761,46.42,30.0731,46.4228), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((30.0731,46.4228,29.5761,46.42), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8017,46.4256,28.9322,46.4283), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9322,46.4283,29.6633,46.4286), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.6633,46.4286,28.9322,46.4283), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.4997,46.4322,29.6633,46.4286), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3961,46.4381,29.7331,46.4394), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.7331,46.4394,28.25194,46.43942), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.25194,46.43942,29.7331,46.4394), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3008,46.445,28.25194,46.43942), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((30.0069,46.4567,29.3008,46.445), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((30.0186,46.4697,29.3475,46.4764), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3475,46.4764,29.7333,46.4781), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3239,46.4764,29.7333,46.4781), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.7333,46.4781,29.3475,46.4764), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5075,46.4811,29.7333,46.4781), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2283,46.4847,29.9706,46.4881), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9706,46.4881,29.2283,46.4847), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.4836,46.4922,29.0347,46.4953), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.0347,46.4953,29.9989,46.4961), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9989,46.4961,29.0347,46.4953), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.20943,46.50249,29.9286,46.5053), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9286,46.5053,28.20943,46.50249), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8986,46.53,29.2194,46.5328), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2194,46.5328,29.8986,46.53), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.205,46.5414,29.2194,46.5328), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8958,46.5511,29.205,46.5414), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9689,46.5753,29.8958,46.5511), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.24721,46.60832,29.9483,46.6369), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9483,46.6369,28.24721,46.60832), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.21388,46.68443,29.9483,46.6369), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9728,46.7344,28.21388,46.68443), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9464,46.7892,29.95,46.8142), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.95,46.8142,29.9464,46.7892), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.9089,46.8142,29.9464,46.7892), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.8689,46.8614,29.7614,46.8664), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.7614,46.8664,29.8689,46.8614), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.7153,46.9117,29.5756,46.9431), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5756,46.9431,29.7153,46.9117), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.07111,46.9911,29.5933,47.0253), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5933,47.0253,27.99249,47.02859), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.99249,47.02859,29.5933,47.0253), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.6236,47.0461,27.99249,47.02859), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5394,47.0669,29.6136,47.0844), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.6136,47.0844,29.5697,47.0986), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5697,47.0986,29.6136,47.0844), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.4969,47.1183,29.5697,47.0986), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5714,47.1408,29.4969,47.1183), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.80694,47.1636,29.5714,47.1408), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5567,47.2331,29.5992,47.2633), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5992,47.2633,29.475,47.2922), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.475,47.2922,29.4056,47.295), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.4056,47.295,29.475,47.2922), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.585,47.3197,29.3794,47.3219), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3794,47.3219,29.585,47.3197), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5594,47.3428,29.5058,47.3464), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.5058,47.3464,29.5594,47.3428), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3803,47.3578,29.5058,47.3464), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.57222,47.37109,29.3228,47.3786), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3228,47.3786,29.3544,47.38), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3544,47.38,29.3228,47.3786), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.3078,47.4144,29.2625,47.4319), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2625,47.4319,29.1867,47.44), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1867,47.44,29.2625,47.4319), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.57944,47.4547,29.2525,47.4553), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2525,47.4553,27.57944,47.4547), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.46971,47.48859,29.2525,47.4553), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.46638,47.52387,29.1314,47.5442), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1314,47.5442,27.46638,47.52387), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1667,47.5867,29.1314,47.5442), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2056,47.6322,27.32194,47.64165), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.32194,47.64165,29.2056,47.6322), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2081,47.7181,27.28944,47.72359), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.28944,47.72359,29.2081,47.7181), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2581,47.7644,29.2492,47.78), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2492,47.78,27.23833,47.78526), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.23833,47.78526,29.2492,47.78), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.195,47.7931,27.23833,47.78526), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1942,47.8089,29.195,47.7931), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.2575,47.8747,29.1936,47.895), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1936,47.895,29.2575,47.8747), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.0036,47.9331,29.0508,47.9394), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.0508,47.9394,29.0036,47.9331), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.9561,47.95,29.1781,47.9519), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1781,47.9519,28.9561,47.95), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.16444,47.96776,29.0806,47.9828), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.0806,47.9828,29.1419,47.9861), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((29.1419,47.9861,29.0806,47.9828), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8783,48.01,28.8517,48.0319), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8517,48.0319,28.8783,48.01), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8539,48.0556,28.4608,48.0714), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4608,48.0714,28.8275,48.0789), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8275,48.0789,28.4608,48.0714), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8331,48.1017,28.4775,48.1183), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4775,48.1183,28.4217,48.1186), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4217,48.1186,28.4775,48.1183), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.8156,48.1214,28.4217,48.1186), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.3456,48.1269,28.8156,48.1214), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4381,48.1383,28.3128,48.1394), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.3128,48.1394,28.4381,48.1383), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.6425,48.1536,27.00055,48.15554), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.00055,48.15554,28.6425,48.1536), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.5514,48.1606,28.3886,48.1639), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.3886,48.1639,28.5514,48.1606), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.4186,48.1694,28.3886,48.1639), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.3706,48.1961,26.89444,48.2047), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.89444,48.2047,28.2225,48.2053), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.2225,48.2053,26.89444,48.2047), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.1872,48.2156,28.2225,48.2053), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.0944,48.2294,28.1281,48.2317), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.1281,48.2317,28.0944,48.2294), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.295,48.2381,26.635,48.24087), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.635,48.24087,28.3567,48.2411), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.635,48.24087,28.3567,48.2411), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.3567,48.2411,26.635,48.24087), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.0819,48.2433,28.3567,48.2411), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.1819,48.2497,26.70777,48.25332), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.70777,48.25332,28.1819,48.2497), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.1656,48.2575,26.70777,48.25332), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.0967,48.2886,26.8033,48.2972), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.8033,48.2972,26.6489,48.2981), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.6489,48.2981,26.8033,48.2972), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.7506,48.2994,26.6489,48.2981), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((28.0856,48.3075,26.6883,48.3117), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.6883,48.3117,28.0856,48.3075), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.8108,48.3219,27.9631,48.325), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.9631,48.325,26.8108,48.3219), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.7031,48.3447,27.90411,48.35427), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.90411,48.35427,26.9689,48.3586), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.9689,48.3586,27.90411,48.35427), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.0494,48.365,27.2339,48.3678), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.2339,48.3678,27.0494,48.365), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.8358,48.3789,26.7914,48.3817), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.2703,48.3789,26.7914,48.3817), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.7914,48.3817,26.8358,48.3789), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.87,48.3983,26.8539,48.405), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((26.8539,48.405,27.0536,48.4069), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.0536,48.4069,26.8539,48.405), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.4189,48.4167,27.0536,48.4069), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.3922,48.4389,27.6533,48.4417), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.6533,48.4417,27.3431,48.4428), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.3431,48.4428,27.6533,48.4417), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.7708,48.4478,27.3431,48.4428), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.6072,48.4544,27.7708,48.4478), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.5208,48.465,27.6072,48.4544), mapfile, tile_dir, 0, 11, "md-moldova")
	render_tiles((27.5997,48.4867,27.5208,48.465), mapfile, tile_dir, 0, 11, "md-moldova")