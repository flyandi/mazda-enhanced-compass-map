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
    # Region: DZ
    # Region Name: Algeria

	render_tiles((3.33194,18.97638,3.12139,19.1361), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.12139,19.1361,4.24509,19.14619), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((4.24509,19.14619,3.12139,19.1361), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.15361,19.22305,4.24509,19.14619), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.27222,19.3736,5.8125,19.4461), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.8125,19.4461,3.24805,19.51221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.24805,19.51221,3.21833,19.53222), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.21833,19.53222,3.24805,19.51221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.235,19.81555,2.84639,19.97471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.84639,19.97471,2.42167,20.05305), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.42167,20.05305,2.84639,19.97471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.32361,20.19944,2.1025,20.22083), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.1025,20.22083,1.90889,20.23138), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.90889,20.23138,2.1025,20.22083), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.21805,20.27916,2.17806,20.27944), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.17806,20.27944,2.21805,20.27916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.81028,20.30111,1.88111,20.30138), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.88111,20.30138,1.81028,20.30111), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.66556,20.44221,1.66305,20.53611), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.66305,20.53611,1.66556,20.44221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.48333,20.63916,1.375,20.65749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.375,20.65749,1.48333,20.63916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.17889,20.73277,1.32667,20.73333), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.32667,20.73333,1.17889,20.73277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.16139,20.7611,1.32667,20.73333), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.45449,20.85213,1.16139,20.7611), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.16924,21.0999,7.45449,20.85213), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.89444,21.72221,-0.00236,21.82636), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.00236,21.82636,8.89444,21.72221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.36083,22.5886,-1.42944,22.80694), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.42944,22.80694,10.36083,22.5886), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((11.84444,23.44194,11.98645,23.52232), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((11.98645,23.52232,11.84444,23.44194), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.10028,23.91416,11.55889,24.30249), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((11.55889,24.30249,10.42222,24.47805), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.42222,24.47805,10.71527,24.56721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.71527,24.56721,10.25222,24.60583), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.25222,24.60583,10.71527,24.56721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.21833,24.75111,10.05444,24.83805), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.05444,24.83805,10.21833,24.75111), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.80639,25.00027,10.05444,24.83805), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-5.14806,25.21277,10.02666,25.33749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((10.02666,25.33749,-5.14806,25.21277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.39833,26.15332,9.39305,26.18277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.39305,26.18277,9.39833,26.15332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.43,26.23305,-6.89722,26.26749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.89722,26.26749,9.43,26.23305), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.49944,26.35749,9.63861,26.41555), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.63861,26.41555,9.49944,26.35749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.87638,26.52888,9.63861,26.41555), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.93055,26.85971,9.84833,26.9086), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.84833,26.9086,9.93055,26.85971), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.66929,27.2802,9.73499,27.32388), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.73499,27.32388,-8.66929,27.2802), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.83916,27.50805,9.81388,27.57638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.81388,27.57638,9.88111,27.62111), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.88111,27.62111,9.81388,27.57638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.66929,27.66885,9.88111,27.62111), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.95417,27.8686,-8.66929,27.66885), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.79027,28.27055,9.95417,27.8686), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.66929,28.70929,-8.49555,28.79055), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.49555,28.79055,9.87333,28.85916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.87333,28.85916,-8.35778,28.91916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.35778,28.91916,9.87333,28.85916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-8.04667,29.09361,-8.35778,28.91916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-7.62278,29.39194,-7.43417,29.39722), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-7.43417,29.39722,-7.62278,29.39194), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.76472,29.43333,-7.43417,29.39722), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.57556,29.57111,-7.19972,29.59472), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-7.19972,29.59472,-6.57556,29.57111), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.51111,29.6336,-7.19972,29.59472), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.40028,29.80444,9.56666,29.80694), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.56666,29.80694,-6.40028,29.80444), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.19139,29.80972,9.56666,29.80694), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-6.47444,29.83305,-6.19139,29.80972), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-5.59389,29.89694,-6.47444,29.83305), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-5.39611,29.97416,-5.59389,29.89694), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.30389,30.12249,-5.145,30.18666), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-5.145,30.18666,9.53202,30.23606), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.53202,30.23606,-5.145,30.18666), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.9425,30.49221,-4.59195,30.62638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.59195,30.62638,-4.43028,30.63721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.43028,30.63721,-4.59195,30.62638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.17389,30.76611,-4.43028,30.63721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-4.02722,30.90666,-3.64667,30.9611), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.64667,30.9611,-3.60222,30.99138), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.60222,30.99138,-3.64667,30.9611), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.59805,31.08611,-3.81556,31.15249), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.81556,31.15249,-3.82556,31.18277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.82556,31.18277,-3.72444,31.1886), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.72444,31.1886,-3.82556,31.18277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.79639,31.22166,-3.72444,31.1886), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.80417,31.33916,-3.72472,31.39471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.72472,31.39471,-3.80417,31.33916), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.83556,31.46693,-3.72472,31.39471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.19555,31.56916,-3.83528,31.65027), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.83528,31.65027,-3.51889,31.67277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.51889,31.67277,-3.83528,31.65027), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.81861,31.69555,-3.71472,31.71638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-3.71472,31.71638,-3.81861,31.69555), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.9725,31.85083,-3.71472,31.71638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.93833,32.02887,-2.86528,32.08471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.86528,32.08471,-1.21306,32.08971), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.21306,32.08971,9.05916,32.09109), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((9.05916,32.09109,-1.21306,32.08971), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.5,32.10804,-1.17778,32.11499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.17778,32.11499,-1.5,32.10804), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.22028,32.14999,-1.29278,32.15915), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.29278,32.15915,-1.19861,32.16693), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.19861,32.16693,-1.29278,32.15915), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.25389,32.21471,-1.19861,32.16693), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.25389,32.21471,-1.19861,32.16693), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.25,32.32693,-1.19639,32.4047), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.19639,32.4047,-1.07806,32.44137), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.07806,32.44137,-1.19639,32.4047), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.01056,32.50832,8.35111,32.5311), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.35111,32.5311,-1.01056,32.50832), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.38305,32.72443,8.30611,32.83415), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.30611,32.83415,-1.54278,32.93942), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.54278,32.93942,-1.48722,32.97915), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.48722,32.97915,-1.54278,32.93942), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.47889,33.05804,8.02278,33.11276), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.02278,33.11276,8.09027,33.11443), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.09027,33.11443,8.02278,33.11276), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.765,33.2086,7.73194,33.24832), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.73194,33.24832,-1.66556,33.25665), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.66556,33.25665,7.73194,33.24832), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.66667,33.38332,7.72416,33.43942), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.72416,33.43942,-1.66667,33.38332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.60028,33.55693,-1.64194,33.65082), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.64194,33.65082,-1.72667,33.69553), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.72667,33.69553,-1.64194,33.65082), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.5225,33.7886,-1.72667,33.69553), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.49167,33.89971,7.5225,33.7886), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.65361,34.08498,7.52722,34.10193), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.52722,34.10193,-1.65361,34.08498), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.65694,34.21249,7.7675,34.23637), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.7675,34.23637,7.65694,34.21249), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.79333,34.37832,7.83972,34.41248), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.83972,34.41248,-1.79333,34.37832), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.68639,34.48526,7.83972,34.41248), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.85333,34.60054,8.2475,34.6411), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.2475,34.6411,-1.85333,34.60054), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.22666,34.69553,-1.74722,34.74721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.74722,34.74721,8.28861,34.75277), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.28861,34.75277,-1.74722,34.74721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.26139,34.91026,-2.20528,35.05138), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.20528,35.05138,-2.06556,35.07193), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.06556,35.07193,-1.89167,35.08971), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.89167,35.08971,-2.19861,35.09471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.19861,35.09471,-1.89167,35.08971), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-2.21152,35.09972,-2.19861,35.09471), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.40111,35.19221,8.45083,35.23387), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.45083,35.23387,8.42722,35.26749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.42722,35.26749,8.345,35.28721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.345,35.28721,-1.47472,35.30582), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.47472,35.30582,-1.36695,35.31388), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.36695,35.31388,-1.47472,35.30582), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.30833,35.33915,-1.36695,35.31388), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.27111,35.38582,8.30944,35.42721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.30944,35.42721,-1.27111,35.38582), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.36222,35.52193,-1.1825,35.57777), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-1.1825,35.57777,8.36222,35.52193), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.34027,35.67748,-0.64222,35.71249), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.64222,35.71249,8.34027,35.67748), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.27361,35.7586,-0.79972,35.77221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.79972,35.77221,-0.52111,35.77666), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.52111,35.77666,-0.79972,35.77221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.09556,35.78777,-0.52111,35.77666), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.27667,35.82221,-0.48056,35.8461), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.48056,35.8461,-0.27667,35.82221), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.04722,35.87693,-0.47889,35.88388), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.47889,35.88388,0.04722,35.87693), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((-0.33778,35.89749,8.26278,35.90109), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.26278,35.90109,-0.33778,35.89749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.1325,36.05138,0.29306,36.13443), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.29306,36.13443,0.33944,36.19666), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.33944,36.19666,0.29306,36.13443), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.88667,36.38776,8.37361,36.4447), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.37361,36.4447,0.95,36.45026), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((0.95,36.45026,8.37361,36.4447), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.19083,36.49165,1.22694,36.50638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.22694,36.50638,8.19083,36.49165), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.18361,36.52415,1.22694,36.50638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((1.86556,36.5661,8.23139,36.56721), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.23139,36.56721,1.86556,36.5661), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.57889,36.59026,2.41,36.59332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.41,36.59332,2.57889,36.59026), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.30832,36.62975,2.37083,36.63055), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.37083,36.63055,2.30832,36.62975), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.33444,36.64138,2.37083,36.63055), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.44305,36.6547,5.195,36.66082), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.195,36.66082,8.44305,36.6547), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.77944,36.67721,5.195,36.66082), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.09111,36.70915,8.47528,36.7172), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.47528,36.7172,5.09111,36.70915), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.13833,36.73832,8.47166,36.75304), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.47166,36.75304,3.21111,36.75555), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.21111,36.75555,8.47166,36.75304), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.42111,36.76443,3.21111,36.75555), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.10222,36.77499,3.50889,36.77583), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.50889,36.77583,5.10222,36.77499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.32389,36.78054,3.50889,36.77583), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.42389,36.79054,2.89444,36.79249), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((2.89444,36.79249,8.42389,36.79054), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.02417,36.80804,3.23444,36.81248), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.23444,36.81248,5.82583,36.81388), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.82583,36.81388,3.23444,36.81248), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((5.71639,36.82499,8.65,36.83526), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.65,36.83526,5.71639,36.82499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.95528,36.84637,8.65,36.83526), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.81,36.86193,8.65722,36.86749), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.65722,36.86749,7.81,36.86193), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.95,36.88332,6.135,36.88416), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.135,36.88416,7.77056,36.88443), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.77056,36.88443,6.135,36.88416), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((4.6525,36.89165,3.98417,36.89332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.98417,36.89332,8.61639,36.89499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((3.76639,36.89332,8.61639,36.89499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.61639,36.89499,3.98417,36.89332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.48028,36.89971,8.61639,36.89499), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.17861,36.92638,6.85028,36.92999), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.85028,36.92999,7.17861,36.92638), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.63023,36.94299,6.26305,36.9461), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.26305,36.9461,8.63023,36.94299), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((8.22917,36.95527,6.26305,36.9461), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.59278,36.9736,7.62583,36.97443), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.62583,36.97443,6.59278,36.9736), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.8,36.98444,6.25111,36.98804), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.25111,36.98804,7.8,36.98444), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.2575,37.00916,6.25111,36.98804), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.40778,37.04305,7.48912,37.05353), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.48912,37.05353,7.40778,37.04305), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.50916,37.07526,7.18694,37.08082), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.18694,37.08082,7.39028,37.08332), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((7.39028,37.08332,6.37917,37.0836), mapfile, tile_dir, 0, 11, "dz-algeria")
	render_tiles((6.37917,37.0836,7.39028,37.08332), mapfile, tile_dir, 0, 11, "dz-algeria")