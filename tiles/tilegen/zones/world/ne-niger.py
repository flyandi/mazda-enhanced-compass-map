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
    # Region: NE
    # Region Name: Niger

	render_tiles((3.60549,11.69169,3.68833,11.74972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.68833,11.74972,3.55889,11.75694), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.55889,11.75694,3.68833,11.74972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.46889,11.85583,2.40156,11.88988), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.40156,11.88988,3.31222,11.89), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.31222,11.89,2.40156,11.88988), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.41,11.90444,3.31222,11.89), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.61694,11.91972,2.39028,11.93333), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.39028,11.93333,3.61694,11.91972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.67167,11.97555,2.47056,11.97777), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.47056,11.97777,3.67167,11.97555), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.25389,12.01722,2.44528,12.02416), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.44528,12.02416,3.25389,12.01722), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.19555,12.06388,2.44528,12.02416), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.38361,12.16639,2.37806,12.24027), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.37806,12.24027,2.68639,12.29028), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.68639,12.29028,2.64528,12.30472), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.64528,12.30472,2.68639,12.29028), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.05833,12.35722,2.77111,12.37749), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.77111,12.37749,2.07778,12.38611), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.07778,12.38611,2.8435,12.39332), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.8435,12.39332,2.07778,12.38611), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.265,12.425,2.8435,12.39332), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.65778,12.52888,1.87167,12.60888), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.87167,12.60888,1.57833,12.63), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.57833,12.63,2.20083,12.63083), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.20083,12.63083,1.57833,12.63), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((2.09611,12.72555,1.98889,12.73111), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.98889,12.73111,2.09611,12.72555), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.95278,12.74888,1.98889,12.73111), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((9.63527,12.80277,8.985,12.84666), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.985,12.84666,9.63527,12.80277), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.66361,12.94333,7.09056,12.99527), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((7.09056,12.99527,4.105,12.99638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.105,12.99638,6.93333,12.99722), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.93333,12.99722,4.105,12.99638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.11722,13.01111,6.93333,12.99722), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.98917,13.04722,12.47805,13.05666), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.47805,13.05666,8.43303,13.06478), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.43303,13.06478,8.55444,13.06667), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.55444,13.06667,8.43303,13.06478), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.37361,13.07333,8.55444,13.06667), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.4975,13.08583,12.37361,13.07333), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((7.37972,13.09972,12.14693,13.10119), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.14693,13.10119,7.37972,13.09972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.80639,13.10805,12.245,13.11027), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.245,13.11027,6.80639,13.10805), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((7.21889,13.12555,9.93,13.13305), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((9.93,13.13305,7.21889,13.12555), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.86722,13.24972,10.14833,13.25972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((10.14833,13.25972,11.86722,13.24972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.67055,13.27806,10.14833,13.25972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.62916,13.30055,8.12222,13.30361), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.12222,13.30361,12.62916,13.30055), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.12667,13.32861,6.67917,13.34389), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.67917,13.34389,7.815,13.35278), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((7.815,13.35278,11.03833,13.36027), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.03833,13.36027,1.2175,13.36361), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.2175,13.36361,11.03833,13.36027), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.99167,13.37166,1.01167,13.37277), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.01167,13.37277,0.99167,13.37166), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.44638,13.37639,1.01167,13.37277), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((10.84111,13.38611,11.44638,13.37639), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.07222,13.44638,12.8625,13.45028), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.8625,13.45028,1.07222,13.44638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.1425,13.47694,4.24778,13.48138), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.24778,13.48138,4.1425,13.47694), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((12.96861,13.51278,4.24778,13.48138), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.99028,13.57027,13.25139,13.58888), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.25139,13.58888,6.42306,13.60527), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.42306,13.60527,13.25139,13.58888), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.16083,13.64305,0.77444,13.64417), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.77444,13.64417,6.16083,13.64305), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.32361,13.67944,6.23805,13.68333), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.23805,13.68333,6.285,13.68389), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((6.285,13.68389,6.23805,13.68333), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.625,13.68472,6.285,13.68389), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.47,13.68694,0.77278,13.68833), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.77278,13.68833,4.47,13.68694), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.62524,13.71821,13.34778,13.72), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.34778,13.72,13.62524,13.71821), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.92361,13.73638,5.28611,13.75222), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((5.28611,13.75222,4.92361,13.73638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.88555,13.78139,5.28611,13.75222), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((5.365,13.8475,5.54747,13.89313), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((5.54747,13.89313,5.365,13.8475), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.56238,13.99192,0.385,14.04917), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.385,14.04917,13.56238,13.99192), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.40528,14.25416,13.46222,14.42805), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.46222,14.42805,0.18944,14.46472), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.18944,14.46472,13.47555,14.46833), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.47555,14.46833,0.18944,14.46472), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.16667,14.52305,13.66528,14.54194), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.66528,14.54194,0.16667,14.52305), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.68416,14.5725,13.66528,14.54194), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.66083,14.64583,13.68416,14.5725), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.79416,14.73277,0.24139,14.75222), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.24139,14.75222,13.79416,14.73277), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.19333,14.83583,13.76722,14.84805), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.76722,14.84805,0.19333,14.83583), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.23722,14.8875,0.23453,14.91561), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.23453,14.91561,0.695,14.94222), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.695,14.94222,0.39667,14.96055), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.39667,14.96055,0.7425,14.96749), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.7425,14.96749,0.39667,14.96055), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.97472,14.97861,0.7425,14.96749), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.22639,15.00076,0.51222,15.00083), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((0.51222,15.00083,0.22639,15.00076), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.85916,15.03778,0.51222,15.00083), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((1.3125,15.28666,3.52482,15.35935), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.52482,15.35935,3.02194,15.37638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.02194,15.37638,3.52482,15.35935), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.0325,15.43305,3.02194,15.37638), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.53778,15.49249,3.0325,15.43305), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.7225,15.65,3.84417,15.67277), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.84417,15.67277,3.7225,15.65), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.89028,15.71805,14.36889,15.73388), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((14.36889,15.73388,3.89028,15.71805), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.92444,15.90416,4.00083,15.98972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.00083,15.98972,3.98083,16.07027), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((3.98083,16.07027,4.00083,15.98972), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.07444,16.30333,4.20083,16.39388), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.20083,16.39388,4.07444,16.30333), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.48861,16.90027,4.21417,16.9936), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.21417,16.9936,4.2525,16.99443), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.2525,16.99443,4.21417,16.9936), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.52404,17.33394,4.2525,16.99443), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.59249,18.60444,4.24555,18.66055), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.24555,18.66055,15.59249,18.60444), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((4.24509,19.14619,5.8125,19.4461), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((5.8125,19.4461,4.24509,19.14619), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.75389,19.93249,15.99666,20.35305), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.99666,20.35305,15.75389,19.93249), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.56861,20.77972,7.45449,20.85213), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((7.45449,20.85213,15.55833,20.88444), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.55833,20.88444,7.45449,20.85213), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.58444,20.92999,15.62722,20.95527), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.62722,20.95527,15.58444,20.92999), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.28444,21.44527,15.2025,21.49582), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.2025,21.49582,15.28444,21.44527), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((8.89444,21.72221,15.2025,21.49582), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.19472,21.99888,8.89444,21.72221), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((10.36083,22.5886,14.235,22.61416), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((14.235,22.61416,10.36083,22.5886), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((15.00063,23.00037,13.59555,23.13943), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.59555,23.13943,13.41722,23.21471), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((13.41722,23.21471,13.59555,23.13943), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.84444,23.44194,11.98645,23.52232), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.98645,23.52232,11.84444,23.44194), mapfile, tile_dir, 0, 11, "ne-niger")
	render_tiles((11.98645,23.52232,11.84444,23.44194), mapfile, tile_dir, 0, 11, "ne-niger")