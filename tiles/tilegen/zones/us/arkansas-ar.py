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
    # Zone: us
    # Region: Arkansas
    # Region Name: AR

	render_tiles((-91.16607,33.00411,-91.26456,33.00474), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.26456,33.00474,-91.16607,33.00411), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.43593,33.00584,-91.46039,33.006), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.46039,33.006,-91.43593,33.00584), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.48918,33.00618,-91.46039,33.006), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.87513,33.00773,-92.0691,33.00848), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.0691,33.00848,-92.22283,33.00908), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.06915,33.00848,-92.22283,33.00908), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.22283,33.00908,-92.0691,33.00848), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.15961,33.01124,-92.50138,33.01216), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.50138,33.01216,-91.15961,33.01124), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.72355,33.01433,-92.72474,33.01434), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.72474,33.01434,-92.72355,33.01433), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.97114,33.01719,-92.98871,33.01725), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.98871,33.01725,-92.97114,33.01719), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.1974,33.01795,-93.23861,33.01802), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.23861,33.01802,-93.1974,33.01795), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.37713,33.01823,-93.23861,33.01802), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.49051,33.01863,-93.52099,33.01874), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.52099,33.01874,-93.49051,33.01863), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04296,33.01922,-93.81455,33.01939), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.81455,33.01939,-93.80491,33.0194), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.80491,33.0194,-93.81455,33.01939), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.72327,33.01946,-93.80491,33.0194), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.12038,33.05453,-93.72327,33.01946), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.18084,33.09836,-91.10432,33.1316), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.10432,33.1316,-91.15302,33.13509), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.15302,33.13509,-91.10432,33.1316), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04272,33.16029,-91.08437,33.18086), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.08437,33.18086,-94.04272,33.16029), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.06871,33.23294,-94.04295,33.27124), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04295,33.27124,-91.08614,33.27365), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.08614,33.27365,-94.04295,33.27124), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.12554,33.28026,-91.08614,33.27365), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04307,33.3305,-91.14222,33.34899), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.14222,33.34899,-94.04307,33.3305), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.11376,33.39312,-91.14766,33.42717), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.14766,33.42717,-94.04299,33.43582), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04299,33.43582,-91.14766,33.42717), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.1718,33.46234,-94.04299,33.43582), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.18938,33.49301,-91.1718,33.46234), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.21567,33.52942,-91.20564,33.54698), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.20564,33.54698,-94.04343,33.55143), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04343,33.55143,-94.04383,33.55171), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.04383,33.55171,-94.04343,33.55143), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.35417,33.55645,-94.04383,33.55171), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.30374,33.56449,-94.38805,33.56551), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.38805,33.56551,-94.30374,33.56449), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.33842,33.56708,-94.38805,33.56551), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.21361,33.57062,-94.07267,33.57223), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.07267,33.57223,-94.21361,33.57062), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.18894,33.57623,-94.23887,33.57672), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.23887,33.57672,-91.18894,33.57623), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.41906,33.57722,-94.23887,33.57672), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.14302,33.57773,-94.41906,33.57722), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.1834,33.59221,-94.14302,33.57773), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.1309,33.61092,-94.1834,33.59221), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.48588,33.63787,-91.17831,33.65111), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.17831,33.65111,-91.10098,33.66055), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.10098,33.66055,-91.17831,33.65111), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.07539,33.7144,-91.14329,33.74714), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.14329,33.74714,-91.02678,33.76364), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.02678,33.76364,-91.11149,33.77457), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.11149,33.77457,-91.08551,33.77641), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.08551,33.77641,-91.11149,33.77457), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.48184,33.78901,-91.08551,33.77641), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.02517,33.80595,-94.48184,33.78901), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.05282,33.82418,-91.02517,33.80595), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.06125,33.87751,-91.02638,33.90798), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.02638,33.90798,-91.06125,33.87751), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.47727,33.94091,-91.03596,33.94376), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.03596,33.94376,-94.47727,33.94091), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.0887,33.96133,-91.00498,33.97701), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.00498,33.97701,-91.04837,33.98508), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.04837,33.98508,-91.00498,33.97701), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.97995,34.00011,-91.04837,33.98508), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.94266,34.01805,-94.4749,34.01966), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.4749,34.01966,-90.94266,34.01805), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.89242,34.02686,-94.4749,34.01966), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.87454,34.07204,-90.90113,34.09467), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.90113,34.09467,-90.94632,34.10937), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.94632,34.10937,-90.9448,34.11666), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.9448,34.11666,-90.94408,34.12007), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.94408,34.12007,-90.9448,34.11666), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.93806,34.14875,-90.89439,34.16095), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.89439,34.16095,-90.93806,34.14875), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.8827,34.18436,-94.47015,34.18986), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.47015,34.18986,-90.8827,34.18436), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.89456,34.22438,-90.83998,34.23611), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.83998,34.23611,-90.89456,34.22438), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.81283,34.27944,-90.75268,34.28927), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.75268,34.28927,-90.81283,34.27944), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.6604,34.33576,-90.76517,34.34282), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.76517,34.34282,-90.6604,34.33576), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.46543,34.35955,-90.72913,34.36421), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.72913,34.36421,-94.46543,34.35955), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.6414,34.38387,-90.72913,34.36421), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.57534,34.41515,-90.6414,34.38387), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.58372,34.45883,-90.57534,34.41515), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.46117,34.50746,-90.56935,34.52487), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.56935,34.52487,-94.46117,34.50746), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.54924,34.5681,-90.56935,34.52487), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.58722,34.61573,-90.57329,34.63367), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.57329,34.63367,-94.4575,34.63495), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.4575,34.63495,-90.57329,34.63367), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.55016,34.66345,-94.4575,34.63495), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.54605,34.70208,-94.4544,34.72896), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.4544,34.72896,-90.54605,34.70208), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.50549,34.76457,-90.47353,34.78884), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.47353,34.78884,-90.50549,34.76457), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.4638,34.83492,-90.40798,34.83527), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.40798,34.83527,-90.40163,34.83531), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.40163,34.83531,-90.40798,34.83527), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.31348,34.8717,-90.31142,34.87285), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.31142,34.87285,-90.31348,34.8717), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.44906,34.89056,-90.2501,34.90732), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.2501,34.90732,-94.44906,34.89056), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.44751,34.93398,-90.24448,34.9376), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.24448,34.9376,-94.44751,34.93398), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.3093,34.99569,-90.3007,35.02879), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.3007,35.02879,-90.2653,35.04029), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.2653,35.04029,-90.19715,35.05073), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.19715,35.05073,-90.2653,35.04029), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.44293,35.06251,-90.19715,35.05073), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.18139,35.0914,-90.09061,35.11829), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.09061,35.11829,-90.16006,35.12883), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.16006,35.12883,-90.09061,35.11829), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.09978,35.16447,-90.16006,35.12883), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.09329,35.20328,-90.09978,35.16447), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.09795,35.24998,-90.16659,35.27459), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.16659,35.27459,-94.43532,35.27589), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.43532,35.27589,-90.16659,35.27459), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.12186,35.30454,-94.43532,35.27589), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.0879,35.36327,-94.43152,35.36959), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.43152,35.36959,-90.0879,35.36327), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.43391,35.38636,-94.43489,35.39319), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.43489,35.39319,-94.43391,35.38636), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.1125,35.41015,-90.04531,35.41544), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.04531,35.41544,-90.1125,35.41015), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.07055,35.42329,-90.04531,35.41544), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.02206,35.45738,-90.07055,35.42329), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.04581,35.49653,-94.4497,35.49672), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.4497,35.49672,-90.04581,35.49653), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.9585,35.5417,-90.03762,35.55033), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.03762,35.55033,-89.9585,35.5417), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.94475,35.56031,-90.03762,35.55033), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.9325,35.60787,-89.87655,35.62665), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.87655,35.62665,-94.47312,35.63855), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.47312,35.63855,-89.87655,35.62665), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.89892,35.6509,-94.47312,35.63855), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.95659,35.69549,-89.89892,35.6509), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.86387,35.74759,-89.91549,35.75492), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.91549,35.75492,-94.49304,35.75917), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.49304,35.75917,-89.91549,35.75492), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.49455,35.7683,-94.49304,35.75917), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.79705,35.78265,-94.49455,35.7683), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.72343,35.80938,-89.79705,35.78265), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.72952,35.84763,-89.72263,35.87372), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.72263,35.87372,-89.64727,35.89492), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.64727,35.89492,-89.6489,35.90358), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.6489,35.90358,-89.64727,35.89492), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.65228,35.92146,-89.6489,35.90358), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.68692,35.94772,-89.65228,35.92146), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.53207,35.98785,-90.36872,35.99581), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.36872,35.99581,-90.28895,35.99651), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.28895,35.99651,-90.36872,35.99581), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.10384,35.99814,-89.95938,35.99901), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.95938,35.99901,-89.90118,35.99937), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.90118,35.99937,-89.95938,35.99901), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-89.7331,36.00061,-89.90118,35.99937), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.33934,36.04711,-89.7331,36.00061), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.55191,36.10223,-90.29449,36.11295), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.29449,36.11295,-94.55191,36.10223), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.23559,36.13947,-94.56227,36.16197), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.56227,36.16197,-90.23559,36.13947), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.22043,36.18476,-90.18913,36.19899), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.18913,36.19899,-90.22043,36.18476), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.15593,36.21407,-90.18913,36.19899), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.11492,36.2656,-94.5862,36.29997), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.5862,36.29997,-90.06398,36.30304), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.06398,36.30304,-94.5862,36.29997), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.06353,36.35691,-90.06614,36.38627), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.06614,36.38627,-90.13104,36.41507), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.13104,36.41507,-90.06614,36.38627), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.1414,36.45987,-90.15387,36.49534), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.15387,36.49534,-90.22075,36.49594), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.22075,36.49594,-90.15387,36.49534), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.40492,36.49712,-91.40714,36.49714), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.40714,36.49714,-91.40492,36.49712), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.45,36.49754,-92.35028,36.49779), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.35028,36.49779,-91.12654,36.4978), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.12654,36.4978,-92.35028,36.49779), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.12597,36.49785,-91.12654,36.4978), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.85405,36.49802,-92.83888,36.49803), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.83888,36.49803,-92.85405,36.49802), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.01797,36.49806,-92.77233,36.49808), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.77233,36.49808,-91.01797,36.49806), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.15031,36.49814,-92.52914,36.49817), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.52914,36.49817,-92.12043,36.49819), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.12043,36.49819,-92.52914,36.49817), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-92.56424,36.49824,-93.29345,36.49826), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.29345,36.49826,-92.56424,36.49824), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.31533,36.49831,-93.29345,36.49826), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.49458,36.49837,-90.57618,36.49841), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.57618,36.49841,-91.9858,36.49843), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.9858,36.49843,-90.57618,36.49841), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.78424,36.49846,-90.76567,36.49849), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-90.76567,36.49849,-90.78424,36.49846), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.42699,36.49859,-90.76567,36.49849), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.95919,36.49872,-93.42699,36.49859), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.86676,36.49887,-93.58428,36.4989), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.58428,36.4989,-93.86676,36.49887), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.07709,36.49898,-93.58428,36.4989), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-93.70017,36.49914,-91.67234,36.49926), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.67234,36.49926,-91.64259,36.49934), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-91.64259,36.49934,-94.61792,36.49941), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.61792,36.49941,-91.64259,36.49934), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.61792,36.49941,-91.64259,36.49934), mapfile, tile_dir, 0, 11, "arkansas-ar")
	render_tiles((-94.3612,36.4996,-94.61792,36.49941), mapfile, tile_dir, 0, 11, "arkansas-ar")