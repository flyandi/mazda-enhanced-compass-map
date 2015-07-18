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
    # Region: Washington
    # Region Name: WA

	render_tiles((-122.87414,48.4182,-122.80352,48.42875), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.80352,48.42875,-122.87414,48.4182), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.928,48.43997,-122.80352,48.42875), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.03916,48.46,-122.928,48.43997), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.81791,48.48389,-123.14148,48.50529), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.14148,48.50529,-122.77912,48.50891), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.77912,48.50891,-123.14148,48.50529), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.77121,48.56243,-123.20268,48.59021), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.20268,48.59021,-122.79901,48.60468), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.79901,48.60468,-123.20268,48.59021), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.74305,48.66199,-123.23715,48.68347), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.23715,48.68347,-123.07043,48.69997), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.23715,48.68347,-123.07043,48.69997), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.07043,48.69997,-123.23715,48.68347), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.0197,48.72131,-123.07043,48.69997), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.81844,48.74463,-123.0197,48.72131), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.97952,48.7817,-122.93793,48.79032), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.93793,48.79032,-122.97952,48.7817), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.26263,45.54432,-122.3315,45.54824), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.3315,45.54824,-122.24889,45.55013), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.24889,45.55013,-122.3315,45.54824), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.43867,45.56359,-122.3803,45.57594), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.3803,45.57594,-122.1837,45.5777), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.1837,45.5777,-122.3803,45.57594), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.49226,45.58328,-122.10168,45.58352), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.10168,45.58352,-122.49226,45.58328), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.18384,45.60644,-122.64391,45.60974), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.64391,45.60974,-121.18384,45.60644), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.00369,45.61593,-121.1222,45.61607), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.1222,45.61607,-122.00369,45.61593), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.89558,45.64295,-122.73811,45.64414), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.73811,45.64414,-121.95184,45.64495), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.95184,45.64495,-122.73811,45.64414), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.08493,45.64789,-120.914,45.64808), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.914,45.64808,-121.08493,45.64789), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.92396,45.65428,-120.94398,45.65645), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.94398,45.65645,-121.92396,45.65428), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.90086,45.66201,-122.75644,45.66242), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.75644,45.66242,-121.90086,45.66201), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.21578,45.67124,-120.85567,45.67155), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.85567,45.67155,-121.21578,45.67124), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.77451,45.68044,-120.85567,45.67155), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.86717,45.69328,-121.42359,45.69399), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.42359,45.69399,-121.7351,45.69404), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.7351,45.69404,-121.42359,45.69399), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.44052,45.69902,-120.40396,45.69925), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.40396,45.69925,-121.44052,45.69902), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.4881,45.69991,-120.50586,45.70005), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.50586,45.70005,-120.4881,45.69991), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.33777,45.70495,-121.66836,45.70508), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.66836,45.70508,-121.33777,45.70495), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.8113,45.70676,-121.66836,45.70508), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.68937,45.71585,-120.28216,45.72125), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.28216,45.72125,-121.52275,45.72346), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.52275,45.72346,-120.28216,45.72125), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.21075,45.72595,-121.53311,45.72654), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.53311,45.72654,-120.21075,45.72595), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.76651,45.72868,-121.53311,45.72654), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.65252,45.73617,-122.76651,45.72868), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.63497,45.74585,-120.59117,45.74655), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.59117,45.74655,-120.63497,45.74585), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.76145,45.75916,-120.59117,45.74655), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.14135,45.77315,-120.07015,45.78515), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.07015,45.78515,-120.14135,45.77315), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.79561,45.81,-119.99951,45.81168), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.99951,45.81168,-122.79561,45.81), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.96574,45.82437,-119.99951,45.81168), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.8681,45.83823,-119.80266,45.84753), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.80266,45.84753,-122.78809,45.85101), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.78809,45.85101,-119.80266,45.84753), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.66988,45.85687,-122.78809,45.85101), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.78503,45.8677,-119.66988,45.85687), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.48783,45.90631,-122.81151,45.91273), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.81151,45.91273,-119.43208,45.91322), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.43208,45.91322,-122.81151,45.91273), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.60055,45.91958,-119.3644,45.92161), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.3644,45.92161,-119.60055,45.91958), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.57158,45.92546,-119.3644,45.92161), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.12612,45.93286,-119.25715,45.93993), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.25715,45.93993,-119.12612,45.93286), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.06146,45.95853,-122.814,45.96098), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.814,45.96098,-119.06146,45.95853), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.91599,45.99541,-117.35393,45.99635), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.35393,45.99635,-116.91599,45.99541), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.48014,45.99757,-117.60316,45.99876), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.60316,45.99876,-118.98713,45.99986), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.98713,45.99986,-117.71785,45.99987), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.71785,45.99987,-118.98713,45.99986), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.97776,46.00017,-117.99691,46.00019), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.99691,46.00019,-117.97776,46.00017), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.36779,46.00062,-118.60679,46.00086), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.60679,46.00086,-118.67787,46.00094), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.67787,46.00094,-118.60679,46.00086), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.85616,46.01447,-118.67787,46.00094), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.94266,46.061,-122.90412,46.08373), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.90412,46.08373,-116.98196,46.08492), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.98196,46.08492,-122.90412,46.08373), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.96268,46.10482,-116.98196,46.08492), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.00423,46.13382,-116.93547,46.14245), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.93547,46.14245,-123.28017,46.14484), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.28017,46.14484,-123.36364,46.14624), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.36364,46.14624,-123.37143,46.14637), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.37143,46.14637,-123.36364,46.14624), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.92396,46.17092,-123.21249,46.1711), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.21249,46.1711,-116.92396,46.17092), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.43085,46.18183,-123.1159,46.18527), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.1159,46.18527,-123.43085,46.18183), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.16641,46.18897,-123.1159,46.18527), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.96297,46.19968,-123.16641,46.18897), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.42763,46.22935,-123.87553,46.23979), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.87553,46.23979,-123.90931,46.24549), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.90931,46.24549,-123.87553,46.23979), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.96438,46.25328,-123.54766,46.25911), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.54766,46.25911,-116.96438,46.25328), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.6695,46.26683,-124.08067,46.26724), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.08067,46.26724,-123.6695,46.26683), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.47964,46.26913,-124.08067,46.26724), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.75956,46.27507,-123.95435,46.277), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.95435,46.277,-123.75956,46.27507), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.80614,46.28359,-123.95435,46.277), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.72789,46.29134,-123.96943,46.2914), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.96943,46.2914,-123.72789,46.29134), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-116.99726,46.30315,-123.70076,46.30528), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.70076,46.30528,-116.99726,46.30315), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.06462,46.3269,-123.70076,46.30528), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.06275,46.35362,-124.06462,46.3269), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03555,46.41001,-117.03661,46.42564), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03661,46.42564,-117.03555,46.41001), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03977,46.47178,-124.05702,46.49334), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.05702,46.49334,-117.03977,46.47178), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03978,46.54178,-124.05702,46.49334), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.06958,46.63065,-123.96064,46.63636), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.96064,46.63636,-124.06958,46.63065), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.92327,46.67271,-123.96064,46.63636), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.97516,46.71397,-124.08098,46.735), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.08098,46.735,-123.97516,46.71397), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.0968,46.79409,-124.10123,46.81066), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.10123,46.81066,-124.0968,46.79409), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.13823,46.90553,-124.18011,46.92636), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.18011,46.92636,-124.13823,46.90553), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.16911,46.99451,-124.18011,46.92636), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03983,47.12726,-117.03984,47.15473), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03984,47.15473,-124.18854,47.15786), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.18854,47.15786,-117.03984,47.15473), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.19589,47.174,-124.18854,47.15786), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04016,47.25927,-124.23635,47.28729), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.23635,47.28729,-117.04016,47.25927), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.31938,47.35556,-117.04049,47.3661), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04049,47.3661,-124.31938,47.35556), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.35363,47.53361,-124.35596,47.5457), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.35596,47.5457,-124.35363,47.53361), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.41211,47.6912,-117.04163,47.7353), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04163,47.7353,-124.47169,47.76691), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.47169,47.76691,-117.04163,47.7353), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.53993,47.83697,-124.61311,47.88057), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.61311,47.88057,-124.62551,47.88796), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.62551,47.88796,-124.61311,47.88057), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.65106,47.92099,-122.6341,47.92304), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.6341,47.92304,-122.65106,47.92099), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.6167,47.92514,-122.6341,47.92304), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.57375,47.951,-124.67243,47.96441), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.67243,47.96441,-122.54682,47.96722), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.54682,47.96722,-124.67243,47.96441), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.70129,47.97298,-117.04131,47.97739), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04131,47.97739,-122.70129,47.97298), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.54292,47.9964,-117.04131,47.97739), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.70184,48.01611,-122.60734,48.03099), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.60734,48.03099,-117.04121,48.04556), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04121,48.04556,-124.68539,48.04924), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.68539,48.04924,-117.04121,48.04556), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.00413,48.09052,-122.94612,48.09855), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.94612,48.09855,-124.6871,48.09866), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.6871,48.09866,-122.94612,48.09855), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.69847,48.1031,-124.6871,48.09866), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.91069,48.1098,-122.5983,48.11062), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.5983,48.11062,-122.91069,48.1098), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.31458,48.11373,-122.5983,48.11062), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.23913,48.11822,-123.06621,48.12047), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.06621,48.12047,-123.23913,48.11822), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.44197,48.12426,-117.04111,48.1249), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.04111,48.1249,-123.44197,48.12426), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.83317,48.13441,-122.76045,48.14324), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.76045,48.14324,-123.55113,48.15138), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.55113,48.15138,-124.72173,48.15319), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.72173,48.15319,-123.55113,48.15138), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.88007,48.16062,-123.67245,48.16272), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.67245,48.16272,-123.72874,48.1628), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.72874,48.1628,-123.67245,48.16272), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.63317,48.16328,-123.72874,48.1628), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-123.14478,48.17594,-124.05073,48.17775), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.05073,48.17775,-123.14478,48.17594), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.71151,48.19357,-124.05073,48.17775), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.10177,48.21688,-124.69039,48.21975), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.69039,48.21975,-124.10177,48.21688), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.75256,48.26006,-124.25088,48.26477), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.25088,48.26477,-122.75256,48.26006), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.38087,48.2847,-124.66927,48.29635), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.66927,48.29635,-124.38087,48.2847), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.70708,48.31529,-124.66927,48.29635), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.54626,48.35359,-124.72584,48.38601), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.72584,48.38601,-124.65324,48.39069), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.72584,48.38601,-124.65324,48.39069), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-124.65324,48.39069,-124.72584,48.38601), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.66698,48.41247,-122.66534,48.41645), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.66534,48.41645,-122.66698,48.41247), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03529,48.42273,-122.66534,48.41645), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.68912,48.47685,-122.65031,48.53016), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.65031,48.53016,-122.68912,48.47685), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.6426,48.58834,-122.66845,48.63957), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.66845,48.63957,-117.03367,48.6569), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03367,48.6569,-122.66845,48.63957), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.71018,48.72224,-117.03367,48.6569), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.72005,48.7892,-122.73251,48.8381), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.73251,48.8381,-117.03294,48.84656), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03294,48.84656,-122.73251,48.8381), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.79402,48.88313,-117.03294,48.84656), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.82163,48.94137,-121.75125,48.9974), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.75125,48.9974,-117.03235,48.99919), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.03235,48.99919,-120.0012,48.99942), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.0012,48.99942,-117.03235,48.99919), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.26825,48.99982,-121.39554,48.99985), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.39554,48.99985,-117.26825,48.99982), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.7166,49.00019,-119.1321,49.00026), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.1321,49.00026,-117.42951,49.00031), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-119.4577,49.00026,-117.42951,49.00031), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.42951,49.00031,-119.1321,49.00026), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.8369,49.00031,-119.1321,49.00026), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.19682,49.00041,-118.00205,49.00044), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-118.00205,49.00044,-118.19682,49.00041), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.8512,49.00059,-120.37622,49.00071), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-120.37622,49.00071,-120.8512,49.00059), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-117.60732,49.00084,-120.37622,49.00071), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-121.12624,49.00141,-117.60732,49.00084), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.09836,49.00215,-122.75802,49.00236), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.75802,49.00236,-122.25106,49.00249), mapfile, tile_dir, 0, 11, "washington-wa")
	render_tiles((-122.25106,49.00249,-122.75802,49.00236), mapfile, tile_dir, 0, 11, "washington-wa")