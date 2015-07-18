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
    # Region: SI
    # Region Name: Slovenia

	render_tiles((15.1686,45.4256,15.2242,45.4311), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.2242,45.4311,15.1686,45.4256), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.1881,45.4394,13.6914,45.4444), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.6914,45.4444,15.1881,45.4394), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.9069,45.4533,15.3353,45.4564), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3353,45.4564,13.9069,45.4533), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.9867,45.46,15.2722,45.4617), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.2722,45.4617,13.9867,45.46), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.6153,45.4644,14.8178,45.4658), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.8178,45.4658,13.6153,45.4644), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.1314,45.4744,14.9072,45.4764), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.9072,45.4764,13.8586,45.4781), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.8586,45.4781,14.9072,45.4764), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.9972,45.48,13.8586,45.4781), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3233,45.4822,14.3206,45.4842), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.3206,45.4842,15.0847,45.4861), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.0847,45.4861,14.3206,45.4842), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.3928,45.4861,14.3206,45.4842), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.0211,45.4894,15.0847,45.4861), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.7972,45.5014,14.2383,45.5056), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.2383,45.5056,13.56967,45.50707), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.56967,45.50707,13.9783,45.5078), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.9783,45.5078,13.56967,45.50707), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3361,45.5103,13.9783,45.5078), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.9894,45.5222,14.9292,45.5244), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.9292,45.5244,13.9894,45.5222), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.4858,45.5297,14.7031,45.5328), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.7031,45.5328,14.4858,45.5297), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.6869,45.5367,15.3039,45.5378), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3039,45.5378,14.6869,45.5367), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.74944,45.54694,15.3039,45.5378), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.6853,45.5742,13.85944,45.58749), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.85944,45.58749,13.71751,45.59766), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.71751,45.59766,14.5097,45.5981), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.5097,45.5981,13.71751,45.59766), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.2831,45.6081,14.5097,45.5981), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.6136,45.62,14.5544,45.6311), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.5544,45.6311,13.91916,45.63749), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.91916,45.63749,14.5544,45.6311), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3919,45.6467,15.3481,45.6492), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3481,45.6492,15.3919,45.6467), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.565,45.665,15.3472,45.675), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3472,45.675,14.6014,45.6753), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.6014,45.6753,15.3472,45.675), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3028,45.6908,15.2833,45.6947), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.2833,45.6947,15.3358,45.6975), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3358,45.6975,15.2833,45.6947), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3681,45.7028,15.3358,45.6975), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3586,45.7144,15.3086,45.7194), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3086,45.7194,15.3586,45.7144), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.82194,45.72693,15.2919,45.7311), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.2919,45.7311,13.82194,45.72693), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.3225,45.7614,15.2919,45.7311), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.4822,45.8022,13.59805,45.81081), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.59805,45.81081,15.4447,45.8158), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.4447,45.8158,13.59805,45.81081), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6567,45.8233,15.5383,45.8264), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.5383,45.8264,15.6567,45.8233), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.4994,45.8358,15.6978,45.8442), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6978,45.8442,15.6094,45.8486), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6094,45.8486,15.6978,45.8442), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.57778,45.85416,15.6094,45.8486), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6794,45.8619,13.57778,45.85416), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6906,45.9028,13.62361,45.92221), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.62361,45.92221,15.7236,45.9347), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.7236,45.9347,13.62361,45.92221), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.5425,45.96748,13.635,45.98859), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.635,45.98859,13.59,45.99332), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.59,45.99332,13.635,45.98859), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.47944,46.01332,15.7,46.02), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.7,46.02,13.47944,46.01332), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.7183,46.0472,15.7,46.02), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6294,46.0869,15.7183,46.0472), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.64583,46.14249,15.5997,46.1425), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.5997,46.1425,13.64583,46.14249), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.66444,46.18304,15.7811,46.2125), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.7811,46.2125,15.6519,46.2167), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6519,46.2167,13.55055,46.21804), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.55055,46.21804,15.6519,46.2167), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.6767,46.2267,13.44333,46.23026), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.44333,46.23026,15.6767,46.2267), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.8217,46.2583,13.44333,46.23026), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.38305,46.29721,16.0172,46.2981), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.0172,46.2981,13.38305,46.29721), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.0811,46.3311,16.0172,46.2981), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.48111,46.36943,16.2939,46.3744), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.2939,46.3744,13.48111,46.36943), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.0783,46.3797,16.1925,46.3847), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.1925,46.3847,16.3039,46.3858), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.3039,46.3858,16.1925,46.3847), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.57444,46.38749,16.2631,46.3889), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.2631,46.3889,14.57444,46.38749), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.1447,46.4061,16.2689,46.4119), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.2689,46.4119,16.1447,46.4061), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.59416,46.43748,14.15944,46.44082), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.15944,46.44082,14.59416,46.43748), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.42833,46.44637,13.69222,46.45026), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.69222,46.45026,14.42833,46.44637), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.5772,46.4694,16.60924,46.47517), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.60924,46.47517,16.5772,46.4694), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.08472,46.48859,16.2514,46.4983), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.2514,46.4983,14.08472,46.48859), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.81472,46.51276,13.71896,46.52554), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((13.71896,46.52554,16.3007,46.53174), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.3007,46.53174,13.71896,46.52554), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.3978,46.5408,16.3007,46.53174), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((14.87055,46.61582,15.50083,46.61804), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.50083,46.61804,14.87055,46.61582), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.39166,46.63638,15.50083,46.61804), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.10527,46.6572,16.02694,46.66137), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.02694,46.66137,15.10527,46.6572), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.02694,46.66137,15.10527,46.6572), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.42666,46.68776,15.64944,46.70971), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.64944,46.70971,16.42666,46.68776), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.99333,46.73721,15.64944,46.70971), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.31833,46.77971,15.99333,46.73721), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((15.99361,46.83471,16.35055,46.84109), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.35055,46.84109,15.99361,46.83471), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.1067,46.85139,16.35055,46.84109), mapfile, tile_dir, 0, 11, "si-slovenia")
	render_tiles((16.28167,46.87248,16.1067,46.85139), mapfile, tile_dir, 0, 11, "si-slovenia")