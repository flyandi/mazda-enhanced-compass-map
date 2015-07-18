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
    # Region: CG
    # Region Name: Congo

	render_tiles((12.01007,-5.02062,11.98,-4.98778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.98,-4.98778,12.01007,-5.02062), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.655,-4.91,12.16639,-4.89583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.16639,-4.89583,14.41904,-4.88765), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.41904,-4.88765,14.71889,-4.885), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.71889,-4.885,13.41028,-4.88306), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.41028,-4.88306,14.71889,-4.885), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.49583,-4.84083,13.49416,-4.80389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.49416,-4.80389,12.29222,-4.79361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.29222,-4.79361,14.84611,-4.79167), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.84611,-4.79167,12.29222,-4.79361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.37555,-4.78667,14.84611,-4.79167), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.20638,-4.75889,13.37555,-4.78667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.70055,-4.72333,12.20638,-4.75889), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.19472,-4.68361,11.75694,-4.64694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.75694,-4.64694,13.09105,-4.63307), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.09105,-4.63307,11.75694,-4.64694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.41278,-4.6025,14.38333,-4.59944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.38333,-4.59944,12.41278,-4.6025), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.79944,-4.59278,13.14528,-4.58917), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.14528,-4.58917,11.79944,-4.59278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.10083,-4.5725,12.65,-4.55944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.65,-4.55944,14.36472,-4.55778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.36472,-4.55778,12.65,-4.55944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.66444,-4.52167,11.74917,-4.5125), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.74917,-4.5125,12.66444,-4.52167), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.965,-4.495,12.92222,-4.48528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.92222,-4.48528,13.88027,-4.48472), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.88027,-4.48472,12.92222,-4.48528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.99111,-4.46528,14.47666,-4.45639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.47666,-4.45639,13.99111,-4.46528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.73916,-4.44167,14.47666,-4.45639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.4875,-4.42695,13.82861,-4.42611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.82861,-4.42611,14.4875,-4.42695), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.15194,-4.42,12.89583,-4.41528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.89583,-4.41528,15.15194,-4.42), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.78083,-4.38889,12.89583,-4.41528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.39916,-4.34139,15.27287,-4.30676), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.27287,-4.30676,15.42861,-4.29361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.42861,-4.29361,15.27287,-4.30676), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.41528,-4.27417,15.42861,-4.29361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.47333,-4.24889,15.48,-4.22564), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.48,-4.22564,15.47333,-4.24889), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.48707,-4.20097,11.38166,-4.19222), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.38166,-4.19222,15.48707,-4.20097), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.51695,-4.0968,11.33639,-4.08583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.33639,-4.08583,15.51695,-4.0968), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.56944,-4.03722,11.33639,-4.08583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.84333,-3.96917,11.1582,-3.94363), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.1582,-3.94363,11.14151,-3.9229), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.14151,-3.9229,15.91111,-3.91972), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.91111,-3.91972,11.14151,-3.9229), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.98333,-3.75694,11.88472,-3.69917), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.88472,-3.69917,11.69472,-3.69639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.69472,-3.69639,11.88472,-3.69917), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.24472,-3.67861,11.69472,-3.69639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.925,-3.62667,11.43917,-3.585), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.43917,-3.585,11.84222,-3.57944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.84222,-3.57944,11.43917,-3.585), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.84138,-3.55139,11.84222,-3.57944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.47833,-3.52139,11.56055,-3.51722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.56055,-3.51722,11.47833,-3.52139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.22777,-3.32222,11.95805,-3.29139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.975,-3.32222,11.95805,-3.29139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.95805,-3.29139,16.22777,-3.32222), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.69972,-3.16694,16.18888,-3.06167), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.18888,-3.06167,11.76611,-3.04722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.76611,-3.04722,11.73194,-3.03778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.73194,-3.03778,11.76611,-3.04722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.80166,-3.00889,11.73194,-3.03778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.56472,-2.86722,11.54333,-2.85695), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.54333,-2.85695,11.56472,-2.86722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.19582,-2.8225,11.6325,-2.81861), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.6325,-2.81861,16.19582,-2.8225), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.54611,-2.79389,11.6325,-2.81861), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.61194,-2.71583,11.54611,-2.79389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.6475,-2.61333,11.61194,-2.71583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.08777,-2.495,13.87888,-2.47639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.87888,-2.47639,14.08777,-2.495), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.12833,-2.45444,11.73527,-2.43667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.73527,-2.43667,13.48694,-2.43528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.48694,-2.43528,11.73527,-2.43667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.86444,-2.42306,13.48694,-2.43528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.77166,-2.39639,12.02166,-2.39139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.02166,-2.39139,11.77166,-2.39639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.93027,-2.35778,11.68111,-2.35667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.68111,-2.35667,13.93027,-2.35778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.01944,-2.34778,14.255,-2.34222), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.255,-2.34222,13.01944,-2.34778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.57417,-2.33333,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((11.96416,-2.33333,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.47805,-2.32722,11.57417,-2.33333), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.01111,-2.29556,12.47805,-2.32722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.18388,-2.24583,14.15833,-2.22861), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.15833,-2.22861,16.18388,-2.24583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.97277,-2.19083,14.22555,-2.19028), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.22555,-2.19028,12.97277,-2.19083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.72583,-2.18611,14.22555,-2.19028), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.20694,-2.15889,13.72722,-2.15111), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.72722,-2.15111,12.90444,-2.14694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.90444,-2.14694,13.72722,-2.15111), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.51722,-2.09306,12.90444,-2.14694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.77055,-2.09306,12.90444,-2.14694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.44805,-2.02445,16.41444,-1.98111), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.41444,-1.98111,14.26444,-1.96611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.26444,-1.96611,12.84527,-1.95444), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.84527,-1.95444,14.26444,-1.96611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.49527,-1.92389,12.52472,-1.90528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.52472,-1.90528,14.42972,-1.89167), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.42972,-1.89167,12.52472,-1.90528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.45277,-1.87722,12.52055,-1.8725), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.52055,-1.8725,12.45277,-1.87722), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((12.655,-1.82445,14.42139,-1.81278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.42139,-1.81278,12.655,-1.82445), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.58388,-1.76667,14.42139,-1.81278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.46666,-1.68056,14.38389,-1.61083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.38389,-1.61083,14.43389,-1.57861), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.43389,-1.57861,14.38389,-1.61083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.50166,-1.39028,14.44416,-1.33194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.44416,-1.33194,16.80722,-1.31639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.80722,-1.31639,14.44416,-1.33194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.48194,-1.21278,16.96805,-1.15389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.96805,-1.15389,14.48194,-1.21278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.41639,-1.03345,17.30694,-1.01528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.30694,-1.01528,14.41639,-1.03345), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.63555,-0.67083,14.51861,-0.60917), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.51861,-0.60917,17.63555,-0.67083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.45666,-0.51861,14.1775,-0.4525), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.1775,-0.4525,17.73693,-0.44361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.73693,-0.44361,14.1775,-0.4525), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.30666,-0.43333,17.73693,-0.44361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.16083,-0.35778,17.73677,-0.31637), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.73677,-0.31637,14.12083,-0.27611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.12083,-0.27611,13.88694,-0.25306), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.88694,-0.25306,14.12083,-0.27611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.70999,-0.17417,13.85222,-0.1675), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.85222,-0.1675,17.70999,-0.17417), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.95,0.03333,13.91333,0.0425), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.91333,0.0425,13.95,0.03333), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.8036,0.14861,13.88861,0.22278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.88861,0.22278,17.8036,0.14861), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.9487,0.35261,13.96972,0.36278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.96972,0.36278,17.9487,0.35261), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.06639,0.42806,17.96693,0.44778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.96693,0.44778,14.06639,0.42806), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.07222,0.485,17.96693,0.44778), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.10278,0.54639,14.27916,0.55361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.27916,0.55361,14.10278,0.54639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.4725,0.84611,17.87888,0.95333), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.87888,0.95333,14.4725,0.84611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.36527,1.09028,14.31277,1.11111), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.31277,1.11111,14.36527,1.09028), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.18587,1.22313,13.30555,1.23417), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.30555,1.23417,13.18587,1.22313), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.15222,1.25389,13.52194,1.27194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.52194,1.27194,13.15222,1.25389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.37833,1.29639,13.22555,1.29972), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.22555,1.29972,13.37833,1.29639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.27389,1.32639,13.60139,1.33194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.60139,1.33194,14.27389,1.32639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.23517,1.36379,13.77889,1.37194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.77889,1.37194,14.07388,1.37528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.07388,1.37528,13.77889,1.37194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.15944,1.39528,13.24638,1.39556), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.24638,1.39556,14.15944,1.39528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.79833,1.43389,13.90361,1.43889), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.90361,1.43889,13.79833,1.43389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.0386,1.44555,13.90361,1.43889), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.1375,1.56861,16.07222,1.65417), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.07222,1.65417,18.07833,1.65528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.07833,1.65528,16.07222,1.65417), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.14194,1.70306,16.16249,1.72667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.16249,1.72667,13.14194,1.70306), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.01777,1.76111,13.17389,1.77611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.17389,1.77611,15.93277,1.78056), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.93277,1.78056,13.17389,1.77611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.87194,1.825,15.93277,1.78056), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.12416,1.87389,13.16111,1.90444), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.16111,1.90444,15.33722,1.92055), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.33722,1.92055,13.16111,1.90444), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.06055,1.97333,15.48638,1.97639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.48638,1.97639,16.06055,1.97333), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.07222,1.98,15.48638,1.97639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.27277,1.99222,14.95388,2.00361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.95388,2.00361,14.88778,2.00389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.88778,2.00389,14.95388,2.00361), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.14139,2.00833,14.88778,2.00389), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.98,2.03194,15.15139,2.03972), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((15.15139,2.03972,14.98,2.03194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.91083,2.05333,14.80527,2.06083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.80527,2.06083,13.28833,2.06694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.28833,2.06694,14.80527,2.06083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.86139,2.1125,13.28833,2.06694), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.73,2.16028,14.52389,2.16055), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.52389,2.16055,13.73,2.16028), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.37833,2.16139,14.52389,2.16055), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((13.29422,2.16407,16.08999,2.16639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.08999,2.16639,13.29422,2.16407), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((14.56783,2.20399,16.20641,2.2211), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.20641,2.2211,18.09333,2.22944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.09333,2.22944,16.20641,2.2211), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.21999,2.42722,18.34202,2.61263), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.34202,2.61263,18.40277,2.73139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.40277,2.73139,16.50166,2.84944), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.50166,2.84944,18.40277,2.73139), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.53933,3.07568,18.61166,3.13194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.61166,3.13194,16.48277,3.15667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.48277,3.15667,18.61166,3.13194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.64222,3.21056,16.48277,3.15667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.626,3.47886,16.58888,3.48194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.626,3.47886,16.58888,3.48194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.58888,3.48194,18.18388,3.48278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.18388,3.48278,16.58888,3.48194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.23388,3.49917,18.18388,3.48278), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.86194,3.52444,16.98777,3.53528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.98777,3.53528,17.86499,3.53611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.86499,3.53611,16.98777,3.53528), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.98888,3.53917,17.86499,3.53611), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.69916,3.54528,18.14388,3.55083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.14388,3.55083,18.54888,3.55333), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.54888,3.55333,18.14388,3.55083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((16.87666,3.56583,18.04944,3.56639), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.04944,3.56639,16.87666,3.56583), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.07722,3.57667,17.93888,3.5775), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.93888,3.5775,17.07722,3.57667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.39805,3.5775,17.07722,3.57667), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.2725,3.58306,17.93888,3.5775), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.53333,3.59889,18.2725,3.58306), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.3561,3.61528,17.75999,3.63055), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.75999,3.63055,18.48138,3.64194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((18.48138,3.64194,17.57944,3.64833), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.57944,3.64833,18.48138,3.64194), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.41388,3.67972,17.4936,3.71083), mapfile, tile_dir, 0, 11, "cg-congo")
	render_tiles((17.4936,3.71083,17.41388,3.67972), mapfile, tile_dir, 0, 11, "cg-congo")