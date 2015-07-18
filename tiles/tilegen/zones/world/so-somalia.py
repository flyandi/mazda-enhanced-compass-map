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
    # Region: SO
    # Region Name: Somalia

	render_tiles((53.75916,12.30833,53.57638,12.34472), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.75916,12.30833,53.57638,12.34472), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.57638,12.34472,54.03944,12.35056), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((54.03944,12.35056,53.57638,12.34472), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.30888,12.53444,54.4936,12.54333), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((54.4936,12.54333,53.30888,12.53444), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.38999,12.56111,54.5336,12.56583), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((54.5336,12.56583,53.38999,12.56111), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.83443,12.60694,53.77471,12.61639), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.77471,12.61639,53.83443,12.60694), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((54.22083,12.65055,53.9711,12.65694), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.9711,12.65694,53.40499,12.65805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.40499,12.65805,53.9711,12.65694), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((54.19166,12.67972,53.40499,12.65805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.64804,12.70583,53.54193,12.71278), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((53.54193,12.71278,53.64804,12.70583), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.55772,-1.67436,41.55526,-1.59222), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.55526,-1.59222,41.55772,-1.67436), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.82388,-1.27917,41.87193,-1.21806), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.87193,-1.21806,41.83221,-1.16889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.83221,-1.16889,41.87193,-1.21806), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.97137,-1.02556,41.02971,-0.90639), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.02971,-0.90639,42.08638,-0.83167), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.08638,-0.83167,42.15083,-0.82), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.15083,-0.82,42.08638,-0.83167), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.45165,-0.46306,42.74832,-0.14778), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.74832,-0.14778,42.89287,0.00053), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((40.98859,0,42.89287,0.00053), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.89287,0.00053,43.21304,0.34806), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.21304,0.34806,43.53805,0.68944), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.53805,0.68944,43.89555,0.98917), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.89555,0.98917,43.53805,0.68944), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.24221,1.3,44.60888,1.59555), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.60888,1.59555,45.00416,1.86083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.00416,1.86083,45.43221,2.08389), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.43221,2.08389,45.00416,1.86083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.84693,2.32083,46.10332,2.50972), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.10332,2.50972,45.84693,2.32083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((40.98631,2.83259,46.43027,2.84805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.43027,2.84805,40.98631,2.83259), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.33693,3.1675,46.7761,3.16889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.7761,3.16889,41.33693,3.1675), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.09304,3.52278,47.42832,3.86055), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.42832,3.86055,47.49082,3.93667), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.49082,3.93667,41.91364,3.99046), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.91364,3.99046,41.91364,3.99866), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.91364,3.99866,41.91364,3.99046), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.93054,4.00972,41.91364,3.99866), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((41.95582,4.08528,41.93054,4.00972), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.07582,4.17528,42.19582,4.20889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.19582,4.20889,42.51415,4.23361), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.51415,4.23361,42.19582,4.20889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.83166,4.29528,47.80249,4.29916), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.80249,4.29916,42.83166,4.29528), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.91415,4.35611,47.80249,4.29916), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.97137,4.50861,42.91415,4.35611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.16387,4.67083,48.08582,4.67694), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.08582,4.67694,43.16387,4.67083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.6236,4.86916,44.95082,4.9025), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.95082,4.9025,43.6236,4.86916), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.98193,4.96305,44.95082,4.9025), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.34304,5.10139,43.98193,4.96305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.64221,5.4775,48.34304,5.10139), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.89999,5.90555,49.05971,6.19305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.05971,6.19305,49.07388,6.41055), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.07388,6.41055,46.41859,6.47333), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.41859,6.47333,49.07388,6.41055), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.2911,6.88389,49.56749,7.29167), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.56749,7.29167,49.58777,7.31278), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.58777,7.31278,49.56749,7.29167), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.81915,7.76,49.80638,7.88278), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.80638,7.88278,49.83193,7.95028), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.83193,7.95028,47.01193,8.00111), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.01193,8.00111,47.98943,8.00305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.98943,8.00305,47.01193,8.00111), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.10165,8.17555,47.98943,8.00305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.28944,8.49111,50.32555,8.54639), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.32555,8.54639,45.34332,8.56416), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.34332,8.56416,50.32555,8.54639), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.32193,8.61889,45.34332,8.56416), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.4761,8.93889,44.01054,9.00722), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.01054,9.00722,50.4761,8.93889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.6436,9.08305,44.01054,9.00722), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.64416,9.19389,50.69582,9.27611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.69582,9.27611,50.7661,9.31305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.7661,9.31305,43.58305,9.33611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.58305,9.33611,43.6272,9.35444), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.6272,9.35444,43.58305,9.33611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.43082,9.42611,50.84082,9.44305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.84082,9.44305,43.43082,9.42611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.34109,9.60805,43.27804,9.64139), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.27804,9.64139,50.82443,9.67027), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.82443,9.67027,43.27804,9.64139), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.21609,9.88138,43.09165,9.91833), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.09165,9.91833,43.21609,9.88138), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.90166,10.18944,50.89332,10.30916), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.89332,10.30916,42.81693,10.31388), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.81693,10.31388,50.89332,10.30916), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.95443,10.35833,51.36582,10.37222), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.36582,10.37222,50.95443,10.35833), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.38471,10.41194,51.02721,10.41305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.08721,10.41194,51.02721,10.41305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.02721,10.41305,44.38471,10.41194), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.24582,10.42444,44.95971,10.42666), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.95971,10.42666,51.24582,10.42444), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.00971,10.43277,44.95971,10.42666), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((44.27832,10.44777,51.4129,10.44937), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.22665,10.44777,51.4129,10.44937), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.4129,10.44937,44.27832,10.44777), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.7861,10.45555,51.4129,10.44937), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.37415,10.48861,51.26082,10.50027), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.26082,10.50027,51.37415,10.48861), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.7122,10.53194,51.17443,10.56194), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.17443,10.56194,51.15916,10.58361), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.15916,10.58361,51.17443,10.56194), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.66553,10.62138,51.15916,10.58361), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.44804,10.68889,46.34749,10.69694), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.34749,10.69694,46.44804,10.68889), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.65554,10.74833,46.25027,10.78611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((46.25027,10.78611,45.98666,10.79139), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.98666,10.79139,46.25027,10.78611), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((45.79471,10.87694,43.68416,10.95417), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.68416,10.95417,51.11416,10.96778), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.11416,10.96778,43.68416,10.95417), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.94328,11.00391,47.11665,11.02305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((42.94328,11.00391,47.11665,11.02305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.11665,11.02305,42.94328,11.00391), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.69166,11.09916,51.17027,11.14083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.17027,11.14083,48.16249,11.14555), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.16249,11.14555,51.17027,11.14083), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.39888,11.18,47.51999,11.18305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((47.51999,11.18305,51.07943,11.18472), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.07943,11.18472,47.51999,11.18305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.49888,11.21556,48.92401,11.24555), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.92401,11.24555,43.49888,11.21556), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.34749,11.27639,48.92401,11.24555), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.07638,11.32805,48.65443,11.32917), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((48.65443,11.32917,51.07638,11.32805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.42915,11.34194,48.65443,11.32917), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.49249,11.36583,49.42915,11.34194), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.3361,11.42389,49.54388,11.43333), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((49.54388,11.43333,43.3361,11.42389), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.28749,11.46305,43.2586,11.46361), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.2586,11.46361,43.28749,11.46305), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.24862,11.47285,43.2586,11.46361), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((43.2936,11.49055,50.06554,11.50805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.06554,11.50805,43.2936,11.49055), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.27388,11.58889,50.06554,11.50805), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.49888,11.75,51.26582,11.7825), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.26582,11.7825,50.49888,11.75), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((51.27888,11.83167,51.26582,11.7825), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.57054,11.91083,51.27888,11.83167), mapfile, tile_dir, 0, 11, "so-somalia")
	render_tiles((50.77221,11.99055,50.57054,11.91083), mapfile, tile_dir, 0, 11, "so-somalia")