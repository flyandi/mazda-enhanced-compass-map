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
    # Region: NL
    # Region Name: Netherlands

	render_tiles((3.95722,51.21609,3.80639,51.21693), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.80639,51.21693,3.95722,51.21609), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.43917,51.24443,3.52417,51.25054), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.52417,51.25054,3.43917,51.24443), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.79306,51.26193,3.52417,51.25054), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.52472,51.28832,3.36389,51.3136), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.36389,51.3136,4.215,51.33027), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.215,51.33027,3.83278,51.34165), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.83278,51.34165,4.215,51.33027), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.23001,51.35722,3.96139,51.36971), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.23001,51.35722,3.96139,51.36971), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.96139,51.36971,4.21083,51.37249), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.21083,51.37249,3.96139,51.36971), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.37065,51.37555,4.21083,51.37249), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.60111,51.39193,3.44333,51.39277), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.44333,51.39277,3.60111,51.39193), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.98194,51.40721,3.44333,51.39277), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.00841,50.75607,5.69861,50.75777), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.69861,50.75777,6.00841,50.75607), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.98167,50.80276,5.69861,50.75777), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.08472,50.8736,5.68374,50.88219), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.68374,50.88219,6.08472,50.8736), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.01083,50.9436,5.75917,50.94915), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.75917,50.94915,6.01083,50.9436), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.72278,50.96526,6.02833,50.97665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.02833,50.97665,5.96583,50.97832), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.96583,50.97832,6.02833,50.97665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.86944,51.01888,5.77583,51.0211), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.77583,51.0211,5.86944,51.01888), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.95222,51.03665,5.87389,51.05026), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.87389,51.05026,5.95222,51.03665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.80278,51.09332,5.87389,51.05026), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.85546,51.14782,6.16722,51.16276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.85546,51.14782,6.16722,51.16276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.16722,51.16276,6.07944,51.17582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.07944,51.17582,6.16722,51.16276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.07389,51.22054,5.23889,51.26221), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.23889,51.26221,5.45778,51.28054), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.45778,51.28054,5.23889,51.26221), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.2375,51.30832,5.14389,51.3186), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.14389,51.3186,5.2375,51.30832), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.41305,51.35804,6.22222,51.36166), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.22222,51.36166,4.41305,51.35804), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.23644,51.36877,4.23944,51.37415), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.23944,51.37415,4.43778,51.37526), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.43778,51.37526,4.23944,51.37415), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.07722,51.39526,3.90667,51.39999), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.90667,51.39999,4.94028,51.40137), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.94028,51.40137,3.90667,51.39999), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.20028,51.40804,4.06639,51.41415), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.06639,51.41415,4.78278,51.4147), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.78278,51.4147,3.74417,51.41471), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.74417,51.41471,4.78278,51.4147), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.39667,51.41693,3.74417,51.41471), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.81639,51.42554,4.54194,51.42665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.54194,51.42665,4.67167,51.42748), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.67167,51.42748,4.54194,51.42665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.10444,51.43498,4.67167,51.42748), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.58028,51.4436,4.27722,51.44527), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.11111,51.4436,4.27722,51.44527), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.27722,51.44527,3.58028,51.4436), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.93028,51.4486,4.27722,51.44527), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.98361,51.45638,4.41167,51.45693), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.41167,51.45693,3.98361,51.45638), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.69139,51.4586,4.41167,51.45693), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.84472,51.46137,3.69139,51.4586), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.29361,51.46832,4.84472,51.46137), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.2175,51.47637,4.54944,51.48276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.54944,51.48276,5.04139,51.48665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.04139,51.48665,4.54944,51.48276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.82583,51.49221,5.04139,51.48665), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.76917,51.50277,4.26694,51.50832), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.26694,51.50832,4.76917,51.50277), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.4525,51.5211,4.26694,51.50832), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.44639,51.54193,3.4525,51.5211), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.90361,51.56888,3.99555,51.57943), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.99555,51.57943,3.90361,51.56888), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.20861,51.58999,3.56889,51.59721), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.56889,51.59721,4.0275,51.6011), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.0275,51.6011,3.8625,51.60221), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.8625,51.60221,4.0275,51.6011), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.09305,51.60721,3.8625,51.60221), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.15389,51.61443,3.99555,51.61804), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.99555,51.61804,4.15389,51.61443), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.89917,51.63527,6.11611,51.65192), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.11611,51.65192,3.89917,51.63527), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.72056,51.67249,6.02944,51.6786), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.02944,51.6786,3.72056,51.67249), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.68778,51.68943,3.82861,51.69138), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.82861,51.69138,3.68778,51.68943), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.03917,51.71693,3.69361,51.72083), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.69361,51.72083,6.03917,51.71693), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.955,51.73859,3.69361,51.72083), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((3.87024,51.7859,5.96889,51.7911), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.96889,51.7911,3.87024,51.7859), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.38028,51.82999,5.96194,51.83027), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.96194,51.83027,6.38028,51.82999), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.16972,51.84193,5.96194,51.83027), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.06417,51.85749,6.16972,51.84193), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.13778,51.87693,4.02611,51.88416), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.02611,51.88416,6.54889,51.88526), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.54889,51.88526,4.02611,51.88416), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.72778,51.89943,6.15972,51.90554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.15972,51.90554,6.72778,51.89943), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.05222,51.91527,6.15972,51.90554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.83083,51.97137,4.11111,51.98554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.11111,51.98554,4.02111,51.98804), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.02111,51.98804,4.11111,51.98554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.80055,52.00721,4.02111,51.98804), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.68805,52.03888,6.6975,52.06998), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.6975,52.06998,6.68805,52.03888), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.86056,52.12026,6.87972,52.15359), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.87972,52.15359,6.86056,52.12026), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.0525,52.23582,4.43583,52.24666), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.43583,52.24666,7.0525,52.23582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.06556,52.38582,6.94611,52.43443), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.94611,52.43443,6.9875,52.4611), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.9875,52.4611,6.70555,52.48582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.70555,52.48582,4.58861,52.48943), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.58861,52.48943,6.70555,52.48582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.69028,52.55193,6.76083,52.56721), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.76083,52.56721,6.69028,52.55193), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.72083,52.62943,7.03583,52.63276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.03583,52.63276,6.72083,52.62943), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.05528,52.65192,6.78167,52.65415), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.78167,52.65415,7.05528,52.65192), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.06972,52.81499,4.71944,52.89082), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.71944,52.89082,4.8725,52.89721), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.8725,52.89721,4.71944,52.89082), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.80861,52.92416,4.8725,52.89721), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.08583,52.95527,4.73417,52.95554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.73417,52.95554,5.08583,52.95527), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((4.82056,52.96082,4.73417,52.95554), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.19889,52.96776,4.82056,52.96082), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.37861,53.09165,5.4425,53.21193), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.4425,53.21193,7.20944,53.24276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.20944,53.24276,7.09639,53.25471), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.09639,53.25471,7.20944,53.24276), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((7.07389,53.28805,7.09639,53.25471), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.70361,53.32749,6.90167,53.35027), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.90167,53.35027,5.70361,53.32749), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((5.915,53.38415,6.30361,53.39582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.30361,53.39582,5.915,53.38415), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.0925,53.41082,6.30361,53.39582), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.87139,53.43193,6.0925,53.41082), mapfile, tile_dir, 0, 11, "nl-netherlands")
	render_tiles((6.74194,53.46582,6.87139,53.43193), mapfile, tile_dir, 0, 11, "nl-netherlands")