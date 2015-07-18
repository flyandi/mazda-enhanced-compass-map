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
    # Region: ML
    # Region Name: Mali

	render_tiles((-7.01056,10.14194,-7.97768,10.16547), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.97768,10.16547,-6.95,10.17194), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.95,10.17194,-7.88528,10.17666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.88528,10.17666,-6.95,10.17194), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.0625,10.19111,-7.88528,10.17666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.98333,10.20861,-6.94611,10.21472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.94611,10.21472,-5.98333,10.20861), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.19306,10.23388,-7.94056,10.24333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.94056,10.24333,-7.34583,10.25027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.34583,10.25027,-6.98222,10.25361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.98222,10.25361,-7.26972,10.25444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.26972,10.25444,-6.98222,10.25361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.22325,10.25848,-7.26972,10.25444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.88944,10.27583,-5.95611,10.28444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.95611,10.28444,-5.88944,10.27583), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.22694,10.31416,-7.985,10.33777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.985,10.33777,-6.68946,10.3395), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.43083,10.33777,-6.68946,10.3395), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.68946,10.3395,-7.985,10.33777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.36,10.35083,-6.94389,10.35305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.94389,10.35305,-7.36,10.35083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.09389,10.35638,-6.16833,10.35861), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.16833,10.35861,-8.09389,10.35638), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.87806,10.37555,-6.77611,10.37749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.77611,10.37749,-5.87806,10.37555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.69972,10.40639,-6.63806,10.40944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.63806,10.40944,-7.69972,10.40639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.53472,10.41972,-8.21556,10.42166), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.21556,10.42166,-7.53472,10.41972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.52066,10.43099,-8.12222,10.43722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.12222,10.43722,-5.52066,10.43099), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.45667,10.45083,-5.65028,10.45111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.65028,10.45111,-7.45667,10.45083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.50028,10.46139,-5.65028,10.45111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.21028,10.50972,-6.24667,10.51639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.24667,10.51639,-6.21028,10.50972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.28222,10.54944,-6.41833,10.55055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.41833,10.55055,-8.28222,10.54944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.38194,10.59055,-6.67667,10.59833), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.67667,10.59833,-6.38194,10.59055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.58528,10.60722,-6.67667,10.59833), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.42028,10.62888,-6.18889,10.63027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.18889,10.63027,-6.42028,10.62888), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.4625,10.64555,-6.18889,10.63027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.64509,10.66679,-5.4625,10.64555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.41,10.69305,-6.64509,10.66679), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.2432,10.73495,-5.45639,10.74694), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.45639,10.74694,-6.2432,10.73495), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.41167,10.835,-5.45639,10.74694), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.67139,10.955,-8.60278,10.96416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.60278,10.96416,-8.67139,10.955), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.54417,10.97527,-8.60278,10.96416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.68278,10.99194,-8.28972,11.00777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.28972,11.00777,-8.68278,10.99194), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.48639,11.03028,-8.28972,11.00777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.35111,11.05722,-8.48889,11.05889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.48889,11.05889,-8.35111,11.05722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.48861,11.07638,-8.48889,11.05889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.33028,11.11472,-5.2975,11.14416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.2975,11.14416,-5.33028,11.11472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.29722,11.20694,-8.515,11.22639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.515,11.22639,-5.25083,11.24472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.25083,11.24472,-8.515,11.22639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.37611,11.28194,-8.47472,11.29111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.47472,11.29111,-8.37611,11.28194), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.35472,11.32277,-8.41028,11.33527), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.41028,11.33527,-8.35472,11.32277), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.35965,11.37156,-5.25167,11.395), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.25167,11.395,-8.35965,11.37156), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.19861,11.41889,-8.51417,11.43027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.51417,11.43027,-5.19861,11.41889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.60778,11.47583,-8.53417,11.49389), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.53417,11.49389,-8.60778,11.47583), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.21472,11.5775,-5.28944,11.61666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.28944,11.61666,-5.21472,11.5775), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.70305,11.65889,-8.83167,11.66166), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.83167,11.66166,-8.70305,11.65889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.26361,11.74944,-5.33722,11.80027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.33722,11.80027,-5.34806,11.82527), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.34806,11.82527,-5.40222,11.83), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.40222,11.83,-5.34806,11.82527), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.65241,11.89351,-5.19389,11.90528), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.19389,11.90528,-10.65241,11.89351), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.25389,11.99611,-4.78472,12.00083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.78472,12.00083,-11.25389,11.99611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.79657,12.00806,-4.93639,12.00944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.93639,12.00944,-8.79657,12.00806), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.76111,12.02805,-11.15417,12.04055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.15417,12.04055,-8.90417,12.04444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.90417,12.04444,-11.15417,12.04055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.66438,12.062,-4.69861,12.06277), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.69861,12.06277,-4.66438,12.062), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.6725,12.06777,-4.69861,12.06277), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.51111,12.11611,-9.98694,12.12083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.98694,12.12083,-10.51111,12.11611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.6675,12.12778,-9.98694,12.12083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.53944,12.13972,-11.46278,12.14028), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.46278,12.14028,-4.53944,12.13972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.89944,12.17805,-10.33333,12.18527), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.33333,12.18527,-8.89944,12.17805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.56945,12.20111,-11.495,12.20611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.89222,12.20111,-11.495,12.20611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.495,12.20611,-11.04528,12.20722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.04528,12.20722,-11.495,12.20611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.27306,12.20944,-11.04528,12.20722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.97994,12.22371,-10.32917,12.22416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.32917,12.22416,-8.97994,12.22371), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.46667,12.24861,-9.34544,12.2498), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.34544,12.2498,-9.46667,12.24861), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.43,12.28722,-4.40722,12.29722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.40722,12.29722,-11.43,12.28722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.3875,12.31111,-4.40722,12.29722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.45694,12.33389,-8.94667,12.35305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-8.94667,12.35305,-9.29333,12.35555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.29333,12.35555,-8.94667,12.35305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.44111,12.36333,-9.29333,12.35555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.37333,12.40464,-4.43806,12.43444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.43806,12.43444,-9.05778,12.43666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.05778,12.43666,-4.43806,12.43444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.39889,12.44611,-9.05778,12.43666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.35833,12.46749,-9.36222,12.4875), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.36222,12.4875,-9.31131,12.50425), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.31131,12.50425,-9.36222,12.4875), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.36583,12.53333,-11.45194,12.55055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.45194,12.55055,-4.36583,12.53333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.4225,12.62361,-4.47417,12.65778), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.47417,12.65778,-11.4225,12.62361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.42861,12.71389,-4.26917,12.71639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.26917,12.71639,-11.42861,12.71389), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.46889,12.72583,-11.38333,12.72666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.38333,12.72666,-4.46889,12.72583), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.20722,12.76639,-11.38333,12.72666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.41222,12.91611,-11.36806,12.92889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.36806,12.92889,-4.20556,12.94027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.20556,12.94027,-11.36806,12.92889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.42,12.95944,-4.20556,12.94027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.37806,12.98805,-11.42,12.95944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.43472,13.07611,-4.33528,13.10999), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.33528,13.10999,-11.52778,13.13778), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.52778,13.13778,-4.33528,13.10999), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.3125,13.16639,-3.44139,13.16805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.44139,13.16805,-4.3125,13.16639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.54111,13.17889,-4.21889,13.18139), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.21889,13.18139,-3.54111,13.17889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.42333,13.1975,-4.21889,13.18139), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.24333,13.23444,-3.42333,13.1975), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.44112,13.27585,-3.2325,13.28805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.2325,13.28805,-3.44112,13.27585), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.12944,13.30639,-11.79833,13.31278), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.79833,13.31278,-4.12944,13.30639), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.79167,13.36166,-11.59778,13.36472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.59778,13.36472,-3.79167,13.36166), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.75694,13.37305,-11.88528,13.375), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.88528,13.375,-11.75694,13.37305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.93861,13.37805,-11.88528,13.375), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.96667,13.39889,-11.71972,13.41278), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.71972,13.41278,-4.02528,13.42611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.02528,13.42611,-11.71972,13.41278), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.86806,13.4575,-3.9025,13.45777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.9025,13.45777,-11.86806,13.4575), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.96417,13.50416,-3.9025,13.45777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.04944,13.6125,-2.96222,13.62583), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.96222,13.62583,-3.04944,13.6125), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.87861,13.65611,-12.05445,13.66055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.05445,13.66055,-3.07083,13.66083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.07083,13.66083,-12.05445,13.66055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.19019,13.6789,-3.07083,13.66083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.27111,13.70166,-12.08259,13.70828), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.08259,13.70828,-3.27111,13.70166), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.24889,13.71999,-2.90417,13.72166), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.90417,13.72166,-3.24889,13.71999), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.95278,13.80833,-2.88444,13.87749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.88444,13.87749,-11.9425,13.90444), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.9425,13.90444,-2.88444,13.87749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.015,14.0125,-2.83333,14.04139), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.83333,14.04139,-12.015,14.0125), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.10778,14.15139,-11.98083,14.16722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.98083,14.16722,-2.10778,14.15139), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.00694,14.18805,-11.98083,14.16722), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.03111,14.27888,-2.47278,14.28889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-2.47278,14.28889,-12.03111,14.27888), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.09833,14.30444,-2.47278,14.28889), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.09889,14.36666,-12.2025,14.40083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.2025,14.40083,-12.09889,14.36666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-1.98083,14.475,-1.67833,14.50055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-1.67833,14.50055,-1.98083,14.475), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.17861,14.6075,-12.14778,14.63944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.14778,14.63944,-12.17861,14.6075), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.05762,14.72555,-1.31889,14.72888), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-1.31889,14.72888,-12.05762,14.72555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.23083,14.76194,-11.97444,14.77111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.97444,14.77111,-12.24575,14.77217), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.24575,14.77217,-11.97444,14.77111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-1.07389,14.7775,-12.13695,14.77777), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-12.13695,14.77777,-1.07389,14.7775), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.80417,14.90805,0.23453,14.91561), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.23453,14.91561,-11.80417,14.90805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.695,14.94222,0.39667,14.96055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.39667,14.96055,0.7425,14.96749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.7425,14.96749,0.39667,14.96055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.97472,14.97861,0.7425,14.96749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.22639,15.00076,0.51222,15.00083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((0.51222,15.00083,0.22639,15.00076), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-0.38611,15.00555,0.51222,15.00083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.80278,15.04861,-11.845,15.05916), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.845,15.05916,-11.80278,15.04861), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-0.24611,15.07805,-0.725,15.08305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-0.725,15.08305,-0.24611,15.07805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-0.44389,15.08305,-0.24611,15.07805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.89882,15.10682,-0.725,15.08305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.9225,15.13833,-10.89882,15.10682), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.82722,15.27555,1.3125,15.28666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.3125,15.28666,-10.82722,15.27555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.7625,15.31277,1.3125,15.28666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.52482,15.35935,-10.11806,15.37277), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.11806,15.37277,-9.81778,15.37388), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.81778,15.37388,-11.1725,15.37472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.1725,15.37472,-9.81778,15.37388), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.02194,15.37638,-11.1725,15.37472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.16445,15.40361,-9.69667,15.43), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.69667,15.43,3.0325,15.43305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.0325,15.43305,-9.69667,15.43), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-10.71901,15.43814,3.0325,15.43305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.41139,15.44389,-10.71901,15.43814), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.53778,15.49249,-5.49562,15.49846), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.49562,15.49846,-9.33361,15.49972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.33361,15.49972,-5.49562,15.49846), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.90556,15.50333,-7.62611,15.50361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-7.62611,15.50361,-5.90556,15.50333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.64222,15.52416,-7.62611,15.50361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.71083,15.54778,-11.64222,15.52416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.4425,15.59694,-9.31944,15.63416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.31944,15.63416,-11.42083,15.63583), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.42083,15.63583,-9.31944,15.63416), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-11.51222,15.64666,3.7225,15.65), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.7225,15.65,-11.51222,15.64666), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.84417,15.67277,3.7225,15.65), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-9.33805,15.70472,3.89028,15.71805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.89028,15.71805,-9.33805,15.70472), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.92444,15.90416,4.00083,15.98972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.00083,15.98972,3.98083,16.07027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.98083,16.07027,4.00083,15.98972), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.07444,16.30333,-5.33528,16.32805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.33528,16.32805,4.07444,16.30333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.20083,16.39388,-5.33528,16.32805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.60167,16.50777,4.20083,16.39388), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.21417,16.9936,4.2525,16.99443), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.2525,16.99443,4.21417,16.9936), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.78722,18.18694,4.24555,18.66055), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.24555,18.66055,3.33194,18.97638), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.33194,18.97638,3.12139,19.1361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.12139,19.1361,4.24509,19.14619), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((4.24509,19.14619,3.12139,19.1361), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.15361,19.22305,4.24509,19.14619), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.27222,19.3736,3.24805,19.51221), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.24805,19.51221,3.21833,19.53222), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.21833,19.53222,3.24805,19.51221), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((3.235,19.81555,-5.97444,19.86638), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-5.97444,19.86638,3.235,19.81555), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.84639,19.97471,2.42167,20.05305), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.42167,20.05305,2.84639,19.97471), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.32361,20.19944,2.1025,20.22083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.1025,20.22083,1.90889,20.23138), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.90889,20.23138,2.1025,20.22083), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.21805,20.27916,2.17806,20.27944), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((2.17806,20.27944,2.21805,20.27916), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.81028,20.30111,1.88111,20.30138), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.88111,20.30138,1.81028,20.30111), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.66556,20.44221,1.66305,20.53611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.66305,20.53611,1.66556,20.44221), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.48333,20.63916,1.375,20.65749), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.375,20.65749,1.48333,20.63916), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.17889,20.73277,1.32667,20.73333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.32667,20.73333,1.17889,20.73277), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.16139,20.7611,1.32667,20.73333), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((1.16924,21.0999,1.16139,20.7611), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.16667,21.52805,-0.00236,21.82636), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-0.00236,21.82636,-6.16667,21.52805), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-1.42944,22.80694,-6.35666,23.14602), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.35666,23.14602,-6.37139,23.28971), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.37139,23.28971,-6.35666,23.14602), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-3.10028,23.91416,-6.37139,23.28971), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.5775,24.99916,-4.80639,25.00027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-6.5775,24.99916,-4.80639,25.00027), mapfile, tile_dir, 0, 11, "ml-mali")
	render_tiles((-4.80639,25.00027,-6.5775,24.99916), mapfile, tile_dir, 0, 11, "ml-mali")