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
    # Region: MR
    # Region Name: Mauritania

	render_tiles((-12.05762,14.72555,-12.23083,14.76194), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.23083,14.76194,-11.97444,14.77111), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.97444,14.77111,-12.24575,14.77217), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.24575,14.77217,-11.97444,14.77111), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.13695,14.77777,-12.24575,14.77217), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.43083,14.88778,-11.80417,14.90805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.80417,14.90805,-12.43083,14.88778), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.47889,15.00889,-11.80278,15.04861), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.80278,15.04861,-11.845,15.05916), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.845,15.05916,-11.80278,15.04861), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.89882,15.10682,-10.9225,15.13833), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.9225,15.13833,-12.77853,15.14678), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.77853,15.14678,-10.9225,15.13833), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.84972,15.20805,-12.78833,15.20861), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.78833,15.20861,-12.84972,15.20805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.89361,15.26166,-12.84389,15.27028), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.84389,15.27028,-10.82722,15.27555), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.82722,15.27555,-12.84389,15.27028), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.84583,15.30805,-10.7625,15.31277), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.7625,15.31277,-12.84583,15.30805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.9325,15.36805,-10.11806,15.37277), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.11806,15.37277,-9.81778,15.37388), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.81778,15.37388,-11.1725,15.37472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.1725,15.37472,-9.81778,15.37388), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.16445,15.40361,-9.69667,15.43), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.69667,15.43,-10.71901,15.43814), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-10.71901,15.43814,-9.41139,15.44389), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.41139,15.44389,-10.71901,15.43814), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.06028,15.47916,-5.49562,15.49846), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.49562,15.49846,-9.33361,15.49972), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.33361,15.49972,-5.49562,15.49846), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.90556,15.50333,-7.62611,15.50361), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-7.62611,15.50361,-5.90556,15.50333), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.96472,15.50611,-7.62611,15.50361), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.10028,15.50889,-12.96472,15.50611), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.64222,15.52416,-13.10028,15.50889), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.71083,15.54778,-11.64222,15.52416), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.0925,15.58333,-9.4425,15.59694), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.4425,15.59694,-13.0925,15.58333), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.31944,15.63416,-11.42083,15.63583), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.42083,15.63583,-9.31944,15.63416), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.24278,15.63861,-11.42083,15.63583), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-11.51222,15.64666,-13.24278,15.63861), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-9.33805,15.70472,-11.51222,15.64666), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.49222,16.05222,-13.39555,16.05527), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.39555,16.05527,-16.49222,16.05222), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.52727,16.07796,-16.50695,16.09416), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.50695,16.09416,-13.46056,16.09499), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.46056,16.09499,-16.50695,16.09416), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.67722,16.09888,-13.46056,16.09499), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.53056,16.11666,-13.85166,16.11832), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.85166,16.11832,-16.53056,16.11666), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.49528,16.14972,-16.46889,16.18055), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.46889,16.18055,-13.71111,16.18499), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.71111,16.18499,-16.46889,16.18055), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.87528,16.19805,-13.71111,16.18499), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.38805,16.21999,-13.97361,16.23721), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.97361,16.23721,-16.38805,16.21999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.96694,16.27805,-13.97361,16.23721), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.33528,16.32805,-13.96694,16.27805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.98583,16.48999,-16.3,16.5036), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.3,16.5036,-5.60167,16.50777), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.60167,16.50777,-16.3,16.5036), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.55,16.51249,-5.60167,16.50777), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.225,16.54777,-16.14611,16.55194), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.14611,16.55194,-14.225,16.54777), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.23917,16.55888,-16.14611,16.55194), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.33556,16.57694,-15.46889,16.57972), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.46889,16.57972,-14.33556,16.57694), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.10806,16.58611,-15.46889,16.57972), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.0875,16.61444,-15.04861,16.63027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.04861,16.63027,-14.33722,16.63249), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.33722,16.63249,-15.04861,16.63027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.86028,16.63832,-14.37778,16.63999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.37778,16.63999,-14.86028,16.63832), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.11694,16.64833,-14.37778,16.63999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.09778,16.67111,-16.44445,16.67972), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.44445,16.67972,-15.09778,16.67111), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-14.98111,16.69305,-16.44445,16.67972), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.26917,17.04638,-14.98111,16.69305), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.08611,17.52499,-16.03611,17.85138), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.03611,17.85138,-16.08611,17.52499), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.78722,18.18694,-16.06084,18.44638), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.06084,18.44638,-5.78722,18.18694), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.20695,18.97721,-16.32917,19.17916), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.32917,19.17916,-16.47111,19.26472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.47111,19.26472,-16.32917,19.17916), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.4325,19.37555,-16.53278,19.38694), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.53278,19.38694,-16.4325,19.37555), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.44056,19.40888,-16.46861,19.41055), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.46861,19.41055,-16.44056,19.40888), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.46889,19.45444,-16.38972,19.46472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.38972,19.46472,-16.3075,19.4725), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.3075,19.4725,-16.43056,19.47527), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.43056,19.47527,-16.3075,19.4725), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.34972,19.54333,-16.29528,19.54416), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.29528,19.54416,-16.34972,19.54333), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.265,19.73138,-5.97444,19.86638), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.97444,19.86638,-16.29167,19.89472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.29167,19.89472,-16.25222,19.89999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.25222,19.89999,-16.29167,19.89472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.21695,20.00638,-16.25222,19.89999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.26,20.13194,-16.22695,20.14444), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.22695,20.14444,-16.26,20.13194), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.19722,20.22916,-16.22695,20.14444), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.29111,20.33138,-16.19722,20.22916), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.54333,20.56055,-16.5775,20.58999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.5775,20.58999,-16.54333,20.56055), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.44,20.66777,-16.49084,20.69305), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.49084,20.69305,-16.44,20.66777), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.52917,20.73305,-17.05435,20.77007), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-17.05435,20.77007,-16.52917,20.73305), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.72111,20.81055,-17.05435,20.77007), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-17.02361,20.85666,-17.04889,20.89583), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-17.04889,20.89583,-17.05435,20.91793), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-17.05435,20.91793,-17.04889,20.89583), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.9975,20.96999,-17.05435,20.91793), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-17.015,21.04361,-16.9975,20.96999), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.92639,21.16055,-17.015,21.04361), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-15.17028,21.33694,-13,21.33805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13,21.33805,-16.95305,21.33833), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-16.95305,21.33833,-13,21.33805), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.39555,21.34027,-16.95305,21.33833), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-6.16667,21.52805,-13.39555,21.34027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.07917,22.51055,-13.15028,22.7575), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.15028,22.7575,-13.10555,22.89305), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-13.10555,22.89305,-12.99861,23.02472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.99861,23.02472,-6.35666,23.14602), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-6.35666,23.14602,-12.99861,23.02472), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.59639,23.27643,-6.37139,23.28971), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-6.37139,23.28971,-12.59639,23.27643), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.00056,23.45444,-6.37139,23.28971), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-6.5775,24.99916,-4.80639,25.00027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-4.80639,25.00027,-6.5775,24.99916), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-5.14806,25.21277,-4.80639,25.00027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-8.76722,25.99971,-12.00083,26), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-12.00083,26,-8.66722,26.00027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-8.66722,26.00027,-12.00083,26), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-8.66722,26.00027,-12.00083,26), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-6.89722,26.26749,-8.66722,26.00027), mapfile, tile_dir, 0, 11, "mr-mauritania")
	render_tiles((-8.66929,27.2802,-6.89722,26.26749), mapfile, tile_dir, 0, 11, "mr-mauritania")