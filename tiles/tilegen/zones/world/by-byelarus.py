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
    # Region: BY
    # Region Name: Byelarus

	render_tiles((30.5494,51.2528,30.4797,51.27), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.4797,51.27,30.5717,51.2733), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5717,51.2733,30.4797,51.27), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.46,51.2969,30.5708,51.3019), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5708,51.3019,30.46,51.2969), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.3839,51.3072,30.5708,51.3019), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6442,51.3328,30.335,51.345), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.335,51.345,30.6319,51.3553), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6319,51.3553,30.3503,51.3583), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.3503,51.3583,30.6319,51.3553), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6436,51.3708,29.3539,51.375), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.3539,51.375,30.6436,51.3708), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.3131,51.385,29.4831,51.3903), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4831,51.3903,29.3131,51.385), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4097,51.4028,30.3481,51.4053), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.3481,51.4053,29.4097,51.4028), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.7525,51.4111,30.6197,51.4161), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6197,51.4161,28.765,51.4169), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.765,51.4169,30.6197,51.4161), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.7303,51.4208,28.765,51.4169), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5817,51.4303,29.8514,51.4361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.8514,51.4361,28.6836,51.4386), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.6836,51.4386,29.8514,51.4361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.7592,51.4411,29.2819,51.4433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.2819,51.4433,29.7592,51.4411), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.6606,51.4519,28.7192,51.4522), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.7192,51.4522,28.6606,51.4519), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.5944,51.4531,29.5239,51.4536), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.5239,51.4536,29.5944,51.4531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5886,51.4542,29.5239,51.4536), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.9339,51.4572,30.6156,51.4583), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6156,51.4583,29.9339,51.4572), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.7469,51.4656,29.2361,51.4664), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.2361,51.4664,27.7469,51.4656), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.8958,51.4694,29.2361,51.4664), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.7661,51.4847,29.9833,51.4864), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.9833,51.4864,28.7661,51.4847), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.6217,51.49,30.1764,51.4919), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.1764,51.4919,29.6217,51.49), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.7178,51.4944,30.1764,51.4919), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.6697,51.4989,29.7178,51.4944), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.2508,51.5047,27.6711,51.5067), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.6711,51.5067,29.2508,51.5047), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5658,51.5158,23.6461,51.5208), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.6461,51.5208,30.5658,51.5158), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.59208,51.52828,27.8175,51.5306), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.8175,51.5306,23.59208,51.52828), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.3592,51.5306,23.59208,51.52828), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5822,51.5367,29.2433,51.5378), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.2433,51.5378,30.5822,51.5367), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5303,51.5511,28.8453,51.5542), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.8453,51.5542,30.5303,51.5511), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.7247,51.5644,28.0881,51.565), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.0881,51.565,27.7247,51.5644), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.6272,51.5664,30.5222,51.5669), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5222,51.5669,28.6272,51.5664), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.9753,51.5678,29.205,51.5683), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.205,51.5683,27.9753,51.5678), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.9953,51.5747,28.9289,51.5767), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.9289,51.5767,28.3361,51.5769), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.3361,51.5769,28.9289,51.5767), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5428,51.5772,28.3361,51.5769), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.4756,51.5797,28.135,51.5803), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.135,51.5803,28.4756,51.5797), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.9539,51.585,27.8117,51.5886), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.8117,51.5886,23.9539,51.585), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.7197,51.5925,23.53917,51.59276), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.53917,51.59276,27.7197,51.5925), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.0283,51.5958,30.5175,51.5978), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5175,51.5978,29.0283,51.5958), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.2794,51.5978,29.0283,51.5958), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.4456,51.6011,30.5175,51.5978), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.6086,51.6081,24.0431,51.6103), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.0431,51.6103,29.1861,51.6122), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.1861,51.6122,24.0431,51.6103), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.2608,51.6178,27.8847,51.62), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.8847,51.62,27.8514,51.6203), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.8514,51.6203,27.8847,51.62), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.04,51.6247,30.5567,51.625), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5567,51.625,29.04,51.6247), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.4717,51.6294,27.5125,51.6303), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.5125,51.6303,27.4717,51.6294), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.8967,51.6358,29.0833,51.6383), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.0833,51.6383,23.6667,51.6406), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.6667,51.6406,29.0833,51.6383), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.1886,51.6431,23.6667,51.6406), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.7536,51.6525,27.2989,51.6544), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.2989,51.6544,23.7536,51.6525), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.2586,51.6611,27.2989,51.6544), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.2,51.6703,28.2586,51.6611), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.5739,51.6975,30.6283,51.7114), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6283,51.7114,30.5739,51.6975), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.2794,51.7378,27.2067,51.7431), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.2067,51.7431,23.55389,51.74582), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.55389,51.74582,27.2067,51.7431), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6456,51.7486,26.9333,51.7497), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.9333,51.7497,30.6456,51.7486), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.1797,51.7628,30.6175,51.7642), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6175,51.7642,27.1797,51.7628), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.0292,51.7694,30.6569,51.7744), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6569,51.7744,27.0292,51.7694), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.67,51.7964,23.62638,51.79694), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.62638,51.79694,30.67,51.7964), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4572,51.8128,26.6497,51.8203), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6497,51.8203,26.4392,51.8206), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4392,51.8206,26.6497,51.8203), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.3133,51.8225,30.6622,51.8239), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6622,51.8239,24.3133,51.8225), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4369,51.8569,26.1808,51.8614), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.1808,51.8614,26.4369,51.8569), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.3967,51.8867,30.7469,51.8953), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.7469,51.8953,24.8853,51.8994), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.8853,51.8994,30.7964,51.9028), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.7964,51.9028,24.8853,51.8994), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.9767,51.9122,23.61166,51.91415), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.61166,51.91415,25.9767,51.9122), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.0922,51.9175,23.61166,51.91415), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6958,51.9228,25.54698,51.92324), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.54698,51.92324,25.6958,51.9228), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.3611,51.9269,25.54698,51.92324), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7758,51.9392,30.8008,51.9444), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8008,51.9444,25.7758,51.9392), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.2442,51.9594,25.1794,51.9597), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.1794,51.9597,25.2442,51.9594), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8822,51.9656,25.1794,51.9597), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8978,51.9978,30.9717,52.0064), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9717,52.0064,30.8978,51.9978), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9669,52.025,30.9314,52.0333), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9314,52.0333,30.9669,52.025), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.65638,52.04166,31.2517,52.0456), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.2517,52.0456,30.9439,52.0492), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9439,52.0492,31.2517,52.0456), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.2797,52.0547,30.9439,52.0492), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9358,52.0639,31.2797,52.0547), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9631,52.0811,31.1617,52.0833), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1617,52.0833,30.9631,52.0811), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3011,52.0911,31.1617,52.0833), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7839,52.1081,23.59833,52.10943), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.59833,52.10943,31.7839,52.1081), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3619,52.1167,23.59833,52.10943), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.48583,52.14693,31.7825,52.1658), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7825,52.1658,23.48583,52.14693), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7039,52.1986,23.21138,52.22387), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.21138,52.22387,23.29388,52.22581), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.29388,52.22581,23.21138,52.22387), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7022,52.2453,31.7169,52.2617), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7169,52.2617,31.6442,52.2733), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6442,52.2733,23.16888,52.2822), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.16888,52.2822,31.6442,52.2733), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5867,52.3189,23.16888,52.2822), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6297,52.3706,31.6231,52.3956), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6231,52.3956,31.6017,52.4061), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6017,52.4061,31.6231,52.3956), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6089,52.4914,23.38416,52.50416), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.38416,52.50416,31.5647,52.5158), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5647,52.5158,23.38416,52.50416), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5953,52.5322,31.5647,52.5158), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5817,52.5508,31.6533,52.5531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6533,52.5531,31.5817,52.5508), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.56361,52.58498,31.5836,52.5947), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5836,52.5947,23.56361,52.58498), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.88388,52.67804,31.5,52.69), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5,52.69,31.5583,52.7014), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5583,52.7014,23.93388,52.7122), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.93388,52.7122,31.5583,52.7014), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.595,52.7381,23.93388,52.7122), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5808,52.8031,31.54,52.8219), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.54,52.8219,31.5808,52.8031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5369,52.8561,31.4758,52.8633), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.4758,52.8633,31.5369,52.8561), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.93499,52.89054,31.4153,52.8931), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.4153,52.8931,23.93499,52.89054), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3833,52.9189,31.4153,52.8931), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3564,52.9781,31.2664,53.0219), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.2664,53.0219,31.3178,53.0544), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3178,53.0544,31.3272,53.0822), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3272,53.0822,32.1186,53.0872), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.1186,53.0872,31.3272,53.0822), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.9306,53.0956,31.3864,53.0992), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3864,53.0992,31.9306,53.0956), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.2233,53.1056,32.0125,53.11), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.0125,53.11,32.1864,53.1139), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.1864,53.1139,32.0125,53.11), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8869,53.1208,31.3764,53.1211), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3764,53.1211,31.8869,53.1208), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8011,53.1258,31.3764,53.1211), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.2989,53.1319,32.2256,53.1333), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.2256,53.1333,32.2989,53.1319), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3953,53.1814,32.3531,53.1819), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.3531,53.1819,31.3953,53.1814), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7756,53.1942,31.5725,53.1992), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5725,53.1992,31.7756,53.1942), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4367,53.2061,31.4225,53.2089), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.4225,53.2089,23.85416,53.21082), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.85416,53.21082,31.4225,53.2089), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6161,53.2183,23.85416,53.21082), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4983,53.2783,32.4711,53.2861), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4711,53.2861,32.4983,53.2783), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.5858,53.3069,32.4625,53.3083), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4625,53.3083,32.5858,53.3069), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.6153,53.3158,32.4625,53.3083), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.6044,53.3333,32.7369,53.3425), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.7369,53.3425,32.6044,53.3333), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.7239,53.3678,32.7369,53.3425), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.7364,53.4386,32.7381,53.4658), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.7381,53.4658,32.6578,53.4692), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.6578,53.4692,32.7381,53.4658), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.5975,53.4911,32.6706,53.4972), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.6706,53.4972,32.5975,53.4911), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.5706,53.5217,23.65805,53.52971), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.65805,53.52971,32.5706,53.5217), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4439,53.5719,23.65805,53.52971), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4147,53.6464,32.5106,53.6836), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.5106,53.6836,32.3769,53.7189), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.3769,53.7189,32.4617,53.7228), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.4617,53.7228,32.3769,53.7189), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.3294,53.7622,31.8958,53.7775), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8958,53.7775,32.1981,53.785), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.1981,53.785,31.8958,53.7775), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.76214,53.80378,31.8172,53.8039), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8172,53.8039,31.76214,53.80378), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((32.1197,53.82,31.8172,53.8039), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.52111,53.87859,31.8289,53.8864), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8289,53.8864,24.3869,53.8886), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.3869,53.8886,31.8289,53.8864), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.2778,53.8997,23.6444,53.9047), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.6444,53.9047,24.3325,53.9061), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.3325,53.9061,23.6444,53.9047), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.7797,53.9178,23.8336,53.9258), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.8336,53.9258,23.72,53.9267), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.72,53.9267,23.8336,53.9258), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.5792,53.9361,24.0886,53.9375), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.0886,53.9375,23.7875,53.9381), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.7875,53.9381,24.0886,53.9375), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.9861,53.9389,23.7875,53.9381), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.50375,53.94716,24.2314,53.9531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.2314,53.9531,23.935,53.9558), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((23.935,53.9558,24.2314,53.9531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.1756,53.9675,24.7278,53.9686), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.7278,53.9686,24.1756,53.9675), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.8208,53.9772,24.7278,53.9686), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.6122,53.9922,24.6917,54.0017), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.6917,54.0017,31.8656,54.0022), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8656,54.0022,24.6917,54.0017), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.8406,54.0344,31.7933,54.0531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7933,54.0531,31.8442,54.0644), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.8442,54.0644,31.7933,54.0531), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.7956,54.1014,31.6864,54.1031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6864,54.1031,31.7617,54.1033), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.7617,54.1033,31.6864,54.1031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.8356,54.1136,31.7617,54.1033), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.0244,54.1311,25.0719,54.1347), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.0719,54.1347,25.6692,54.1361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6692,54.1361,31.6161,54.1372), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.6161,54.1372,25.6692,54.1361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.8403,54.1422,31.5442,54.1433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.5442,54.1433,24.8403,54.1422), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5344,54.1469,31.5442,54.1433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7003,54.1547,24.9689,54.1586), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((24.9689,54.1586,25.7853,54.1606), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7853,54.1606,24.9689,54.1586), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.1619,54.1725,25.5047,54.1831), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5047,54.1831,25.1619,54.1725), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5478,54.2033,25.7872,54.2181), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7872,54.2181,25.5183,54.2258), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5183,54.2258,25.2094,54.23), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.2094,54.23,25.5183,54.2258), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3317,54.2375,25.5772,54.2403), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5772,54.2403,31.3317,54.2375), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.8078,54.2481,25.3917,54.2558), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.3917,54.2558,25.8078,54.2481), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.2178,54.2644,25.3917,54.2558), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7703,54.2878,25.7272,54.2906), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7272,54.2906,25.7703,54.2878), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3108,54.2967,25.4519,54.2997), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.4519,54.2997,31.3108,54.2967), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.4928,54.3053,25.4519,54.2997), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6111,54.3114,25.4928,54.3053), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5475,54.3286,25.7142,54.3311), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7142,54.3311,25.5475,54.3286), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.3117,54.3508,25.5572,54.3661), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.5572,54.3661,31.3117,54.3508), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.2639,54.3867,25.5572,54.3661), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6364,54.4272,31.2319,54.4567), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.2319,54.4567,31.19844,54.46004), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.19844,54.46004,31.2319,54.4567), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6331,54.4778,31.1306,54.4786), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1306,54.4786,25.6331,54.4778), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1942,54.4803,31.1306,54.4786), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.0825,54.5056,25.6489,54.5175), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.6489,54.5175,31.0825,54.5056), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1317,54.5706,25.7642,54.5792), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7642,54.5792,31.1317,54.5706), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1869,54.6094,25.7642,54.5792), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.1853,54.6422,31.0247,54.6522), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.0247,54.6522,31.1853,54.6422), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7272,54.6667,31.0247,54.6522), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.005,54.7117,25.7492,54.7283), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7492,54.7283,31.005,54.7117), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7367,54.7894,30.78,54.7969), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.78,54.7969,25.7367,54.7894), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.8036,54.8139,30.78,54.7969), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8011,54.855,25.7889,54.8703), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.7889,54.8703,30.845,54.8811), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.845,54.8811,25.7889,54.8703), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.8625,54.9108,30.845,54.8811), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8339,54.9442,30.9475,54.9692), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((25.8817,54.9442,30.9475,54.9692), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9475,54.9692,26.1608,54.9772), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.1608,54.9772,30.9475,54.9692), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9558,54.9922,26.1608,54.9772), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.0078,55.0267,26.2194,55.03), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.2194,55.03,30.925,55.0333), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.925,55.0333,26.2194,55.03), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.0297,55.0433,30.925,55.0333), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.2483,55.0711,31.0297,55.0433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9872,55.1117,26.2533,55.1236), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.2533,55.1236,26.6158,55.1247), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6158,55.1247,26.2533,55.1236), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4503,55.1328,26.6158,55.1247), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6417,55.1425,31.0047,55.1433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((31.0047,55.1433,26.6417,55.1425), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.2842,55.1481,31.0047,55.1433), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4817,55.155,26.2842,55.1481), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6417,55.1908,30.9133,55.2031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9133,55.2031,26.6417,55.1908), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6686,55.2181,30.9133,55.2031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.7133,55.2431,26.7753,55.2506), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.7753,55.2506,26.7133,55.2431), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8786,55.2603,26.7753,55.2506), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.8197,55.2811,30.8136,55.2933), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8136,55.2933,26.8197,55.2811), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.5547,55.3131,26.7669,55.3136), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.7669,55.3136,26.5547,55.3131), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6314,55.3311,26.4558,55.3414), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.4558,55.3414,26.6314,55.3311), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8578,55.3536,26.4558,55.3414), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.5025,55.39,30.9394,55.3994), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9394,55.3994,26.5025,55.39), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.5236,55.4439,30.9006,55.4592), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9006,55.4592,26.5694,55.4683), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.5694,55.4683,30.9006,55.4592), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9064,55.4792,26.5694,55.4683), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9394,55.4917,30.9064,55.4792), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9186,55.5069,26.545,55.5111), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.545,55.5111,30.9186,55.5069), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.935,55.5319,26.545,55.5111), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6267,55.5931,30.7567,55.5967), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.7567,55.5967,26.6267,55.5931), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.9256,55.6031,30.7567,55.5967), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.8769,55.6192,30.9256,55.6031), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.7269,55.6564,30.6472,55.6614), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6472,55.6614,30.7269,55.6564), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.61409,55.67605,30.6089,55.6814), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6089,55.6814,26.7453,55.6867), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.7453,55.6867,29.4939,55.6889), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4939,55.6889,26.7453,55.6867), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.5264,55.6914,29.4939,55.6889), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.6617,55.7058,26.85,55.7108), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.85,55.7108,26.6617,55.7058), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.6067,55.7261,26.85,55.7108), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.6183,55.7517,29.3858,55.7564), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.3858,55.7564,29.6183,55.7517), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.4942,55.7639,29.3858,55.7564), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.8231,55.7753,26.9081,55.7786), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.9081,55.7786,29.8231,55.7753), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.7192,55.7825,26.9081,55.7786), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.2822,55.7872,29.3592,55.7894), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.3592,55.7894,27.2822,55.7872), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.6014,55.7919,29.3592,55.7894), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.4897,55.8,27.4,55.8039), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.4,55.8039,30.4897,55.8), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.8594,55.8225,27.3508,55.8264), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.3508,55.8264,30.1406,55.8281), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.1406,55.8281,27.3508,55.8264), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((26.99,55.8347,30.2992,55.8361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.2992,55.8361,26.99,55.8347), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.6328,55.8406,30.2992,55.8361), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.1561,55.8464,29.9283,55.8517), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.9283,55.8517,30.0147,55.8544), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.0147,55.8544,29.9283,55.8517), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((30.2261,55.8661,30.0147,55.8544), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4069,55.8958,29.4644,55.9111), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4644,55.9111,27.6472,55.9242), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.6472,55.9242,29.4644,55.9111), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.8469,55.9425,29.4122,55.9578), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.4122,55.9578,28.73,55.9631), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.73,55.9631,29.4122,55.9578), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.8536,55.9706,28.73,55.9631), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.7911,55.9889,28.8536,55.9706), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.7956,56.0253,28.7022,56.0322), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.7022,56.0322,29.0594,56.0328), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((29.0594,56.0328,28.7022,56.0322), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.3125,56.05,28.3536,56.0569), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.3536,56.0569,28.3125,56.05), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.8933,56.0656,28.3536,56.0569), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.6461,56.0936,28.3875,56.0961), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.3875,56.0961,28.6461,56.0936), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.5208,56.1053,27.9294,56.1133), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((27.9294,56.1133,28.5208,56.1053), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.0583,56.1364,28.1665,56.15032), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.1665,56.15032,28.0583,56.1364), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.1665,56.15032,28.0583,56.1364), mapfile, tile_dir, 0, 11, "by-byelarus")
	render_tiles((28.1183,56.1672,28.1665,56.15032), mapfile, tile_dir, 0, 11, "by-byelarus")