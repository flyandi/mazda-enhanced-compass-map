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
        mapfile = "../../../tilestyles/mazda/mazda.xml"
    except KeyError:
        print("[MapFile] Not found")
        sys.exit(1)
    try:
        # ./tilegen/zones/[zone]/[region]
        tile_dir = "../../../../output/"
    except KeyError:
        print("[OutputDir] No output directory found")
        sys.exit(1)

    if not tile_dir.endswith('/'):
        tile_dir = tile_dir + '/'


    print ("Starting")

    # ------------------------------------------------------------------------
    # Tile Render Data
    # Zone: world
    # Region: DO
    # Region Name: Dominican Republic

    render_tiles((-71.39667,17.61833,-71.02917,17.62917), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.39667,17.61833,-71.02917,17.62917), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.39667,17.61833,-71.02917,17.62917), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.39667,17.61833,-71.02917,17.62917), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.44278,17.62917,-71.39667,19.90472), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.44278,17.62917,-71.39667,19.90472), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.44278,17.62917,-71.39667,19.90472), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.44278,17.62917,-71.39667,19.90472), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.5114,17.74055,-71.39667,19.90472), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.5114,17.74055,-71.39667,19.90472), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.5114,17.74055,-71.39667,19.90472), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.5114,17.74055,-71.39667,19.90472), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.67668,17.75916,-71.39667,19.09888), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.67668,17.75916,-71.39667,19.09888), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.67668,17.75916,-71.39667,19.09888), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.67668,17.75916,-71.39667,19.09888), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.62807,17.81805,-71.39667,19.21971), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.62807,17.81805,-71.39667,19.21971), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.62807,17.81805,-71.39667,19.21971), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.62807,17.81805,-71.39667,19.21971), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.26723,17.84666,-71.39667,19.82305), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.26723,17.84666,-71.39667,19.82305), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.26723,17.84666,-71.39667,19.82305), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.26723,17.84666,-71.39667,19.82305), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.66167,17.96,-71.02917,17.75916), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.66167,17.96,-71.02917,17.75916), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.66167,17.96,-71.02917,17.75916), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.66167,17.96,-71.02917,17.75916), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.77148,18.05423,-71.02917,18.20221), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.77148,18.05423,-71.02917,18.20221), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.77148,18.05423,-71.02917,18.20221), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.77148,18.05423,-71.02917,18.20221), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.74889,18.09555,-71.39667,19.70017), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.74889,18.09555,-71.39667,19.70017), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.74889,18.09555,-71.39667,19.70017), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.74889,18.09555,-71.39667,19.70017), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.05501,18.1511,-71.02917,18.30416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.05501,18.1511,-71.02917,18.30416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.05501,18.1511,-71.02917,18.30416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.05501,18.1511,-71.02917,18.30416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.74779,18.19888,-71.02917,18.35361), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.74779,18.19888,-71.02917,18.35361), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.74779,18.19888,-71.02917,18.35361), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.74779,18.19888,-71.02917,18.35361), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.76917,18.20221,-71.39667,19.33194), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.76917,18.20221,-71.39667,19.33194), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.76917,18.20221,-71.39667,19.33194), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.76917,18.20221,-71.39667,19.33194), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.55751,18.20583,-71.02917,18.40166), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.55751,18.20583,-71.02917,18.40166), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.55751,18.20583,-71.02917,18.40166), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.55751,18.20583,-71.02917,18.40166), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.63806,18.21333,-71.39667,18.87416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.63806,18.21333,-71.39667,18.87416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.63806,18.21333,-71.39667,18.87416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.63806,18.21333,-71.39667,18.87416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.15417,18.23306,-71.39667,19.62083), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.15417,18.23306,-71.39667,19.62083), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.15417,18.23306,-71.39667,19.62083), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.15417,18.23306,-71.39667,19.62083), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.10335,18.24583,-71.02917,18.30416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.10335,18.24583,-71.02917,18.30416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.10335,18.24583,-71.02917,18.30416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.10335,18.24583,-71.02917,18.30416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.94862,18.25305,-71.39667,19.90027), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.94862,18.25305,-71.39667,19.90027), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.94862,18.25305,-71.39667,19.90027), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.94862,18.25305,-71.39667,19.90027), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.91389,18.26638,-71.02917,18.25305), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.91389,18.26638,-71.02917,18.25305), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.91389,18.26638,-71.02917,18.25305), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.91389,18.26638,-71.02917,18.25305), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.07556,18.30416,-71.02917,18.1511), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.07556,18.30416,-71.02917,18.1511), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.07556,18.30416,-71.02917,18.1511), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.07556,18.30416,-71.02917,18.1511), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.02612,18.30527,-71.39667,19.92833), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.02612,18.30527,-71.39667,19.92833), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.02612,18.30527,-71.39667,19.92833), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.02612,18.30527,-71.39667,19.92833), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.69472,18.32222,-71.39667,19.24166), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.69472,18.32222,-71.39667,19.24166), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.69472,18.32222,-71.39667,19.24166), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.69472,18.32222,-71.39667,19.24166), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.51918,18.34416,-71.02917,18.35583), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.51918,18.34416,-71.02917,18.35583), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.51918,18.34416,-71.02917,18.35583), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.51918,18.34416,-71.02917,18.35583), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.85001,18.34444,-71.39667,19.90499), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.85001,18.34444,-71.39667,19.90499), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.85001,18.34444,-71.39667,19.90499), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.85001,18.34444,-71.39667,19.90499), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.75307,18.3461,-71.02917,18.37638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.75307,18.3461,-71.02917,18.37638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.75307,18.3461,-71.02917,18.37638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.75307,18.3461,-71.02917,18.37638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.82973,18.35361,-71.02917,18.19888), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.82973,18.35361,-71.02917,18.19888), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.82973,18.35361,-71.02917,18.19888), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.82973,18.35361,-71.02917,18.19888), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.45001,18.35583,-71.02917,18.34416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.45001,18.35583,-71.02917,18.34416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.45001,18.35583,-71.02917,18.34416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.45001,18.35583,-71.02917,18.34416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.71278,18.37638,-71.02917,18.4136), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.71278,18.37638,-71.02917,18.4136), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.71278,18.37638,-71.02917,18.4136), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.71278,18.37638,-71.02917,18.4136), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.59531,18.37833,-71.02917,18.21333), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.59531,18.37833,-71.02917,18.21333), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.59531,18.37833,-71.02917,18.21333), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.59531,18.37833,-71.02917,18.21333), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.05972,18.39055,-71.39667,19.01527), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.05972,18.39055,-71.39667,19.01527), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.05972,18.39055,-71.39667,19.01527), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.05972,18.39055,-71.39667,19.01527), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.58612,18.40166,-71.02917,18.20583), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.58612,18.40166,-71.02917,18.20583), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.58612,18.40166,-71.02917,18.20583), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.58612,18.40166,-71.02917,18.20583), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.62611,18.40638,-71.39667,19.08916), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.62611,18.40638,-71.39667,19.08916), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.62611,18.40638,-71.39667,19.08916), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.62611,18.40638,-71.39667,19.08916), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.71251,18.4136,-71.02917,18.37638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.71251,18.4136,-71.02917,18.37638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.71251,18.4136,-71.02917,18.37638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.71251,18.4136,-71.02917,18.37638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.0139,18.41555,-71.39667,19.6786), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.0139,18.41555,-71.39667,19.6786), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.0139,18.41555,-71.39667,19.6786), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.0139,18.41555,-71.39667,19.6786), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.95445,18.41805,-71.39667,19.03083), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.95445,18.41805,-71.39667,19.03083), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.95445,18.41805,-71.39667,19.03083), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.95445,18.41805,-71.39667,19.03083), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.32556,18.42638,-71.02917,18.44472), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.32556,18.42638,-71.02917,18.44472), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.32556,18.42638,-71.02917,18.44472), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.32556,18.42638,-71.02917,18.44472), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.66446,18.43499,-71.39667,19.76083), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.66446,18.43499,-71.39667,19.76083), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.66446,18.43499,-71.39667,19.76083), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.66446,18.43499,-71.39667,19.76083), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.91028,18.44194,-71.02917,18.61138), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.91028,18.44194,-71.02917,18.61138), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.91028,18.44194,-71.02917,18.61138), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.91028,18.44194,-71.02917,18.61138), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.315,18.44472,-71.02917,18.42638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.315,18.44472,-71.02917,18.42638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.315,18.44472,-71.02917,18.42638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.315,18.44472,-71.02917,18.42638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.69084,18.45555,-71.39667,19.1161), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.69084,18.45555,-71.39667,19.1161), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.69084,18.45555,-71.39667,19.1161), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.69084,18.45555,-71.39667,19.1161), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.88196,18.46944,-71.39667,19.60027), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.88196,18.46944,-71.39667,19.60027), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.88196,18.46944,-71.39667,19.60027), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.88196,18.46944,-71.39667,19.60027), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.88501,18.47721,-71.02917,18.61138), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.88501,18.47721,-71.02917,18.61138), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.88501,18.47721,-71.02917,18.61138), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.88501,18.47721,-71.02917,18.61138), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.94635,18.54246,-71.02917,18.62416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.94635,18.54246,-71.02917,18.62416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.94635,18.54246,-71.02917,18.62416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.94635,18.54246,-71.02917,18.62416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.32224,18.60666,-71.02917,18.64444), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.32224,18.60666,-71.02917,18.64444), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.32224,18.60666,-71.02917,18.64444), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.32224,18.60666,-71.02917,18.64444), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.99152,18.60859,-71.02917,18.62416), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.99152,18.60859,-71.02917,18.62416), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.99152,18.60859,-71.02917,18.62416), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.99152,18.60859,-71.02917,18.62416), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.88751,18.61138,-71.02917,18.47721), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.88751,18.61138,-71.02917,18.47721), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.88751,18.61138,-71.02917,18.47721), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.88751,18.61138,-71.02917,18.47721), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.97084,18.62416,-71.02917,18.60859), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.97084,18.62416,-71.02917,18.60859), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.97084,18.62416,-71.02917,18.60859), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.97084,18.62416,-71.02917,18.60859), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.34639,18.64444,-71.02917,18.60666), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.34639,18.64444,-71.02917,18.60666), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.34639,18.64444,-71.02917,18.60666), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.34639,18.64444,-71.02917,18.60666), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.72639,18.7161,-71.39667,19.69749), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.72639,18.7161,-71.39667,19.69749), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.72639,18.7161,-71.39667,19.69749), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.72639,18.7161,-71.39667,19.69749), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.71001,18.79889,-71.39667,19.69749), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.71001,18.79889,-71.39667,19.69749), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.71001,18.79889,-71.39667,19.69749), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.71001,18.79889,-71.39667,19.69749), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.64418,18.87416,-71.02917,18.21333), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.64418,18.87416,-71.02917,18.21333), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.64418,18.87416,-71.02917,18.21333), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.64418,18.87416,-71.02917,18.21333), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.77917,18.95777,-71.39667,19.77277), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.77917,18.95777,-71.39667,19.77277), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.77917,18.95777,-71.39667,19.77277), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.77917,18.95777,-71.39667,19.77277), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.85388,18.96888,-71.02917,18.47721), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.85388,18.96888,-71.02917,18.47721), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.85388,18.96888,-71.02917,18.47721), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.85388,18.96888,-71.02917,18.47721), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.13863,19.01166,-71.39667,19.30111), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.13863,19.01166,-71.39667,19.30111), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.13863,19.01166,-71.39667,19.30111), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.13863,19.01166,-71.39667,19.30111), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.19862,19.01333,-71.39667,19.18805), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.19862,19.01333,-71.39667,19.18805), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.19862,19.01333,-71.39667,19.18805), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.19862,19.01333,-71.39667,19.18805), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.99918,19.01527,-71.02917,18.41805), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.99918,19.01527,-71.02917,18.41805), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.99918,19.01527,-71.02917,18.41805), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.99918,19.01527,-71.02917,18.41805), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-68.92668,19.03083,-71.02917,18.41805), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-68.92668,19.03083,-71.02917,18.41805), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-68.92668,19.03083,-71.02917,18.41805), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-68.92668,19.03083,-71.02917,18.41805), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.62361,19.08916,-71.02917,18.40638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.62361,19.08916,-71.02917,18.40638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.62361,19.08916,-71.02917,18.40638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.62361,19.08916,-71.02917,18.40638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.67833,19.09888,-71.02917,17.75916), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.41556,19.10583,-71.02917,18.42638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.41556,19.10583,-71.02917,18.42638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.41556,19.10583,-71.02917,18.42638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.41556,19.10583,-71.02917,18.42638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.63722,19.1161,-71.02917,18.40638), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.63722,19.1161,-71.02917,18.40638), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.63722,19.1161,-71.02917,18.40638), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.63722,19.1161,-71.02917,18.40638), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.21667,19.18805,-71.39667,19.36527), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.21667,19.18805,-71.39667,19.36527), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.21667,19.18805,-71.39667,19.36527), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.21667,19.18805,-71.39667,19.36527), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.62944,19.21971,-71.02917,17.81805), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.62944,19.21971,-71.02917,17.81805), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.62944,19.21971,-71.02917,17.81805), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.62944,19.21971,-71.02917,17.81805), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.60973,19.22666,-71.39667,19.08916), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.60973,19.22666,-71.39667,19.08916), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.60973,19.22666,-71.39667,19.08916), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.60973,19.22666,-71.39667,19.08916), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.69638,19.24166,-71.02917,18.32222), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.69638,19.24166,-71.02917,18.32222), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.69638,19.24166,-71.02917,18.32222), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.69638,19.24166,-71.02917,18.32222), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.24084,19.28999,-71.39667,19.36527), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.24084,19.28999,-71.39667,19.36527), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.24084,19.28999,-71.39667,19.36527), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.24084,19.28999,-71.39667,19.36527), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.75862,19.29222,-71.02917,18.45555), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.75862,19.29222,-71.02917,18.45555), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.75862,19.29222,-71.02917,18.45555), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.75862,19.29222,-71.02917,18.45555), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.16251,19.30111,-71.39667,19.01166), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.16251,19.30111,-71.39667,19.01166), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.16251,19.30111,-71.39667,19.01166), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.16251,19.30111,-71.39667,19.01166), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.76695,19.33194,-71.02917,18.20221), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.76695,19.33194,-71.02917,18.20221), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.76695,19.33194,-71.02917,18.20221), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.76695,19.33194,-71.02917,18.20221), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.73972,19.34555,-71.39667,19.76749), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.73972,19.34555,-71.39667,19.76749), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.73972,19.34555,-71.39667,19.76749), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.73972,19.34555,-71.39667,19.76749), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.26889,19.34888,-71.39667,19.28999), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.26889,19.34888,-71.39667,19.28999), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.26889,19.34888,-71.39667,19.28999), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.26889,19.34888,-71.39667,19.28999), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.23279,19.36527,-71.39667,19.28999), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.23279,19.36527,-71.39667,19.28999), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.23279,19.36527,-71.39667,19.28999), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.23279,19.36527,-71.39667,19.28999), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.87807,19.445,-71.39667,19.60027), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.87807,19.445,-71.39667,19.60027), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.87807,19.445,-71.39667,19.60027), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.87807,19.445,-71.39667,19.60027), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.69221,19.49833,-71.02917,18.32222), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.69221,19.49833,-71.02917,18.32222), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.69221,19.49833,-71.02917,18.32222), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.69221,19.49833,-71.02917,18.32222), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.88167,19.60027,-71.02917,18.46944), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.88167,19.60027,-71.02917,18.46944), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.88167,19.60027,-71.02917,18.46944), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.88167,19.60027,-71.02917,18.46944), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.125,19.62083,-71.02917,18.23306), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.125,19.62083,-71.02917,18.23306), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.125,19.62083,-71.02917,18.23306), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.125,19.62083,-71.02917,18.23306), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.89862,19.63721,-71.02917,18.46944), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.89862,19.63721,-71.02917,18.46944), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.89862,19.63721,-71.02917,18.46944), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.89862,19.63721,-71.02917,18.46944), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.30334,19.65499,-71.02917,18.23306), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.30334,19.65499,-71.02917,18.23306), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.30334,19.65499,-71.02917,18.23306), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.30334,19.65499,-71.02917,18.23306), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-69.98056,19.6786,-71.02917,18.41555), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-69.98056,19.6786,-71.02917,18.41555), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-69.98056,19.6786,-71.02917,18.41555), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-69.98056,19.6786,-71.02917,18.41555), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.71973,19.69749,-71.02917,18.7161), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.71973,19.69749,-71.02917,18.7161), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.71973,19.69749,-71.02917,18.7161), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.71973,19.69749,-71.02917,18.7161), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.75148,19.70017,-71.02917,18.09555), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.75148,19.70017,-71.02917,18.09555), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.75148,19.70017,-71.02917,18.09555), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.75148,19.70017,-71.02917,18.09555), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.63501,19.76083,-71.02917,18.43499), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.63501,19.76083,-71.02917,18.43499), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.63501,19.76083,-71.02917,18.43499), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.63501,19.76083,-71.02917,18.43499), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.74057,19.76749,-71.39667,19.34555), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.74057,19.76749,-71.39667,19.34555), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.74057,19.76749,-71.39667,19.34555), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.74057,19.76749,-71.39667,19.34555), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.77725,19.77277,-71.39667,18.95777), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.77725,19.77277,-71.39667,18.95777), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.77725,19.77277,-71.39667,18.95777), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.77725,19.77277,-71.39667,18.95777), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.455,19.7836,-71.02917,18.20583), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.455,19.7836,-71.02917,18.20583), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.455,19.7836,-71.02917,18.20583), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.455,19.7836,-71.02917,18.20583), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.26112,19.82305,-71.02917,17.84666), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.26112,19.82305,-71.02917,17.84666), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.26112,19.82305,-71.02917,17.84666), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.26112,19.82305,-71.02917,17.84666), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.96028,19.90027,-71.02917,18.25305), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.96028,19.90027,-71.02917,18.25305), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.96028,19.90027,-71.02917,18.25305), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.96028,19.90027,-71.02917,18.25305), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.60556,19.90222,-71.02917,17.81805), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.60556,19.90222,-71.02917,17.81805), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.60556,19.90222,-71.02917,17.81805), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.60556,19.90222,-71.02917,17.81805), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.48584,19.90472,-71.02917,17.74055), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.48584,19.90472,-71.02917,17.74055), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.48584,19.90472,-71.02917,17.74055), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.48584,19.90472,-71.02917,17.74055), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-70.84862,19.90499,-71.02917,18.34444), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-70.84862,19.90499,-71.02917,18.34444), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-70.84862,19.90499,-71.02917,18.34444), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-70.84862,19.90499,-71.02917,18.34444), mapfile, tile_dir, 17, 17, "do-dominican-republic")
    render_tiles((-71.02917,19.92833,-71.02917,18.30527), mapfile, tile_dir, 0, 11, "do-dominican-republic")
    render_tiles((-71.02917,19.92833,-71.02917,18.30527), mapfile, tile_dir, 13, 13, "do-dominican-republic")
    render_tiles((-71.02917,19.92833,-71.02917,18.30527), mapfile, tile_dir, 15, 15, "do-dominican-republic")
    render_tiles((-71.02917,19.92833,-71.02917,18.30527), mapfile, tile_dir, 17, 17, "do-dominican-republic")