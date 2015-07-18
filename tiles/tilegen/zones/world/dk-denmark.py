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
    # Region: DK
    # Region Name: Denmark

	render_tiles((9.95639,54.85805,9.87583,54.88082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.95639,54.85805,9.87583,54.88082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.87583,54.88082,9.89778,54.89915), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.07306,54.88082,9.89778,54.89915), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.89778,54.89915,9.87583,54.88082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.84222,54.93999,10.03,54.95055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.03,54.95055,9.76056,54.95638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.76056,54.95638,10.03,54.95055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.83611,54.97638,9.76056,54.95638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.63472,55.04499,9.63944,55.0586), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.63944,55.0586,9.63472,55.04499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.75417,55.08443,9.63944,55.0586), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.70361,54.72555,10.73833,54.73888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.70361,54.72555,10.73833,54.73888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.73833,54.73888,10.70361,54.72555), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.60333,54.83388,10.72666,54.88055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.72666,54.88055,10.81167,54.8861), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.81167,54.8861,10.72666,54.88055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.68111,54.90499,10.81167,54.8861), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.89639,55.12027,10.95889,55.14777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.95889,55.14777,10.89639,55.12027), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.46139,54.61888,11.79861,54.64582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.46139,54.61888,11.79861,54.64582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.79861,54.64582,11.46139,54.61888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.85833,54.68943,11.80528,54.70749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.80528,54.70749,11.85833,54.68943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.08972,54.74416,11.86167,54.7461), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.86167,54.7461,11.08972,54.74416), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.995,54.78277,11.07889,54.8061), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.07889,54.8061,11.58889,54.80804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.58889,54.80804,11.07889,54.8061), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.02111,54.81304,11.58889,54.80804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.1075,54.82249,11.78222,54.82749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.78222,54.82749,11.1075,54.82249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.56667,54.84026,11.78222,54.82749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.015,54.88471,11.64389,54.9061), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.64389,54.9061,11.015,54.88471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.05778,54.93499,11.23139,54.95693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.23139,54.95693,11.05778,54.93499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.65528,56.67416,8.77,56.6936), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.65528,56.67416,8.77,56.6936), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.77,56.6936,8.65528,56.67416), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.56389,56.73721,8.77,56.6936), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.86417,56.78693,8.54528,56.79054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.54528,56.79054,8.86417,56.78693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.66111,56.80971,8.84555,56.82443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.84555,56.82443,8.66111,56.80971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.89778,56.86832,8.64667,56.88388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.64667,56.88388,8.85833,56.88693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.85833,56.88693,8.64667,56.88388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.92833,56.9736,8.85833,56.88693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.50972,55.02832,10.74028,55.06721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.74028,55.06721,10.07028,55.08527), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.07028,55.08527,10.75805,55.10194), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.75805,55.10194,10.07028,55.08527), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.15444,55.12693,10.78806,55.14054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.78806,55.14054,10.15444,55.12693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.10833,55.18638,9.99806,55.1911), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.99806,55.1911,10.10833,55.18638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.90833,55.22665,9.99806,55.1911), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.83472,55.29388,10.78167,55.30193), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.78167,55.30193,10.83472,55.29388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.87333,55.32443,10.78167,55.30193), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.78194,55.37498,10.76333,55.38832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.76333,55.38832,9.84278,55.39526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.84278,55.39526,10.76333,55.38832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.42555,55.4386,10.66667,55.45165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.66667,55.45165,10.42222,55.46443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.42222,55.46443,10.66667,55.45165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.60028,55.48277,10.49083,55.4936), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.49083,55.4936,9.67833,55.49443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.67833,55.49443,10.49083,55.4936), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.74444,55.49666,9.67833,55.49443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.88333,55.50582,10.74444,55.49666), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.53583,55.52693,10.47694,55.52999), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.47694,55.52999,10.53583,55.52693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.80583,55.54499,10.47694,55.52999), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.61028,55.6111,10.29889,55.61665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.61028,55.6111,10.29889,55.61665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.29889,55.61665,10.61028,55.6111), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((15.07667,55.0011,14.89528,55.02554), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((15.07667,55.0011,14.89528,55.02554), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((14.89528,55.02554,15.07667,55.0011), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((15.15,55.08916,14.68583,55.0961), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((14.68583,55.0961,15.15,55.08916), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((15.13416,55.14416,14.68583,55.0961), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((14.69889,55.21499,14.93667,55.21582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((14.93667,55.21582,14.69889,55.21499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((14.77639,55.30027,14.93667,55.21582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.98,54.57027,11.92944,54.57277), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.92944,54.57277,11.98,54.57027), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.87194,54.65137,11.95861,54.66332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.95861,54.66332,11.87194,54.65137), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.97639,54.7061,11.95861,54.66332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.88361,54.74971,11.97639,54.7061), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.16944,54.83916,12.11055,54.88749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.11055,54.88749,11.74222,54.89138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.74222,54.89138,12.11055,54.88749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.00028,54.90249,11.74222,54.89138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.90417,54.91943,12.00028,54.90249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.70861,54.93665,11.96361,54.94221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.96361,54.94221,11.87778,54.94499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.87778,54.94499,11.96361,54.94221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.85611,54.95499,11.75083,54.96082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.75083,54.96082,11.85611,54.95499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.07194,54.9686,11.88361,54.97388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.88361,54.97388,12.07194,54.9686), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.91389,55.00471,12.17361,55.00499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.17361,55.00499,11.91389,55.00471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.84722,55.02026,12.17361,55.00499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.82333,55.04638,11.84722,55.02026), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.12306,55.0811,11.77917,55.08138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.77917,55.08138,12.12306,55.0811), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.61861,55.08305,11.77917,55.08138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.16889,55.08971,11.61861,55.08305), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.72389,55.10832,12.17333,55.12276), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.17333,55.12276,11.76694,55.13138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.76694,55.13138,12.06139,55.13165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.06139,55.13165,11.76694,55.13138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.72,55.15221,11.77361,55.15415), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.77361,55.15415,11.72,55.15221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.015,55.16138,11.77361,55.15415), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.06861,55.17387,12.015,55.16138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.74111,55.19888,11.24667,55.19971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.24667,55.19971,11.74111,55.19888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.23611,55.23138,11.24667,55.19971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.24194,55.26804,12.44333,55.27249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.44333,55.27249,11.24194,55.26804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.11055,55.33054,12.45278,55.3336), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.45278,55.3336,11.11055,55.33054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.17056,55.34971,11.10472,55.3636), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.10472,55.3636,11.17056,55.34971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.21472,55.3886,12.37667,55.39526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.37667,55.39526,11.21472,55.3886), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.22778,55.4261,12.37667,55.39526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.19194,55.46027,11.17805,55.48804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.17805,55.48804,11.08,55.50916), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.08,55.50916,11.13,55.52249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.13,55.52249,11.08,55.50916), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.2425,55.5411,11.15028,55.54971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.15028,55.54971,12.2425,55.5411), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.11972,55.60332,12.38389,55.6111), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.38389,55.6111,11.11972,55.60332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.51278,55.63527,12.05722,55.65332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.05722,55.65332,11.08583,55.65887), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.08583,55.65887,10.9275,55.66026), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.9275,55.66026,11.08583,55.65887), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.79444,55.66165,10.9275,55.66026), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.92916,55.67416,11.79444,55.66165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.99944,55.69527,11.16861,55.70277), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.16861,55.70277,12.60028,55.70415), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.60028,55.70415,11.16861,55.70277), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.09389,55.7111,11.83722,55.71804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.83722,55.71804,11.77028,55.71832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.77028,55.71832,11.83722,55.71804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.05083,55.72443,11.77028,55.71832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.12278,55.73166,10.87361,55.73249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.87361,55.73249,11.12278,55.73166), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.15333,55.74721,11.34333,55.74749), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.34333,55.74749,11.15333,55.74721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.62389,55.77554,11.73639,55.79305), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.73639,55.79305,11.37472,55.80471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.37472,55.80471,11.66444,55.8111), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.66444,55.8111,12.06472,55.81388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.06472,55.81388,11.72556,55.81443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.72556,55.81443,12.06472,55.81388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.02833,55.82249,11.72556,55.81443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.73055,55.83554,11.48694,55.84165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.48694,55.84165,11.95667,55.8461), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.95667,55.8461,11.48694,55.84165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.51583,55.89193,11.66833,55.89526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.66833,55.89526,11.51583,55.89193), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.99278,55.9011,11.91805,55.90166), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.91805,55.90166,11.99278,55.9011), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.67417,55.91444,11.74111,55.91582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.74111,55.91582,11.67417,55.91444), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.74111,55.91582,11.67417,55.91444), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.50778,55.91888,11.74111,55.91582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.92028,55.92971,11.87611,55.9361), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.87611,55.9361,11.60639,55.93638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.60639,55.93638,11.87611,55.9361), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.75778,55.93999,11.60639,55.93638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.53472,55.95388,12.0075,55.95943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.0075,55.95943,11.85361,55.96304), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.85361,55.96304,11.76389,55.96471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.76389,55.96471,11.85361,55.96304), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.27806,55.97527,11.32055,55.97832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((11.32055,55.97832,11.27806,55.97527), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.61806,56.03387,12.11167,56.07526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.11167,56.07526,12.51889,56.0861), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.51889,56.0861,12.11167,56.07526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((12.30167,56.1286,12.51889,56.0861), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.44517,54.82478,9.73722,54.82777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.73722,54.82777,9.44517,54.82478), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.75194,54.84805,9.50312,54.84825), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.50312,54.84825,9.75194,54.84805), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.50312,54.84825,9.75194,54.84805), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.61417,54.89054,9.76805,54.8911), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.76805,54.8911,9.61417,54.89054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.71944,54.8911,9.61417,54.89054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.70361,54.89221,9.76805,54.8911), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.92,54.90804,9.64805,54.91388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.64805,54.91388,8.6563,54.9174), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.6563,54.9174,9.64805,54.91388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.60194,54.9286,8.6563,54.9174), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.7275,54.98943,9.52472,55.02943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.52472,55.02943,9.43444,55.03471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.43444,55.03471,9.52472,55.02943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.59167,55.04555,9.43444,55.03471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.4875,55.06277,8.52222,55.07221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.52222,55.07221,8.4875,55.06277), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.55639,55.0836,8.52222,55.07221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.45694,55.12388,8.68222,55.12999), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.68222,55.12999,9.46611,55.13055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.46611,55.13055,8.68222,55.12999), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.66546,55.13359,9.46611,55.13055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.56028,55.14082,8.69139,55.14221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.69139,55.14221,8.56028,55.14082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.55444,55.14971,8.69139,55.14221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.48306,55.1911,8.56805,55.19332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.56805,55.19332,8.48306,55.1911), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.68861,55.19693,8.56805,55.19332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.71111,55.2611,9.68861,55.19693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.62111,55.35638,9.59694,55.37332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.59694,55.37332,9.62111,55.35638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.63805,55.40276,9.59694,55.37332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.59944,55.4411,8.44,55.45221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.44,55.45221,9.66583,55.45971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.66583,55.45971,8.44,55.45221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.32861,55.47332,9.64972,55.47721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.64972,55.47721,8.32861,55.47332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.26944,55.48415,9.57889,55.48693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.57889,55.48693,8.26944,55.48415), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.50305,55.49194,9.57889,55.48693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.09139,55.54471,8.23472,55.55666), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.23472,55.55666,9.75139,55.55971), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.75139,55.55971,8.23472,55.55666), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.26528,55.5736,8.3325,55.57555), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.3325,55.57555,8.26528,55.5736), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.84528,55.62276,9.81167,55.66943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.81167,55.66943,9.61666,55.69499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.61666,55.69499,10.01833,55.70776), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.01833,55.70776,9.61666,55.69499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.18222,55.72305,10.01833,55.70776), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.01167,55.75721,8.18222,55.72305), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.18639,55.80999,10.04861,55.81443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.04861,55.81443,8.18639,55.80999), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.19305,55.83305,10.15055,55.83499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.15055,55.83499,10.19305,55.83305), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.87389,55.83777,10.15055,55.83499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.88055,55.85165,9.87389,55.83777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.11305,55.87471,8.39417,55.89193), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.39417,55.89193,10.11305,55.87471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.16306,55.93694,8.39417,55.89193), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.12611,55.98305,8.14194,55.99638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.14194,55.99638,8.11389,56.00082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.11389,56.00082,8.14194,55.99638), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.27972,56.01888,8.11389,56.00082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.2625,56.0511,8.31111,56.05305), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.31111,56.05305,10.2625,56.0511), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.52417,56.0986,8.14611,56.1111), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.14611,56.1111,10.57555,56.11665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.57555,56.11665,8.10555,56.11804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.10555,56.11804,10.57555,56.11665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.49416,56.13499,10.73306,56.1486), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.73306,56.1486,10.21917,56.15166), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.21917,56.15166,10.52278,56.1536), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.52278,56.1536,10.21917,56.15166), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.40667,56.15721,10.52278,56.1536), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.66944,56.16554,10.40667,56.15721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.55528,56.17693,10.46639,56.17776), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.46639,56.17776,10.55528,56.17693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.35305,56.19527,10.46639,56.17776), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.69055,56.21777,10.62556,56.22832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.62556,56.22832,10.76056,56.2286), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.76056,56.2286,10.62556,56.22832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.82861,56.25888,10.50833,56.26499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.50833,56.26499,10.82861,56.25888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.40055,56.29082,8.13083,56.30832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.13083,56.30832,10.40055,56.29082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.96083,56.44499,8.62667,56.4786), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.62667,56.4786,8.72833,56.48221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.72833,56.48221,8.62667,56.4786), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.22611,56.48916,8.72833,56.48221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.84417,56.52388,9.3125,56.52554), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.3125,56.52554,10.84417,56.52388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.41472,56.52805,10.72333,56.5286), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.72333,56.5286,10.41472,56.52805), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.37111,56.54082,10.22694,56.54443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.22694,56.54443,9.37111,56.54082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.30194,56.54916,10.20944,56.55138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.20944,56.55138,8.30194,56.54916), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.29417,56.5586,9.06417,56.56304), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.76611,56.5586,9.06417,56.56304), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.06417,56.56304,9.37389,56.56693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.37389,56.56693,9.06417,56.56304), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.56722,56.57277,9.37389,56.56693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.36944,56.58027,8.555,56.58249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.25083,56.58027,8.555,56.58249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.555,56.58249,8.13722,56.58276), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.13722,56.58276,8.555,56.58249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.04389,56.59583,10.31917,56.59888), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.31917,56.59888,9.04389,56.59583), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.15194,56.61249,8.68083,56.61416), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.68083,56.61416,9.15194,56.61249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.33982,56.61678,8.68083,56.61416), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.28806,56.62054,10.33982,56.61678), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.60722,56.63055,9.24889,56.63332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.05528,56.63055,9.24889,56.63332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.24889,56.63332,8.60722,56.63055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.20389,56.63805,9.86444,56.63832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.86444,56.63832,8.20389,56.63805), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.36361,56.64499,9.86444,56.63832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.32972,56.65305,10.36361,56.64499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.18083,56.66943,8.51972,56.68471), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.51972,56.68471,8.23778,56.68777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.23778,56.68777,10.23472,56.6886), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.23472,56.6886,8.23778,56.68777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.19472,56.69138,10.23472,56.6886), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.33861,56.69527,8.19472,56.69138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.19055,56.69916,10.20639,56.70138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.20639,56.70138,9.30194,56.70221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.30194,56.70221,10.20639,56.70138), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.87722,56.70332,8.26472,56.7036), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.26472,56.7036,8.87722,56.70332), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.24028,56.70721,8.21667,56.71054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.21667,56.71054,8.52555,56.71165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.52555,56.71165,8.83833,56.71249), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.83833,56.71249,8.52555,56.71165), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.47472,56.71443,9.17333,56.71526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.17333,56.71526,8.47472,56.71443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.3425,56.71804,10.15944,56.72054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.15944,56.72054,10.3425,56.71804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.23944,56.73055,10.15944,56.72054), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.30528,56.74805,9.23944,56.73055), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.28444,56.76665,8.25147,56.77188), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.25147,56.77188,8.28444,56.76665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.48917,56.79027,8.24222,56.79443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.24222,56.79443,8.9375,56.79555), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.9375,56.79555,8.24222,56.79443), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.17333,56.79943,8.9375,56.79555), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.07,56.80777,9.17333,56.79943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.5725,56.81944,9.07,56.80777), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.21194,56.85693,9.16555,56.88943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.16555,56.88943,10.26917,56.91721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.26917,56.91721,9.16555,56.88943), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.67333,56.94721,8.74694,56.95277), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.74694,56.95277,8.67333,56.94721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.24361,56.96582,9.57889,56.96665), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.57889,56.96665,9.24361,56.96582), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.30972,56.98388,9.24389,56.99554), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.24389,56.99554,10.34389,56.9986), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.34389,56.9986,9.6625,57.00027), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.6625,57.00027,10.34389,56.9986), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.45055,57.00499,9.54667,57.00804), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.54667,57.00804,8.45055,57.00499), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.31805,57.01638,10.23152,57.01743), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.23152,57.01743,9.40972,57.01832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.40972,57.01832,10.23152,57.01743), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.06333,57.02249,9.40972,57.01832), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.68611,57.0386,9.57944,57.04276), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.57944,57.04276,9.68611,57.0386), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.11555,57.05276,9.91805,57.05554), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.91805,57.05554,9.11555,57.05276), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.98136,57.07787,10.02083,57.08693), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.02083,57.08693,9.98136,57.07787), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.58555,57.10082,8.75417,57.1011), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.75417,57.1011,8.58555,57.10082), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.80083,57.10249,8.75417,57.1011), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.05471,57.12386,8.62861,57.12527), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((8.62861,57.12527,9.05471,57.12386), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.23139,57.13721,8.62861,57.12527), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.39,57.1511,10.42389,57.15721), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.42389,57.15721,9.39,57.1511), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.54083,57.21387,10.54202,57.22952), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.54202,57.22952,9.54083,57.21387), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.53722,57.44999,10.44305,57.53526), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.44305,57.53526,9.94,57.57113), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((9.94,57.57113,10.4325,57.59221), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.4325,57.59221,9.94,57.57113), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.49139,57.65388,10.39055,57.6661), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.39055,57.6661,10.49139,57.65388), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.645,57.73749,10.55667,57.74416), mapfile, tile_dir, 0, 11, "dk-denmark")
	render_tiles((10.55667,57.74416,10.645,57.73749), mapfile, tile_dir, 0, 11, "dk-denmark")