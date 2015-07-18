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
    # Region: CU
    # Region Name: Cuba

	render_tiles((-82.85196,21.43833,-83.06639,21.46027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.85196,21.43833,-83.06639,21.46027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.06639,21.46027,-82.85196,21.43833), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.63196,21.52083,-82.95529,21.56222), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.95529,21.56222,-82.9789,21.57777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.9789,21.57777,-82.54333,21.58972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.54333,21.58972,-82.93445,21.59083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.93445,21.59083,-82.54333,21.58972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.18028,21.59944,-82.93445,21.59083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.60667,21.76138,-82.63417,21.76194), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.63417,21.76194,-82.60667,21.76138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.08362,21.77722,-82.63417,21.76194), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.07556,21.83778,-82.69751,21.88805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.69751,21.88805,-82.96056,21.92388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.96056,21.92388,-82.83362,21.9286), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.83362,21.9286,-82.96056,21.92388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.68111,19.82194,-77.73528,19.84777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.73528,19.84777,-77.39334,19.85277), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.39334,19.85277,-77.73528,19.84777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.34973,19.87638,-77.33751,19.88527), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.005,19.87638,-77.33751,19.88527), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.33751,19.88527,-75.15862,19.88778), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.15862,19.88778,-75.5864,19.88832), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.5864,19.88832,-75.15862,19.88778), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.07472,19.89638,-75.5864,19.88832), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.28139,19.90555,-77.71278,19.91082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.71278,19.91082,-77.28139,19.90555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.19221,19.91749,-77.71278,19.91082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.86472,19.93193,-75.13055,19.93305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.13055,19.93305,-76.86472,19.93193), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.56696,19.93805,-75.13055,19.93305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.89973,19.96749,-76.10056,19.96777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.10056,19.96777,-75.89973,19.96749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.14,19.96805,-76.10056,19.96777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.15862,19.97471,-75.14,19.96805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.24889,19.99083,-75.15862,19.97471), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.0775,20.01056,-76.24889,19.99083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.81139,20.03611,-77.57362,20.05555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.57362,20.05555,-75.09277,20.05638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.09277,20.05638,-77.57362,20.05555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.26695,20.06361,-75.09277,20.05638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.13194,20.19971,-74.13194,20.2211), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.13194,20.2211,-74.13194,20.19971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.21666,20.27583,-77.24277,20.29444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.24277,20.29444,-74.34999,20.29499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.34999,20.29499,-77.24277,20.29444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.22888,20.31583,-74.34999,20.29499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.50111,20.35166,-77.11583,20.36499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.11583,20.36499,-74.50111,20.35166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.58417,20.4661,-77.07834,20.46999), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.07834,20.46999,-74.58417,20.4661), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.66695,20.51333,-77.23917,20.55555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.23917,20.55555,-74.66695,20.51333), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.75111,20.61805,-74.86417,20.62999), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.86417,20.62999,-77.19527,20.63222), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.19527,20.63222,-74.86417,20.62999), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.33778,20.66555,-74.90028,20.6661), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-74.90028,20.6661,-75.33778,20.66555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.25696,20.66832,-74.90028,20.6661), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.06332,20.68638,-75.44943,20.68888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.44943,20.68888,-75.06332,20.68638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.76917,20.69638,-78.03139,20.69749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.03139,20.69749,-77.76917,20.69638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.53362,20.6986,-78.03139,20.69749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.74083,20.69999,-75.53362,20.6986), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.33139,20.70444,-75.74083,20.69999), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.34389,20.71221,-77.32362,20.71388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.32362,20.71388,-75.34389,20.71221), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.23639,20.72305,-75.47362,20.72471), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.47362,20.72471,-75.68028,20.72555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.68028,20.72555,-75.47362,20.72471), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.86082,20.73027,-75.39584,20.73333), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.39584,20.73333,-77.86082,20.73027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.77917,20.75528,-75.62888,20.77444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.62888,20.77444,-75.56082,20.79194), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.56082,20.79194,-75.62888,20.77444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.54778,20.81805,-75.74222,20.83388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.74222,20.83388,-75.54778,20.81805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.72084,20.85416,-78.24861,20.85888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.24861,20.85888,-75.72084,20.85416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.64168,20.87722,-75.67833,20.89249), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.67833,20.89249,-75.64168,20.87722), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.72888,20.91388,-75.67833,20.89249), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.33195,20.94583,-75.72888,20.91388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.57973,21.00694,-78.46083,21.01583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.46083,21.01583,-75.57973,21.00694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.49777,21.03444,-78.46083,21.01583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.60861,21.06083,-78.49777,21.03444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.12361,21.09305,-75.71306,21.1236), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-75.71306,21.1236,-76.12361,21.09305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.13194,21.15916,-75.71306,21.1236), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.49527,21.20055,-76.60638,21.21305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.60638,21.21305,-76.45667,21.22277), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.45667,21.22277,-76.54944,21.23082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.54944,21.23082,-76.45667,21.22277), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.52917,21.24027,-76.54944,21.23082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.3264,21.25388,-76.64,21.25555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.64,21.25555,-76.3264,21.25388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.52084,21.27777,-76.54527,21.28388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.54527,21.28388,-76.71194,21.28694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.71194,21.28694,-76.54527,21.28388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.655,21.29166,-76.71194,21.28694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.61555,21.30138,-76.87277,21.30249), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.87277,21.30249,-76.61555,21.30138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.90195,21.31499,-76.63583,21.32083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.63583,21.32083,-76.90195,21.31499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.82897,21.35291,-76.65834,21.35305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.65834,21.35305,-76.82897,21.35291), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.83696,21.35604,-76.88222,21.3561), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.88222,21.3561,-76.83696,21.35604), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.55888,21.35694,-76.88222,21.3561), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.8011,21.38499,-76.81139,21.39333), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.81139,21.39333,-76.8011,21.38499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-76.9164,21.43832,-77.21056,21.45416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.21056,21.45416,-76.9164,21.43832), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.15639,21.47971,-78.60333,21.48416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.60333,21.48416,-77.15639,21.47971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.26973,21.49083,-78.60333,21.48416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.66695,21.5486,-77.15834,21.55083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.15834,21.55083,-78.66695,21.5486), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.3036,21.55888,-77.15834,21.55083), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.2525,21.56833,-77.31555,21.57694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.74889,21.56833,-77.31555,21.57694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.31555,21.57694,-78.68805,21.58027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.68805,21.58027,-77.31555,21.57694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.12193,21.59333,-77.10666,21.5961), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.10666,21.5961,-77.12193,21.59333), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.08168,21.60166,-77.10666,21.5961), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.26472,21.61249,-77.36,21.61971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.36,21.61971,-77.13583,21.62638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.13583,21.62638,-77.36,21.61971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.34639,21.63416,-78.74055,21.63472), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.74055,21.63472,-77.34639,21.63416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.33528,21.65583,-77.43277,21.65749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.43277,21.65749,-77.33528,21.65583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.84111,21.66832,-77.43277,21.65749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.75778,21.70277,-79.82112,21.70555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.82112,21.70555,-79.75778,21.70277), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.455,21.71027,-79.82112,21.70555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.00473,21.71888,-77.455,21.71027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.44444,21.75,-79.89696,21.75388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.89696,21.75388,-77.44444,21.75), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.48332,21.76472,-84.51584,21.76583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.51584,21.76583,-77.48332,21.76472), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.43639,21.78138,-84.51584,21.76583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.44833,21.80249,-77.75639,21.80444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.75639,21.80444,-84.51306,21.80555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.51306,21.80555,-80.05722,21.80583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.05722,21.80583,-84.51306,21.80555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.81555,21.81583,-80.05722,21.80583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.93694,21.84083,-77.49527,21.84666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.49527,21.84666,-77.56807,21.84722), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.56807,21.84722,-77.49527,21.84666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.95334,21.85999,-77.88806,21.8661), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.88806,21.8661,-84.47084,21.87027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.47084,21.87027,-77.88806,21.8661), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.30611,21.87666,-77.86583,21.87916), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.86583,21.87916,-84.30611,21.87666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.89806,21.88416,-77.61166,21.8886), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.61166,21.8886,-84.89806,21.88416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.69499,21.89694,-80.28139,21.89777), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.28139,21.89777,-84.69499,21.89694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.85083,21.90277,-77.52528,21.90722), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.52528,21.90722,-84.85083,21.90277), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.03139,21.91305,-84.47694,21.91721), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.47694,21.91721,-84.92389,21.91944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.92389,21.91944,-84.47694,21.91721), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.92389,21.91944,-84.47694,21.91721), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.57001,21.92582,-84.92389,21.91944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.495,21.93444,-84.10777,21.93971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.10777,21.93971,-83.99083,21.94444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.99083,21.94444,-84.10777,21.93971), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-77.85638,21.96749,-83.99083,21.94444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.45473,22.01221,-83.9825,22.01805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.9825,22.01805,-84.28612,22.01888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.28612,22.01888,-83.9825,22.01805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.44444,22.04305,-84.38612,22.04361), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.38612,22.04361,-80.44444,22.04305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.49695,22.0461,-84.38612,22.04361), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.48778,22.05111,-84.00723,22.05555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.00723,22.05555,-80.48778,22.05111), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.45084,22.06388,-84.00723,22.05555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.46306,22.07527,-78.06055,22.0811), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.06055,22.0811,-81.31917,22.08611), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.31917,22.08611,-81.09584,22.08805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.09584,22.08805,-81.31917,22.08611), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.40111,22.08999,-81.09584,22.08805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.19249,22.09305,-80.40111,22.08999), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.21722,22.10471,-81.3864,22.11527), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.3864,22.11527,-81.21722,22.10471), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.96611,22.13027,-81.3864,22.11527), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.38194,22.15305,-81.23805,22.15332), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.23805,22.15332,-81.38194,22.15305), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.1389,22.16416,-78.31221,22.16666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.31221,22.16666,-81.1389,22.16416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.65417,22.16944,-83.90527,22.17138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.90527,22.17138,-83.65417,22.16944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.47694,22.17416,-80.53528,22.17582), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.53528,22.17582,-83.47694,22.17416), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.40639,22.17833,-80.53528,22.17582), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.48222,22.18888,-83.40222,22.19388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.40222,22.19388,-80.48222,22.18888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.44444,22.20082,-83.40222,22.19388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.84973,22.21361,-78.36362,22.2225), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.36362,22.2225,-81.84973,22.21361), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.56946,22.24249,-78.45056,22.25138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.45056,22.25138,-81.87389,22.25417), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.87389,22.25417,-78.45056,22.25138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.16194,22.2611,-81.21362,22.265), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.21362,22.265,-81.16194,22.2611), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.18555,22.28027,-81.21362,22.265), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.55916,22.31444,-78.7386,22.31499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.7386,22.31499,-78.55916,22.31444), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.28333,22.32166,-83.1825,22.32666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.1825,22.32666,-83.28333,22.32166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.71472,22.34305,-83.14029,22.35666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.14029,22.35666,-82.13806,22.36694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.13806,22.36694,-83.14029,22.35666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.23306,22.38333,-78.73582,22.3861), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.73582,22.3861,-79.23306,22.38333), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-78.79056,22.39499,-82.16306,22.39833), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.16306,22.39833,-78.79056,22.39499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.12389,22.43166,-81.70389,22.45388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.70389,22.45388,-84.30556,22.47166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.30556,22.47166,-83.07362,22.47972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.07362,22.47972,-84.30556,22.47166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.6489,22.49138,-83.07362,22.47972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.21501,22.51194,-81.6489,22.49138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.91,22.55222,-81.64473,22.5761), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.64473,22.5761,-84.18222,22.57916), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.18222,22.57916,-81.64473,22.5761), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.08583,22.64888,-79.62582,22.65166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.62582,22.65166,-84.08583,22.64888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.02528,22.67277,-81.87888,22.67916), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.87888,22.67916,-83.99388,22.68221), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.99388,22.68221,-81.87888,22.67916), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.50723,22.68721,-83.99388,22.68221), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.07056,22.69721,-82.7639,22.70055), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.7639,22.70055,-84.07056,22.69721), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-84.02528,22.71555,-82.7639,22.70055), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.65417,22.75944,-79.72472,22.76944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.72472,22.76944,-79.65417,22.75944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.85445,22.80361,-83.58722,22.82666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.58722,22.82666,-79.86389,22.83583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.86389,22.83583,-83.58722,22.82666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.91222,22.84944,-79.86389,22.83583), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.39445,22.86749,-79.84666,22.87499), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-79.84666,22.87499,-83.39445,22.86749), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.44249,22.90027,-80.27888,22.90527), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.27888,22.90527,-83.44249,22.90027), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.16861,22.92638,-80.0775,22.92888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.0775,22.92888,-83.16861,22.92638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.15472,22.93666,-80.0775,22.92888), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.1886,22.95638,-80.04056,22.95832), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.04056,22.95832,-83.1886,22.95638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.31055,22.95832,-83.1886,22.95638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.1389,22.96111,-80.04056,22.95832), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.98444,22.96916,-80.1389,22.96111), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.16444,22.97943,-82.93056,22.98082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.93056,22.98082,-83.16444,22.97943), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.17639,22.98388,-82.93056,22.98082), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.54472,22.9911,-83.17639,22.98388), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.21695,23.00139,-80.54472,22.9911), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-83.00862,23.01555,-82.92639,23.01944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.92639,23.01944,-83.00862,23.01555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.12277,23.02555,-82.92639,23.01944), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.17639,23.03249,-81.12277,23.02555), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.55305,23.0461,-81.50751,23.04805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.50751,23.04805,-82.59,23.0486), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.59,23.0486,-81.50751,23.04805), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.9725,23.05916,-82.59,23.0486), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.67027,23.08472,-80.62166,23.09166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.62166,23.09166,-81.21973,23.09249), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.21973,23.09249,-80.62166,23.09166), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.07973,23.09694,-81.48277,23.09972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.48277,23.09972,-81.07973,23.09694), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.52722,23.1036,-81.48277,23.09972), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.62111,23.10833,-80.99277,23.10861), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.99277,23.10861,-80.62111,23.10833), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.28555,23.11971,-80.99277,23.10861), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.55333,23.14055,-80.58806,23.14666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.58806,23.14666,-80.63556,23.15138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-80.63556,23.15138,-80.58806,23.14666), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.62,23.15833,-80.63556,23.15138), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-81.20195,23.18166,-82.23444,23.18638), mapfile, tile_dir, 0, 11, "cu-cuba")
	render_tiles((-82.23444,23.18638,-81.20195,23.18166), mapfile, tile_dir, 0, 11, "cu-cuba")