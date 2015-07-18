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
    # Region: GA
    # Region Name: Gabon

	render_tiles((11.14151,-3.9229,11.88472,-3.69917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.88472,-3.69917,11.69472,-3.69639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.69472,-3.69639,11.88472,-3.69917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.24472,-3.67861,11.69472,-3.69639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.93027,-3.64333,11.925,-3.62667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.925,-3.62667,10.93027,-3.64333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.43917,-3.585,11.84222,-3.57944), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.84222,-3.57944,11.43917,-3.585), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.84138,-3.55139,11.84222,-3.57944), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.47833,-3.52139,11.56055,-3.51722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.56055,-3.51722,11.47833,-3.52139), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.63888,-3.41583,11.975,-3.32222), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.975,-3.32222,10.62972,-3.30889), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.62972,-3.30889,11.975,-3.32222), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.95805,-3.29139,10.62972,-3.30889), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.69972,-3.16694,10.40889,-3.08222), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.40889,-3.08222,11.76611,-3.04722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.76611,-3.04722,11.73194,-3.03778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.73194,-3.03778,11.76611,-3.04722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.80166,-3.00889,11.73194,-3.03778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.56472,-2.86722,11.54333,-2.85695), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.54333,-2.85695,11.56472,-2.86722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.6325,-2.81861,11.54611,-2.79389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.54611,-2.79389,10.03944,-2.77833), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.03944,-2.77833,11.54611,-2.79389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.61194,-2.71583,10.03944,-2.77833), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.97722,-2.62833,11.6475,-2.61333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.6475,-2.61333,9.97722,-2.62833), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.095,-2.58583,10.08611,-2.56417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.08611,-2.56417,10.15972,-2.56056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.15972,-2.56056,9.92722,-2.55778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.92722,-2.55778,10.15972,-2.56056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.95916,-2.55028,9.92722,-2.55778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.00416,-2.53139,10.03639,-2.51361), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.03639,-2.51361,9.83278,-2.50528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.83278,-2.50528,10.03639,-2.51361), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.08777,-2.495,10.07611,-2.49333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.07611,-2.49333,14.08777,-2.495), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.87888,-2.47639,9.75879,-2.46787), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.75879,-2.46787,13.87888,-2.47639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.12833,-2.45444,9.70083,-2.44556), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.70083,-2.44556,9.72361,-2.43917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.72361,-2.43917,11.73527,-2.43667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.73527,-2.43667,13.48694,-2.43528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.48694,-2.43528,11.73527,-2.43667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.69028,-2.43139,13.48694,-2.43528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.86444,-2.42306,9.69028,-2.43139), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.87444,-2.41195,9.74028,-2.40722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.74028,-2.40722,9.87444,-2.41195), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.77166,-2.39639,12.02166,-2.39139), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.02166,-2.39139,11.77166,-2.39639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.69833,-2.37667,12.02166,-2.39139), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.93027,-2.35778,11.68111,-2.35667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.68111,-2.35667,13.93027,-2.35778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.01944,-2.34778,14.255,-2.34222), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.255,-2.34222,13.01944,-2.34778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.96416,-2.33333,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.57417,-2.33333,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.47805,-2.32722,9.58575,-2.3264), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.58575,-2.3264,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.01111,-2.29556,9.58575,-2.3264), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.15833,-2.22861,9.57194,-2.20917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.57194,-2.20917,12.97277,-2.19083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.97277,-2.19083,14.22555,-2.19028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.22555,-2.19028,12.97277,-2.19083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.72583,-2.18611,14.22555,-2.19028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.72722,-2.15111,12.90444,-2.14694), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.90444,-2.14694,13.72722,-2.15111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.77055,-2.09306,9.42305,-2.06111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.51722,-2.09306,9.42305,-2.06111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.42305,-2.06111,9.53055,-2.05028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.53055,-2.05028,9.42305,-2.06111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.44805,-2.02445,9.53055,-2.05028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.26444,-1.96611,12.84527,-1.95444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.84527,-1.95444,14.26444,-1.96611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.49527,-1.92389,9.47139,-1.92306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.47139,-1.92306,12.49527,-1.92389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.52472,-1.90528,14.42972,-1.89167), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.42972,-1.89167,12.52472,-1.90528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.45277,-1.87722,9.32949,-1.87459), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.32949,-1.87459,12.52055,-1.8725), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.52055,-1.8725,9.32949,-1.87459), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.45,-1.86695,9.48528,-1.86417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.48528,-1.86417,9.45,-1.86695), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.27278,-1.86083,9.48528,-1.86417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.2575,-1.84611,9.30805,-1.83444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.30805,-1.83444,12.655,-1.82445), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.655,-1.82445,9.37972,-1.8225), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.37972,-1.8225,12.655,-1.82445), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.42139,-1.81278,9.37972,-1.8225), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.46666,-1.68056,9.28444,-1.67722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.28444,-1.67722,14.46666,-1.68056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.41666,-1.67222,9.28444,-1.67722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.39472,-1.61556,9.23055,-1.61472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.23055,-1.61472,9.39472,-1.61556), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.55721,-1.61262,14.38389,-1.61083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.38389,-1.61083,9.55721,-1.61262), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.42166,-1.60583,14.38389,-1.61083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.49916,-1.58389,14.43389,-1.57861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.43389,-1.57861,9.49916,-1.58389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.28472,-1.56722,9.38222,-1.56583), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.38222,-1.56583,9.28472,-1.56722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.26139,-1.53722,9.4825,-1.51333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.4825,-1.51333,9.14361,-1.49639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.14361,-1.49639,9.4825,-1.51333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.24777,-1.47361,9.445,-1.46778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.445,-1.46778,9.29055,-1.46667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.29055,-1.46667,9.445,-1.46778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.12278,-1.42778,9.21916,-1.4125), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.21916,-1.4125,9.12278,-1.42778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.50166,-1.39028,9.14722,-1.38333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.14722,-1.38333,14.50166,-1.39028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.32944,-1.36806,9.11416,-1.35833), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.11416,-1.35833,9.08555,-1.35083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.08555,-1.35083,9.15778,-1.34778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.15778,-1.34778,9.08555,-1.35083), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.35639,-1.33861,14.44416,-1.33194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.44416,-1.33194,9.35639,-1.33861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.03488,-1.28326,14.44416,-1.33194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.98306,-1.23417,14.48194,-1.21278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.48194,-1.21278,8.98306,-1.23417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.03139,-1.18945,14.48194,-1.21278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.91861,-1.045,14.41639,-1.03345), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.41639,-1.03345,8.91861,-1.045), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.92055,-1.00472,14.41639,-1.03345), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.87472,-0.97389,8.92055,-1.00472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.00611,-0.87944,8.86055,-0.80417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.86055,-0.80417,8.79556,-0.74694), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.79556,-0.74694,9.05611,-0.70306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.05611,-0.70306,8.93611,-0.69861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.93611,-0.69861,9.05611,-0.70306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.11333,-0.68611,8.90027,-0.67917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.90027,-0.67917,9.11333,-0.68611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.7775,-0.63056,8.70639,-0.62444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.70639,-0.62444,8.7775,-0.63056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.51861,-0.60917,9.09889,-0.60472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.09889,-0.60472,14.51861,-0.60917), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((8.71194,-0.57111,9.09889,-0.60472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.45666,-0.51861,8.71194,-0.57111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.1775,-0.4525,14.30666,-0.43333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.30666,-0.43333,14.1775,-0.4525), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.16083,-0.35778,9.30694,-0.3525), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.30694,-0.3525,14.16083,-0.35778), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.12083,-0.27611,13.88694,-0.25306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.88694,-0.25306,14.12083,-0.27611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.85222,-0.1675,9.34555,-0.08361), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.34555,-0.08361,13.85222,-0.1675), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.81083,0.01806,13.95,0.03333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.95,0.03333,13.91333,0.0425), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.91333,0.0425,13.95,0.03333), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.49527,0.09806,9.47361,0.10667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.47361,0.10667,9.58805,0.11278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.58805,0.11278,9.47361,0.10667), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.76083,0.12694,9.58805,0.11278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.64639,0.14833,9.48944,0.16444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.48944,0.16444,9.9825,0.17417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.9825,0.17417,9.52055,0.17806), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.52055,0.17806,9.82305,0.17861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.82305,0.17861,9.52055,0.17806), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.90817,0.17966,9.82305,0.17861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.35694,0.18167,9.90817,0.17966), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.02389,0.19361,9.43416,0.19611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.43416,0.19611,10.02389,0.19361), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.88861,0.22278,9.67222,0.22417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.67222,0.22417,13.88861,0.22278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.49666,0.29444,9.37472,0.31056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.37472,0.31056,9.56361,0.31444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.56361,0.31444,9.37472,0.31056), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.30722,0.32111,9.56361,0.31444), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.96972,0.36278,9.35278,0.36306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.35278,0.36306,13.96972,0.36278), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.06639,0.42806,9.60805,0.46861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.60805,0.46861,14.07222,0.485), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.07222,0.485,9.39972,0.48889), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.39972,0.48889,14.07222,0.485), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.30333,0.53472,9.58027,0.53722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.58027,0.53722,9.30333,0.53472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.10278,0.54639,14.27916,0.55361), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.27916,0.55361,14.10278,0.54639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.53916,0.58611,9.58305,0.59722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.58305,0.59722,9.46083,0.60028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.46083,0.60028,9.58305,0.59722), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.56027,0.61472,9.32,0.625), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.32,0.625,9.56027,0.61472), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.54666,0.67,9.32,0.625), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.62694,0.73556,9.54666,0.67), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.4725,0.84611,9.62694,0.73556), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.56,0.95861,11.35389,1.00194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.35389,1.00194,10.68444,1.0025), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.01472,1.00194,10.68444,1.0025), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((10.68444,1.0025,9.8045,1.00255), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.8045,1.00255,10.68444,1.0025), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.75916,1.05194,9.68083,1.0575), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((9.68083,1.0575,9.75916,1.05194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.36527,1.09028,14.31277,1.11111), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.31277,1.11111,14.36527,1.09028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.18587,1.22313,13.30555,1.23417), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.30555,1.23417,13.18587,1.22313), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.15222,1.25389,13.52194,1.27194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.52194,1.27194,13.15222,1.25389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.37833,1.29639,13.22555,1.29972), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.22555,1.29972,13.37833,1.29639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.27389,1.32639,13.60139,1.33194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.60139,1.33194,14.27389,1.32639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.23517,1.36379,13.77889,1.37194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.77889,1.37194,14.07388,1.37528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.07388,1.37528,13.77889,1.37194), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((14.15944,1.39528,13.24638,1.39556), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.24638,1.39556,14.15944,1.39528), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.79833,1.43389,13.90361,1.43889), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.90361,1.43889,13.79833,1.43389), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.34889,1.50305,13.90361,1.43889), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.1375,1.56861,11.34889,1.50305), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.14194,1.70306,13.17389,1.77611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.17389,1.77611,13.14194,1.70306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.16111,1.90444,13.17389,1.77611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.28833,2.06694,13.29422,2.16407), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.29422,2.16407,11.34038,2.16861), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.34038,2.16861,13.29422,2.16407), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.755,2.23278,13.08555,2.24639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.08555,2.24639,13,2.25611), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13,2.25611,13.08555,2.24639), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.255,2.26639,12.57568,2.27433), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.57568,2.27433,13.13194,2.28028), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((13.13194,2.28028,12.57568,2.27433), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.16222,2.28028,12.57568,2.27433), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.36837,2.30359,11.62472,2.31306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.36837,2.30359,11.62472,2.31306), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((11.62472,2.31306,12.33687,2.31728), mapfile, tile_dir, 0, 11, "ga-gabon")
	render_tiles((12.33687,2.31728,11.62472,2.31306), mapfile, tile_dir, 0, 11, "ga-gabon")