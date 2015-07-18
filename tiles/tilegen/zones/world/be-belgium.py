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
    # Region: BE
    # Region Name: Belgium

	render_tiles((5.47305,49.5061,5.78304,49.52727), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.78304,49.52727,5.70521,49.53523), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.70521,49.53523,5.78304,49.52727), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.84,49.55221,5.70521,49.53523), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.42333,49.60915,5.30722,49.63081), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.30722,49.63081,5.42333,49.60915), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.32972,49.65998,5.89917,49.66276), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.89917,49.66276,5.32972,49.65998), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.21694,49.69054,5.27611,49.69859), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.27611,49.69859,5.21694,49.69054), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.82056,49.74915,5.09778,49.76859), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.09778,49.76859,5.82056,49.74915), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.74667,49.79527,4.85805,49.79638), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.85805,49.79638,5.74667,49.79527), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.75333,49.84915,5.73111,49.89415), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.73111,49.89415,4.88111,49.9147), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.88111,49.9147,5.73111,49.89415), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.45833,49.93859,5.80833,49.9611), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.80833,49.9611,4.80083,49.97776), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.80083,49.97776,4.16833,49.98137), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.16833,49.98137,4.80083,49.97776), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.15,49.98637,4.16833,49.98137), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.67361,49.99638,4.15,49.98637), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.81972,50.00971,4.67361,49.99638), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.85194,50.07943,4.22639,50.0811), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.22639,50.0811,4.85194,50.07943), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.13183,50.12553,4.7575,50.12943), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.7575,50.12943,6.13183,50.12553), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.13778,50.13776,4.7575,50.12943), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.87639,50.15498,6.10694,50.16776), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.10694,50.16776,5.98,50.17221), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.98,50.17221,6.10694,50.16776), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.17333,50.23248,4.21611,50.26527), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.21611,50.26527,6.17333,50.23248), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.72528,50.3136,4.09083,50.31443), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.09083,50.31443,3.72528,50.3136), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.40028,50.32915,4.09083,50.31443), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.67194,50.34637,3.76361,50.35193), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.76361,50.35193,3.67194,50.34637), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.66889,50.44415,6.36639,50.45221), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.36639,50.45221,3.66889,50.44415), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.36722,50.49554,3.60222,50.49721), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.60222,50.49721,3.36722,50.49554), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.20083,50.51638,3.60222,50.49721), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.28,50.53832,6.20083,50.51638), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.28139,50.59415,6.26861,50.6236), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.26861,50.6236,6.17139,50.62387), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.17139,50.62387,6.26861,50.6236), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.90128,50.69705,6.02861,50.71582), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.02861,50.71582,6.10833,50.72331), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.10833,50.72331,6.02861,50.71582), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.19333,50.74137,2.78194,50.75555), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.78194,50.75555,6.00841,50.75607), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((6.00841,50.75607,2.78194,50.75555), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.69861,50.75777,6.00841,50.75607), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.03528,50.77387,5.69861,50.75777), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.11305,50.79332,3.03528,50.77387), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.62944,50.82471,3.11305,50.79332), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.68374,50.88219,2.6125,50.88721), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.6125,50.88721,5.68374,50.88219), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.75917,50.94915,5.72278,50.96526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.72278,50.96526,5.75917,50.94915), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.77583,51.0211,5.72278,50.96526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.54167,51.0911,5.80278,51.09332), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.80278,51.09332,2.54167,51.0911), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.85546,51.14782,5.80278,51.09332), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.95722,51.21609,3.80639,51.21693), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.80639,51.21693,3.95722,51.21609), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.43917,51.24443,3.52417,51.25054), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.52417,51.25054,3.43917,51.24443), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((2.97167,51.25749,3.79306,51.26193), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.79306,51.26193,5.23889,51.26221), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.23889,51.26221,3.79306,51.26193), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.45778,51.28054,3.52472,51.28832), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.52472,51.28832,5.45778,51.28054), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.29389,51.30638,5.2375,51.30832), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.2375,51.30832,4.29389,51.30638), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.36389,51.3136,5.14389,51.3186), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.14389,51.3186,3.36389,51.3136), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.215,51.33027,5.14389,51.3186), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.23001,51.35722,4.41305,51.35804), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.41305,51.35804,4.23001,51.35722), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.23644,51.36877,4.23944,51.37415), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.23944,51.37415,4.43778,51.37526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.43778,51.37526,3.37065,51.37555), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.37065,51.37555,4.43778,51.37526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((3.37065,51.37555,4.43778,51.37526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.07722,51.39526,4.94028,51.40137), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.94028,51.40137,5.07722,51.39526), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.78278,51.4147,4.39667,51.41693), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.39667,51.41693,4.78278,51.4147), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.81639,51.42554,4.54194,51.42665), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.54194,51.42665,4.67167,51.42748), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.67167,51.42748,4.54194,51.42665), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.10444,51.43498,4.67167,51.42748), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.41167,51.45693,4.84472,51.46137), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.84472,51.46137,4.41167,51.45693), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.54944,51.48276,5.04139,51.48665), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((5.04139,51.48665,4.54944,51.48276), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.82583,51.49221,5.04139,51.48665), mapfile, tile_dir, 0, 11, "be-belgium")
	render_tiles((4.76917,51.50277,4.82583,51.49221), mapfile, tile_dir, 0, 11, "be-belgium")