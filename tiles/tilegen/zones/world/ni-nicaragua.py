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
    # Region: NI
    # Region Name: Nicaragua

	render_tiles((-83.92444,10.70972,-83.98222,10.75611), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.98222,10.75611,-84.19777,10.78583), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.19777,10.78583,-83.67722,10.78889), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.67722,10.78889,-84.19777,10.78583), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.22472,10.87805,-83.66167,10.87944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.66167,10.87944,-84.22472,10.87805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.71251,10.92416,-83.64534,10.92426), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.64534,10.92426,-83.71251,10.92416), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.32167,10.92666,-83.64534,10.92426), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.90279,10.94083,-84.32167,10.92666), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.94055,10.95527,-84.44444,10.95944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.44444,10.95944,-84.94055,10.95527), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.35556,10.99333,-84.44444,10.95944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.80751,11.07139,-84.67444,11.07805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.67444,11.07805,-85.71135,11.08428), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.71135,11.08428,-84.67444,11.07805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.28416,11.09139,-85.71135,11.08428), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.78389,11.11055,-85.28416,11.09139), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.65028,11.15166,-85.78389,11.11055), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.61444,11.21361,-85.65028,11.15166), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.92834,11.30639,-83.87001,11.34278), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.87001,11.34278,-85.92834,11.30639), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.12001,11.45583,-83.75723,11.55639), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.75723,11.55639,-83.66833,11.59861), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.66833,11.59861,-86.315,11.60083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.315,11.60083,-83.66833,11.59861), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.49695,11.75944,-83.77417,11.80083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.77417,11.80083,-83.73834,11.80139), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.73834,11.80139,-83.77417,11.80083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.6989,11.84639,-83.73306,11.86028), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.73306,11.86028,-83.6989,11.84639), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.83168,11.87472,-83.73306,11.86028), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.79501,11.90055,-83.83168,11.87472), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.63362,11.96278,-83.67917,11.99361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.67917,11.99361,-83.75389,11.9975), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.75389,11.9975,-83.67917,11.99361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.80196,12.05555,-83.73279,12.06528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.73279,12.06528,-83.72278,12.06805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.72278,12.06805,-83.73279,12.06528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.69417,12.11639,-83.70862,12.12444), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.70862,12.12444,-83.69417,12.11639), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.77335,12.15,-83.73306,12.15833), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.73306,12.15833,-83.77335,12.15), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.72278,12.17083,-86.76306,12.17305), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.76306,12.17305,-83.72278,12.17083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.77667,12.17805,-86.76306,12.17305), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.84361,12.24528,-83.66251,12.25666), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.66251,12.25666,-86.84361,12.24528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.7189,12.33361,-83.61473,12.33722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.61473,12.33722,-83.7189,12.33361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.67195,12.34083,-83.61473,12.33722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.625,12.3575,-83.61,12.36972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.61,12.36972,-87.04973,12.37944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.04973,12.37944,-83.63335,12.38111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.63335,12.38111,-87.04973,12.37944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.48668,12.39722,-83.63335,12.38111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.59778,12.41889,-87.09917,12.42972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.09917,12.42972,-87.09917,12.43111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.09917,12.43111,-87.09917,12.42972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.47723,12.44889,-87.09917,12.43111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.73418,12.48,-83.77724,12.48028), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.77724,12.48028,-83.73418,12.48), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.63057,12.53194,-87.22473,12.53528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.22473,12.53528,-83.63057,12.53194), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.75696,12.54611,-87.22473,12.53528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.57861,12.56222,-83.62889,12.57083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.62889,12.57083,-83.57861,12.56222), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.53917,12.59222,-83.63,12.60361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.63,12.60361,-83.55806,12.61083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.55806,12.61083,-83.63,12.60361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.38612,12.6725,-83.55806,12.61083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.48918,12.7675,-83.53807,12.785), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.53807,12.785,-83.52223,12.79667), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.52223,12.79667,-83.64223,12.80083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.64223,12.80083,-83.52223,12.79667), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.59167,12.81861,-83.64223,12.80083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.69029,12.90777,-87.42445,12.91972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.42445,12.91972,-87.35306,12.92528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.35306,12.92528,-87.42445,12.91972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.52028,12.97,-87.65862,12.98722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.65862,12.98722,-87.30014,12.98741), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.30014,12.98741,-87.65862,12.98722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.06361,13.00028,-87.30014,12.98741), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.9525,13.04167,-87.06361,13.00028), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-87.57834,13.08361,-86.9525,13.04167), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.90973,13.24111,-86.74306,13.26083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.74306,13.26083,-83.56529,13.2625), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.56529,13.2625,-86.74306,13.26083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.69638,13.29472,-86.82779,13.29667), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.82779,13.29667,-86.69638,13.29472), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.69666,13.35666,-86.73277,13.37666), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.73277,13.37666,-86.69666,13.35666), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.73277,13.5225,-83.51772,13.548), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.51772,13.548,-86.73277,13.5225), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.49695,13.69528,-86.76347,13.71084), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.76347,13.71084,-83.49695,13.69528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.76112,13.75278,-86.35417,13.75805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.35417,13.75805,-86.76112,13.75278), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.31305,13.77083,-86.35417,13.75805), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.53056,13.7925,-86.31305,13.77083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.73666,13.82861,-85.80083,13.83916), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.73666,13.82861,-85.80083,13.83916), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.80083,13.83916,-85.73666,13.82861), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.90916,13.90694,-85.85388,13.91416), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.85388,13.91416,-85.90916,13.90694), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.73277,13.95778,-83.42807,13.96417), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.42807,13.96417,-85.73277,13.95778), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.17583,13.97083,-83.42807,13.96417), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.02251,14.00305,-86.17583,13.97083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.08833,14.04444,-85.52417,14.05528), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.52417,14.05528,-86.01584,14.06583), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-86.01584,14.06583,-83.33556,14.06722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.33556,14.06722,-86.01584,14.06583), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.46834,14.10805,-83.33556,14.06722), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.38806,14.20555,-83.20306,14.27417), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.20306,14.27417,-85.315,14.28111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.315,14.28111,-83.20306,14.27417), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.17917,14.31528,-85.15584,14.34027), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.15584,14.34027,-83.18806,14.35333), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.18806,14.35333,-85.15584,14.34027), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.17583,14.445,-83.23279,14.5125), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.23279,14.5125,-85.08667,14.54333), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.08667,14.54333,-85.11528,14.56667), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.11528,14.56667,-85.03278,14.57361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.03278,14.57361,-85.11528,14.56667), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.44916,14.62778,-84.27528,14.665), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.27528,14.665,-84.32556,14.67361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.32556,14.67361,-84.27528,14.665), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.68222,14.68555,-84.32556,14.67361), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-85.02583,14.70055,-84.68222,14.68555), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.19666,14.71638,-84.10333,14.72888), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.10333,14.72888,-84.19666,14.71638), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.26778,14.74472,-83.9325,14.7525), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.9325,14.7525,-84.26778,14.74472), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.23721,14.76333,-83.34778,14.76472), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.34778,14.76472,-84.23721,14.76333), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.30196,14.78028,-84.11806,14.78416), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.11806,14.78416,-83.84389,14.785), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.84389,14.785,-84.11806,14.78416), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.92139,14.79944,-83.41196,14.80861), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.41196,14.80861,-83.92139,14.79944), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-84.81862,14.82778,-83.29225,14.83428), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.29225,14.83428,-84.81862,14.82778), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.71889,14.86833,-83.25334,14.88083), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.25334,14.88083,-83.71889,14.86833), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.30556,14.90389,-83.33528,14.91139), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.33528,14.91139,-83.30556,14.90389), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.53111,14.94972,-83.33528,14.91139), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.17694,14.98972,-83.4175,14.99111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.4175,14.99111,-83.17694,14.98972), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.13087,14.99329,-83.4175,14.99111), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.50696,15.01278,-83.39084,15.02472), mapfile, tile_dir, 0, 11, "ni-nicaragua")
	render_tiles((-83.39084,15.02472,-83.50696,15.01278), mapfile, tile_dir, 0, 11, "ni-nicaragua")