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
    # Region: HN
    # Region Name: Honduras

	render_tiles((-87.30014,12.98741,-87.06361,13.00028), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.06361,13.00028,-87.30014,12.98741), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.26889,13.02555,-87.32556,13.03389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.32556,13.03389,-86.9525,13.04167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.9525,13.04167,-87.32556,13.03389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.35722,13.07194,-87.30362,13.09666), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.30362,13.09666,-87.41917,13.10778), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.41917,13.10778,-87.30362,13.09666), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.90973,13.24111,-87.47696,13.25805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.47696,13.25805,-86.74306,13.26083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.74306,13.26083,-87.47696,13.25805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.51306,13.27361,-86.74306,13.26083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.69638,13.29472,-86.82779,13.29667), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.82779,13.29667,-86.69638,13.29472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.52917,13.34722,-87.74001,13.35527), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.74001,13.35527,-86.69666,13.35666), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.69666,13.35666,-87.74001,13.35527), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.41223,13.35889,-86.69666,13.35666), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.67723,13.36639,-87.38,13.37277), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.38,13.37277,-86.73277,13.37666), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.73277,13.37666,-87.38,13.37277), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.47084,13.40055,-87.8145,13.40747), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.8145,13.40747,-87.62279,13.40778), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.62279,13.40778,-87.8145,13.40747), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.39835,13.4125,-87.62279,13.40778), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.61639,13.45167,-87.64195,13.46055), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.64195,13.46055,-87.61639,13.45167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.71445,13.47055,-87.64195,13.46055), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.73277,13.5225,-87.78333,13.52639), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.78333,13.52639,-86.73277,13.5225), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.76347,13.71084,-86.76112,13.75278), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.76112,13.75278,-86.35417,13.75805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.35417,13.75805,-86.76112,13.75278), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.31305,13.77083,-86.35417,13.75805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.53056,13.7925,-86.31305,13.77083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.69527,13.81805,-85.73666,13.82861), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.73666,13.82861,-85.80083,13.83916), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.80083,13.83916,-85.73666,13.82861), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.75047,13.86406,-88.48917,13.86555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.48917,13.86555,-87.75047,13.86406), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.35306,13.86833,-88.00696,13.86944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.00696,13.86944,-88.35306,13.86833), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.90028,13.89805,-87.8786,13.90194), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.8786,13.90194,-87.90028,13.89805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.90916,13.90694,-87.8786,13.90194), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.85388,13.91416,-85.90916,13.90694), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.04083,13.93055,-85.85388,13.91416), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.73277,13.95778,-88.06,13.96389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.06,13.96389,-85.73277,13.95778), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.17583,13.97083,-88.06,13.96389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.50473,13.98138,-88.19666,13.98777), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.19666,13.98777,-88.50473,13.98138), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.02251,14.00305,-88.66144,14.01413), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.66144,14.01413,-86.02251,14.00305), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.08833,14.04444,-85.52417,14.05528), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.52417,14.05528,-86.01584,14.06583), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.01584,14.06583,-85.52417,14.05528), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.80943,14.09305,-88.72139,14.0975), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.72139,14.0975,-88.80943,14.09305), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.46834,14.10805,-88.83139,14.11416), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.83139,14.11416,-85.46834,14.10805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.76723,14.13778,-88.83139,14.11416), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.95473,14.18833,-85.38806,14.20555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.38806,14.20555,-88.90862,14.20722), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.90862,14.20722,-85.38806,14.20555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.315,14.28111,-85.17917,14.31528), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.17917,14.31528,-89.1261,14.32555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.1261,14.32555,-89.03722,14.33472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.03722,14.33472,-85.15584,14.34027), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.15584,14.34027,-89.03722,14.33472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.35487,14.42769,-85.17583,14.445), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.17583,14.445,-89.35487,14.42769), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.29944,14.515,-85.08667,14.54333), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.08667,14.54333,-85.11528,14.56667), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.11528,14.56667,-85.03278,14.57361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.03278,14.57361,-85.11528,14.56667), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.15445,14.5825,-85.03278,14.57361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.44916,14.62778,-89.14445,14.64861), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.14445,14.64861,-84.27528,14.665), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.27528,14.665,-84.32556,14.67361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.32556,14.67361,-84.27528,14.665), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.68222,14.68555,-84.32556,14.67361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.02583,14.70055,-84.68222,14.68555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.19666,14.71638,-84.10333,14.72888), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.10333,14.72888,-84.19666,14.71638), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.26778,14.74472,-83.9325,14.7525), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.9325,14.7525,-84.26778,14.74472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.23721,14.76333,-83.9325,14.7525), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.11806,14.78416,-83.84389,14.785), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.84389,14.785,-84.11806,14.78416), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.92139,14.79944,-83.84389,14.785), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.81862,14.82778,-83.92139,14.79944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.22472,14.86805,-83.71889,14.86833), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.71889,14.86833,-89.22472,14.86805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.53111,14.94972,-89.15417,14.98138), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.15417,14.98138,-83.17694,14.98972), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.17694,14.98972,-83.4175,14.99111), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.4175,14.99111,-83.17694,14.98972), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.13087,14.99329,-83.4175,14.99111), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.18138,15.00333,-83.50696,15.01278), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.50696,15.01278,-83.19501,15.01833), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.19501,15.01833,-83.50696,15.01278), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.39084,15.02472,-83.19501,15.01833), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.15083,15.07361,-83.31029,15.09889), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.31029,15.09889,-89.15083,15.07361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-89.0089,15.12527,-83.31029,15.09889), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.58334,15.16,-89.0089,15.12527), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.62418,15.19528,-83.5414,15.19944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.5414,15.19944,-83.72195,15.2), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.72195,15.2,-83.5414,15.19944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.48251,15.20611,-83.72195,15.2), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.3539,15.22167,-83.76472,15.22305), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.76472,15.22305,-83.3539,15.22167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.58974,15.23361,-83.92084,15.24083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.92084,15.24083,-83.58974,15.23361), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.8289,15.25861,-83.92084,15.24083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.50667,15.27972,-83.53111,15.28167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.53111,15.28167,-83.50667,15.27972), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.47168,15.2875,-83.62361,15.29083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.62361,15.29083,-83.95778,15.29111), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.95778,15.29111,-83.62361,15.29083), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.99306,15.30166,-83.93251,15.30805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.93251,15.30805,-83.99306,15.30166), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.88806,15.31639,-83.93251,15.30805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.0289,15.33389,-88.66833,15.35), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.66833,15.35,-84.07751,15.35139), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.07751,15.35139,-88.66833,15.35), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.88612,15.35889,-83.72168,15.36611), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.72168,15.36611,-83.88612,15.35889), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.02667,15.39111,-83.70668,15.3975), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.70668,15.3975,-84.02667,15.39111), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.00223,15.41416,-84.12001,15.41444), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.12001,15.41444,-84.00223,15.41416), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.08974,15.45944,-84.17917,15.47944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.17917,15.47944,-83.87973,15.48194), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.87973,15.48194,-84.17917,15.47944), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-83.98473,15.52166,-84.20529,15.53055), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.20529,15.53055,-84.14667,15.53166), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.14667,15.53166,-84.20529,15.53055), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.09973,15.54917,-84.14667,15.53166), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.32779,15.63528,-84.05779,15.64778), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.05779,15.64778,-88.32779,15.63528), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.31389,15.67138,-88.16112,15.68389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.31389,15.67138,-88.16112,15.68389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.16112,15.68389,-88.11974,15.6875), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.11974,15.6875,-88.16112,15.68389), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-88.21306,15.72305,-86.91223,15.75555), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.91223,15.75555,-86.35335,15.77167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.35335,15.77167,-84.60196,15.77611), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.60196,15.77611,-86.35335,15.77167), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.47084,15.78305,-87.07668,15.7875), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.07668,15.7875,-86.63445,15.79139), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.63445,15.79139,-87.07668,15.7875), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.9689,15.79916,-84.24391,15.80493), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.24391,15.80493,-87.535,15.81028), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.535,15.81028,-84.24391,15.80493), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.41501,15.8175,-87.92723,15.81805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.92723,15.81805,-84.41501,15.8175), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.62862,15.81917,-87.92723,15.81805), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.35556,15.85028,-84.5139,15.85472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.5139,15.85472,-87.35556,15.85028), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.95418,15.86222,-84.5139,15.85472), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.61,15.87055,-87.95418,15.86222), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.62862,15.88277,-85.42001,15.89139), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.42001,15.89139,-84.69528,15.8925), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.69528,15.8925,-86.13474,15.89305), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.13474,15.89305,-84.69528,15.8925), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.98862,15.89889,-86.13474,15.89305), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-87.69278,15.91694,-85.11974,15.92194), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.11974,15.92194,-87.69278,15.91694), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.67056,15.94417,-85.9164,15.94639), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.9164,15.94639,-85.67056,15.94417), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.70334,15.97305,-85.02306,15.98611), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.02306,15.98611,-84.94667,15.98694), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-84.94667,15.98694,-85.02306,15.98611), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.9039,15.99166,-84.94667,15.98694), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-86.01501,16.01666,-85.92696,16.02027), mapfile, tile_dir, 0, 11, "hn-honduras")
	render_tiles((-85.92696,16.02027,-86.01501,16.01666), mapfile, tile_dir, 0, 11, "hn-honduras")