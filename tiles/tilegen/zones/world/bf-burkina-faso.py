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
    # Region: BF
    # Region Name: Burkina Faso

	render_tiles((-2.745,9.39639,-2.79111,9.41333), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.79111,9.41333,-2.745,9.39639), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.68685,9.48234,-2.79111,9.41333), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.37695,9.5825,-4.31083,9.59833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.31083,9.59833,-4.37695,9.5825), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.94861,9.65111,-4.50917,9.65444), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.50917,9.65444,-4.42639,9.6575), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.42639,9.6575,-4.50917,9.65444), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.68555,9.67527,-4.57139,9.68694), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.57139,9.68694,-4.28722,9.69027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.28722,9.69027,-4.57139,9.68694), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.50861,9.71139,-4.60222,9.72138), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.60222,9.72138,-3.06556,9.725), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.06556,9.725,-2.98972,9.72777), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.98972,9.72777,-3.06556,9.725), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.79445,9.73583,-4.77528,9.73666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.77528,9.73666,-2.79445,9.73583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.53278,9.74666,-4.74222,9.75027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.74222,9.75027,-4.53278,9.74666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.78028,9.79083,-4.04083,9.79944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.04083,9.79944,-4.78028,9.79083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.73861,9.82611,-3.1175,9.82916), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.1175,9.82916,-4.12167,9.83), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.12167,9.83,-3.1175,9.82916), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.17944,9.84,-3.25917,9.84805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.25917,9.84805,-3.17944,9.84), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.30278,9.85805,-3.25917,9.84805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.92833,9.86805,-4.86,9.875), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.86,9.875,-4.92833,9.86805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.29695,9.90083,-4.97444,9.90528), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.97444,9.90528,-3.29695,9.90083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.19111,9.92389,-3.765,9.93027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.765,9.93027,-3.19111,9.92389), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.95,9.94861,-3.765,9.93027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.98,10.05361,-2.79639,10.05777), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.79639,10.05777,-4.98,10.05361), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.75472,10.265,-5.175,10.28944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.175,10.28944,-5.3225,10.29916), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.3225,10.29916,-5.41139,10.30111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.41139,10.30111,-5.3225,10.29916), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.13,10.30472,-5.41139,10.30111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.20806,10.3225,-2.84361,10.32638), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.84361,10.32638,-5.20806,10.3225), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.76861,10.41666,-5.52066,10.43099), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.52066,10.43099,-2.76861,10.41666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.85778,10.45722,-5.52066,10.43099), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.9325,10.63,-5.4625,10.64555), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.4625,10.64555,-2.9325,10.63), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.45639,10.74694,-2.88028,10.80416), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.88028,10.80416,-5.41167,10.835), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.41167,10.835,-2.88028,10.80416), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.61,10.91722,0.50417,10.93694), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.50417,10.93694,-0.665,10.95472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.665,10.95472,0.50417,10.93694), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.56778,10.99222,0.91744,10.99576), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.91744,10.99576,0.96778,10.99583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.96778,10.99583,0.91744,10.99576), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.67167,10.99666,0.96778,10.99583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.67972,10.9975,-0.48889,10.99833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.48889,10.99833,-0.67972,10.9975), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.23028,11.00027,-0.48889,10.99833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.50694,11.0025,-2.83106,11.00341), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.83106,11.00341,0.50694,11.0025), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.48639,11.03028,1.10278,11.04027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.10278,11.04027,-5.48639,11.03028), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.35278,11.06889,-5.48861,11.07638), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.48861,11.07638,0.97889,11.08027), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.97889,11.08027,-5.48861,11.07638), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.42306,11.09694,-0.38194,11.10861), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.38194,11.10861,-5.33028,11.11472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.33028,11.11472,-0.38194,11.10861), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.27528,11.13,-0.15096,11.13927), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.15096,11.13927,1.06389,11.13972), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.06389,11.13972,-0.15096,11.13927), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.2975,11.14416,1.06389,11.13972), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.15639,11.16278,-0.28528,11.16666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.28528,11.16666,1.15639,11.16278), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.29722,11.20694,-5.25083,11.24472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.25083,11.24472,1.13278,11.24944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.13278,11.24944,-5.25083,11.24472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.27278,11.25916,1.13278,11.24944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.15389,11.27722,1.30861,11.29139), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.30861,11.29139,1.15389,11.27722), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.60917,11.38833,-5.25167,11.395), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.25167,11.395,1.60917,11.38833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.19861,11.41889,2.01917,11.42527), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.01917,11.42527,1.76611,11.42666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.76611,11.42666,2.01917,11.42527), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.90268,11.42858,1.76611,11.42666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.3975,11.43972,1.90268,11.42858), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.5,11.46416,1.3975,11.43972), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.21472,11.5775,-5.28944,11.61666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.28944,11.61666,-5.21472,11.5775), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.31386,11.67916,-5.28944,11.61666), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.26361,11.74944,2.33805,11.76472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.33805,11.76472,-5.26361,11.74944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.33722,11.80027,-5.34806,11.82527), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.34806,11.82527,-5.40222,11.83), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.40222,11.83,-5.34806,11.82527), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.40156,11.88988,-5.19389,11.90528), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-5.19389,11.90528,2.40156,11.88988), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.78472,12.00083,-4.93639,12.00944), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.93639,12.00944,-4.78472,12.00083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.66438,12.062,-4.69861,12.06277), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.69861,12.06277,-4.66438,12.062), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.53944,12.13972,-4.56945,12.20111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.56945,12.20111,-4.53944,12.13972), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.40722,12.29722,-4.3875,12.31111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.3875,12.31111,-4.40722,12.29722), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.45694,12.33389,-4.3875,12.31111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.05833,12.35722,-4.45694,12.33389), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.07778,12.38611,2.05833,12.35722), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.265,12.425,-4.43806,12.43444), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.43806,12.43444,2.265,12.425), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.36583,12.53333,1.87167,12.60888), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.87167,12.60888,1.57833,12.63), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.57833,12.63,2.20083,12.63083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.20083,12.63083,1.57833,12.63), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.47417,12.65778,2.20083,12.63083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.26917,12.71639,2.09611,12.72555), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((2.09611,12.72555,-4.46889,12.72583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.46889,12.72583,2.09611,12.72555), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.98889,12.73111,-4.46889,12.72583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.20722,12.76639,1.98889,12.73111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.20556,12.94027,1.11722,13.01111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.11722,13.01111,0.98917,13.04722), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.98917,13.04722,1.11722,13.01111), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.33528,13.10999,-4.3125,13.16639), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.3125,13.16639,-3.44139,13.16805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.44139,13.16805,-4.3125,13.16639), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.54111,13.17889,-4.21889,13.18139), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.21889,13.18139,-3.54111,13.17889), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.42333,13.1975,-4.21889,13.18139), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.24333,13.23444,-3.42333,13.1975), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.44112,13.27585,-3.2325,13.28805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.2325,13.28805,-3.44112,13.27585), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.12944,13.30639,-3.2325,13.28805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.12667,13.32861,-4.12944,13.30639), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.79167,13.36166,1.2175,13.36361), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.2175,13.36361,-3.79167,13.36166), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.99167,13.37166,1.01167,13.37277), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.01167,13.37277,0.99167,13.37166), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.93861,13.37805,1.01167,13.37277), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.96667,13.39889,-3.93861,13.37805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-4.02528,13.42611,1.07222,13.44638), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((1.07222,13.44638,-3.9025,13.45777), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.9025,13.45777,1.07222,13.44638), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.96417,13.50416,-3.9025,13.45777), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.99028,13.57027,-3.04944,13.6125), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.04944,13.6125,-2.96222,13.62583), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.96222,13.62583,-3.04944,13.6125), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.96222,13.62583,-3.04944,13.6125), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.77444,13.64417,-2.87861,13.65611), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.87861,13.65611,-3.07083,13.66083), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.07083,13.66083,-2.87861,13.65611), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.19019,13.6789,0.625,13.68472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.625,13.68472,0.77278,13.68833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.77278,13.68833,0.625,13.68472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.27111,13.70166,0.77278,13.68833), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-3.24889,13.71999,-2.90417,13.72166), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.90417,13.72166,-3.24889,13.71999), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.88444,13.87749,-2.90417,13.72166), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.83333,14.04139,0.385,14.04917), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.385,14.04917,-2.83333,14.04139), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.10778,14.15139,-2.00694,14.18805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.00694,14.18805,-2.10778,14.15139), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.40528,14.25416,-2.47278,14.28889), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-2.47278,14.28889,0.40528,14.25416), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.18944,14.46472,-1.98083,14.475), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-1.98083,14.475,0.18944,14.46472), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-1.67833,14.50055,0.16667,14.52305), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.16667,14.52305,-1.67833,14.50055), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-1.31889,14.72888,0.24139,14.75222), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.24139,14.75222,-1.31889,14.72888), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-1.07389,14.7775,0.24139,14.75222), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.19333,14.83583,0.23722,14.8875), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.23722,14.8875,0.23453,14.91561), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((0.23453,14.91561,0.23722,14.8875), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.38611,15.00555,-0.24611,15.07805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.24611,15.07805,-0.725,15.08305), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.725,15.08305,-0.24611,15.07805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")
	render_tiles((-0.44389,15.08305,-0.24611,15.07805), mapfile, tile_dir, 0, 11, "bf-burkina-faso")