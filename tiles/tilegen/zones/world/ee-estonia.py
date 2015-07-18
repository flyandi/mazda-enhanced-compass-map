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
    # Region: EE
    # Region Name: Estonia

	render_tiles((22.05166,57.90971,22.00055,57.91693), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.05166,57.90971,22.00055,57.91693), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.00055,57.91693,22.05166,57.90971), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.99667,57.97971,21.96389,57.98166), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.96389,57.98166,21.99667,57.97971), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.2025,57.9861,21.96389,57.98166), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.08222,58.07665,22.19722,58.13749), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.19722,58.13749,22.27916,58.18388), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.27916,58.18388,22.73444,58.21638), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.73444,58.21638,22.39417,58.22332), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.39417,58.22332,22.73444,58.21638), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.75694,58.24026,21.88444,58.25221), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.88444,58.25221,22.71055,58.26054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.71055,58.26054,21.88444,58.25221), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.82555,58.27026,22.71055,58.26054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.91027,58.29749,21.85639,58.30166), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.85639,58.30166,21.91027,58.29749), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.95778,58.31443,22.92944,58.32638), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.92944,58.32638,21.87361,58.33249), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.87361,58.33249,22.92944,58.32638), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.89917,58.34277,21.96139,58.34915), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.96139,58.34915,22.0075,58.35332), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.0075,58.35332,21.96139,58.34915), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.02861,58.35777,22.0075,58.35332), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.94917,58.38165,23.02861,58.35777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.07805,58.42055,22.09583,58.42165), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.09583,58.42165,22.07805,58.42055), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.2675,58.42944,22.09583,58.42165), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.91666,58.45749,23.27888,58.46054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.27888,58.46054,21.91666,58.45749), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.15638,58.47916,22.09889,58.48055), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.09889,58.48055,23.15638,58.47916), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.24028,58.49915,21.83194,58.50499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((21.83194,58.50499,22.24028,58.49915), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.00055,58.51499,22.28694,58.52082), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.28694,58.52082,22.00055,58.51499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.19833,58.54943,22.27889,58.55693), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.27889,58.55693,22.19833,58.54943), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.72361,58.58166,23.00945,58.5895), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.00945,58.5895,22.72361,58.58166), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.87167,58.61777,22.55139,58.63194), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.55139,58.63194,22.87167,58.61777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.56527,58.68665,22.46972,58.7036), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.56527,58.68665,22.46972,58.7036), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.46972,58.7036,22.66694,58.70527), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.66694,58.70527,22.46972,58.7036), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.47639,58.75694,22.78444,58.77583), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.78444,58.77583,22.88444,58.77971), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.88444,58.77971,22.78444,58.77583), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.82917,58.81832,22.77583,58.8186), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.77583,58.8186,22.82917,58.81832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.02166,58.82054,22.77583,58.8186), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.89889,58.83443,22.44555,58.83804), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.44555,58.83804,22.89889,58.83443), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.04805,58.84444,22.44555,58.83804), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.38194,58.88693,22.0775,58.92138), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.0775,58.92138,22.04222,58.93999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.04222,58.93999,22.0775,58.92138), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.92972,58.98249,22.73389,59.00138), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.73389,59.00138,22.6986,59.01999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.6986,59.01999,22.53639,59.02471), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.53639,59.02471,22.6986,59.01999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.71583,59.06915,22.6405,59.08179), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((22.6405,59.08179,22.71583,59.06915), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.3383,57.5228,26.5172,57.5244), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.5172,57.5244,27.3383,57.5228), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.6089,57.5269,26.5172,57.5244), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.5611,57.5361,27.3711,57.5364), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.3711,57.5364,26.5611,57.5361), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.245,57.5492,26.635,57.5558), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.635,57.5558,27.0872,57.5622), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.0872,57.5622,26.635,57.5558), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.4603,57.5706,26.7608,57.5711), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.7608,57.5711,26.4603,57.5706), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.7194,57.5817,26.8292,57.5825), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.8292,57.5825,26.7194,57.5817), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.3481,57.5956,26.8292,57.5825), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.0186,57.6114,27.4058,57.6136), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.2992,57.6114,27.4058,57.6136), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4058,57.6136,27.0186,57.6114), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.91,57.6192,27.4058,57.6136), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.8644,57.6258,26.91,57.6192), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.8908,57.6339,27.415,57.6364), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.415,57.6364,26.8908,57.6339), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.3908,57.6589,27.415,57.6364), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4131,57.6906,26.1803,57.7219), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.1803,57.7219,27.5267,57.7228), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5267,57.7228,26.1803,57.7219), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5164,57.7733,26.0375,57.7847), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.0375,57.7847,27.5164,57.7733), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.0286,57.8022,27.5728,57.8028), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5728,57.8028,26.0286,57.8022), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5458,57.8178,27.5728,57.8028), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.735,57.8356,26.0467,57.8403), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.0467,57.8403,27.735,57.8356), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.0314,57.85,26.0467,57.8403), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8211,57.8658,24.31005,57.87083), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.8014,57.8658,24.31005,57.87083), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.31005,57.87083,24.4203,57.8744), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.4203,57.8744,24.31005,57.87083), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8053,57.8911,24.4203,57.8744), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.4531,57.9133,25.6228,57.9164), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.6228,57.9164,24.4531,57.9133), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.6839,57.9264,25.7492,57.9311), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.7492,57.9311,27.6839,57.9264), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.5772,57.9422,25.7492,57.9311), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.5536,57.9547,24.7178,57.9589), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.7178,57.9589,24.5536,57.9547), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.5764,57.9669,24.7178,57.9589), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.8275,57.98,25.2325,57.9928), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.2325,57.9928,25.4611,57.9944), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.4611,57.9944,25.2325,57.9928), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.6983,57.9969,25.4611,57.9944), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.7542,58.0008,27.6983,57.9969), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.8853,58.0075,27.67,58.0097), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.2944,58.0075,27.67,58.0097), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.67,58.0097,24.8853,58.0075), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.9681,58.0144,27.67,58.0097), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.6425,58.0278,25.2061,58.0317), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.2061,58.0317,27.6425,58.0278), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.4217,58.0356,25.3483,58.0367), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.3483,58.0367,25.4217,58.0356), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.2969,58.0381,25.3483,58.0367), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.6767,58.0508,25.2969,58.0381), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.2631,58.0692,24.46083,58.06944), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.46083,58.06944,25.2631,58.0692), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.0917,58.0717,24.46083,58.06944), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.17,58.0744,25.0917,58.0717), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.6733,58.0789,25.3017,58.0831), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.3017,58.0831,27.6733,58.0789), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5542,58.1328,25.3017,58.0831), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4742,58.2139,24.11055,58.23221), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.11055,58.23221,24.47277,58.24721), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.47277,58.24721,24.11055,58.23221), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.14666,58.27026,24.23805,58.27138), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.23805,58.27138,24.14666,58.27026), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.5425,58.28443,24.23805,58.27138), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4647,58.2978,24.5425,58.28443), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.55944,58.32054,23.93777,58.32582), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.93777,58.32582,24.55944,58.32054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.73194,58.34666,24.51778,58.35304), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.51778,58.35304,23.73194,58.34666), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5392,58.3617,24.51778,58.35304), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.32611,58.38471,24.41972,58.38666), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.41972,58.38666,24.32611,58.38471), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.5425,58.4139,24.41972,58.38666), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.67167,58.53665,23.5,58.55943), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.5,58.55943,23.67167,58.53665), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.49555,58.69415,23.80833,58.72999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.80833,58.72999,23.53889,58.7461), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.53889,58.7461,23.80833,58.72999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.42944,58.76249,23.53889,58.7461), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.79139,58.80082,27.4231,58.8014), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4231,58.8014,23.79139,58.80082), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.5275,58.82304,27.4231,58.8014), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.4936,58.8819,23.41611,58.91055), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.41611,58.91055,27.4936,58.8819), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.43111,58.93943,23.54083,58.96721), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.54083,58.96721,23.63778,58.97054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.63778,58.97054,23.54083,58.96721), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.725,58.9878,23.63778,58.97054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.61194,59.01027,23.40722,59.0186), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.40722,59.0186,23.61194,59.01027), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.43305,59.05916,27.7922,59.0633), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.7922,59.0633,23.43305,59.05916), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.49972,59.08499,27.7922,59.0633), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.52639,59.10693,23.49972,59.08499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8442,59.1597,23.46416,59.20638), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.46416,59.20638,23.51083,59.22832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.51083,59.22832,27.8633,59.2289), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8633,59.2289,23.51083,59.22832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.73861,59.23277,27.8633,59.2289), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8867,59.2542,23.73861,59.23277), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.09,59.27583,27.8806,59.2761), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8806,59.2761,24.09,59.27583), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((23.74222,59.2786,27.8806,59.2761), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.8969,59.2861,23.74222,59.2786), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.10028,59.30388,28.0972,59.3081), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.0972,59.3081,24.10028,59.30388), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.0075,59.3361,24.19917,59.34277), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.19917,59.34277,28.0075,59.3361), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.1892,59.3525,24.22361,59.35832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.22361,59.35832,24.02,59.36277), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.02,59.36277,24.22361,59.35832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.1939,59.3758,24.02,59.36277), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.03722,59.39054,24.08111,59.39221), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.08111,59.39221,24.03722,59.39054), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.1703,59.3978,24.24694,59.3986), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.24694,59.3986,28.1703,59.3978), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.88055,59.40777,24.16777,59.41249), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.16777,59.41249,27.88055,59.40777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.31889,59.42582,27.96139,59.43082), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((27.96139,59.43082,24.31889,59.42582), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.65083,59.4361,27.96139,59.43082), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.96472,59.44499,24.73083,59.44777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.73083,59.44777,24.78361,59.44804), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.78361,59.44804,24.73083,59.44777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.32917,59.46416,24.62222,59.46777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.62222,59.46777,24.32917,59.46416), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.39472,59.47499,28.00668,59.4817), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.00668,59.4817,24.39472,59.47499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((28.00668,59.4817,24.39472,59.47499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.81694,59.48888,24.68972,59.48943), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.68972,59.48943,24.81694,59.48888), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.40459,59.49029,24.68972,59.48943), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.78722,59.51499,25.54556,59.53387), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.54556,59.53387,24.78722,59.51499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.65055,59.55332,25.64278,59.56527), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.64278,59.56527,24.79,59.56666), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((24.79,59.56666,25.64278,59.56527), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.70083,59.56832,24.79,59.56666), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.84777,59.57499,25.79333,59.5786), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.79333,59.5786,25.84777,59.57499), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((26.09611,59.58221,25.79333,59.5786), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.95111,59.58693,25.71527,59.5886), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.71527,59.5886,25.95111,59.58693), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.88556,59.62554,25.68417,59.62777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.68417,59.62777,25.99277,59.62999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.99277,59.62999,25.68417,59.62777), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.77472,59.63554,25.99277,59.62999), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.46944,59.64832,25.77472,59.63554), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.49277,59.66415,25.69444,59.66832), mapfile, tile_dir, 0, 11, "ee-estonia")
	render_tiles((25.69444,59.66832,25.49277,59.66415), mapfile, tile_dir, 0, 11, "ee-estonia")