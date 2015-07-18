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
    # Region: TD
    # Region Name: Chad

	render_tiles((15.78277,7.45778,15.51861,7.51833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.51861,7.51833,15.68916,7.52389), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.68916,7.52389,15.4991,7.52643), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.4991,7.52643,15.68916,7.52389), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.81742,7.54597,15.4991,7.52643), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.25138,7.63722,16.94666,7.64528), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.94666,7.64528,16.25138,7.63722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.62999,7.67694,16.40447,7.67884), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.40447,7.67884,16.62999,7.67694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.58416,7.68667,16.40447,7.67884), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((17.16582,7.72805,16.62916,7.75667), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.62916,7.75667,15.58,7.75833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.58,7.75833,16.62916,7.75667), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.50361,7.77417,16.4036,7.78), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.4036,7.78,15.50361,7.77417), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.56333,7.815,16.4036,7.78), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.56444,7.87333,15.44666,7.8775), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.44666,7.8775,17.39138,7.87778), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((17.39138,7.87778,15.44666,7.8775), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((17.8836,7.95833,17.70847,7.98498), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((17.70847,7.98498,17.8836,7.95833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.0461,8.01667,18.58888,8.04027), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.58888,8.04027,18.0461,8.01667), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.62499,8.06722,18.58888,8.04027), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.63666,8.14583,18.68666,8.21416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.68666,8.21416,18.80944,8.26083), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.80944,8.26083,18.68666,8.21416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.25805,8.35166,18.80944,8.26083), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.19277,8.49722,19.06221,8.57694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((19.06221,8.57694,15.19277,8.49722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((19.12582,8.67277,19.06221,8.57694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.82472,8.81111,14.85277,8.81777), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.85277,8.81777,14.82472,8.81111), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.88408,8.83504,14.85277,8.81777), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.92944,8.92055,19.38888,8.99722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((19.38888,8.99722,19.10944,9.01361), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((19.10944,9.01361,19.38888,8.99722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((19.86499,9.04694,19.10944,9.01361), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.32888,9.10444,14.4175,9.13416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.4175,9.13416,20.06749,9.13527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.06749,9.13527,14.4175,9.13416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.45555,9.15777,20.06749,9.13527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.50194,9.28055,20.66777,9.29833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.66777,9.29833,20.50194,9.28055), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.65916,9.34277,20.70666,9.36416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.70666,9.36416,20.65916,9.34277), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.95332,9.59,13.96027,9.63444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.96027,9.63444,20.95332,9.59), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((20.99694,9.68527,14.01,9.73), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.01,9.73,20.99694,9.68527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.10861,9.81139,14.01,9.73), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.77639,9.92111,15.42222,9.92694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.42222,9.92694,14.77639,9.92111), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.03139,9.94638,21.34222,9.95861), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.34222,9.95861,15.03139,9.94638), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.92027,9.97166,21.26944,9.98111), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.26944,9.98111,14.19055,9.98166), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.19055,9.98166,21.26944,9.98111), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.23277,9.98777,15.67887,9.99243), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.67887,9.99243,15.23277,9.98777), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.45833,9.99888,15.67887,9.99243), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.40833,10.00694,14.45833,9.99888), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.47721,10.15111,15.38778,10.22861), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.38778,10.22861,21.67388,10.24277), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.67388,10.24277,15.38778,10.22861), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.71888,10.3325,21.67388,10.24277), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.13972,10.51916,21.70082,10.52694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.70082,10.52694,15.13972,10.51916), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.72388,10.64194,22.00388,10.73472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.00388,10.73472,15.08722,10.74722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.08722,10.74722,22.00388,10.73472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.05666,10.80555,22.015,10.80777), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.015,10.80777,15.05666,10.80555), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.18444,10.81889,22.015,10.80777), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.06388,10.83416,22.18444,10.81889), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.20193,10.87027,15.07861,10.88944), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.07861,10.88944,22.20193,10.87027), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.88116,10.92391,22.76749,10.955), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.76749,10.955,22.88116,10.92391), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.4661,11.00139,22.76749,10.955), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.0175,11.18611,22.97527,11.21583), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.97527,11.21583,15.0175,11.18611), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.7911,11.40166,22.93082,11.41583), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.93082,11.41583,22.7911,11.40166), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.10555,11.48972,15.14,11.525), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.14,11.525,22.62555,11.53444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.62555,11.53444,15.14,11.525), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.55277,11.66555,15.06277,11.68167), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.06277,11.68167,22.55277,11.66555), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.11111,11.78139,15.06277,11.68167), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.05166,11.945,15.05361,12.03305), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.05361,12.03305,22.47666,12.03416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.47666,12.03416,15.05361,12.03305), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.62555,12.08361,15.04507,12.08408), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.04507,12.08408,22.62555,12.08361), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.90166,12.14277,22.49777,12.18694), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.49777,12.18694,14.90166,12.14277), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.87555,12.44111,22.38388,12.46527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.38388,12.46527,14.87555,12.44111), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.46693,12.62166,21.97083,12.63972), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.97083,12.63972,22.12527,12.65), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.12527,12.65,14.70722,12.65388), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.70722,12.65388,22.12527,12.65), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.74555,12.67333,21.90055,12.67666), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.90055,12.67666,14.74555,12.67333), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.715,12.71028,21.90055,12.67666), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.22332,12.74722,14.55194,12.76361), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.55194,12.76361,22.22332,12.74722), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.82825,12.80389,14.55458,12.82794), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.55458,12.82794,21.82825,12.80389), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.52012,12.86465,14.50805,12.8775), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.50805,12.8775,14.52012,12.86465), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.52611,12.96999,14.50805,12.8775), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.07499,13.08159,14.44305,13.08472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.44305,13.08472,21.97249,13.0875), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.97249,13.0875,14.44305,13.08472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.07999,13.15333,21.97249,13.0875), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.22888,13.27639,13.90318,13.32478), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.90318,13.32478,22.22888,13.27639), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.29527,13.37778,13.90318,13.32478), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.76189,13.52478,22.21721,13.58472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.21721,13.58472,22.1536,13.63444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.1536,13.63444,22.21721,13.58472), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.62524,13.71821,22.1386,13.72805), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.1386,13.72805,13.62524,13.71821), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.08444,13.77916,22.1386,13.72805), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.23388,13.96583,13.56238,13.99192), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.56238,13.99192,22.23388,13.96583), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.47694,14.10083,22.55249,14.12083), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.55249,14.12083,22.47694,14.10083), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.56694,14.16416,22.55249,14.12083), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.55527,14.23194,22.45666,14.25222), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.45666,14.25222,22.55527,14.23194), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.44027,14.28222,22.45666,14.25222), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.46222,14.42805,13.47555,14.46833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.47555,14.46833,22.44194,14.48944), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.44194,14.48944,13.47555,14.46833), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.38138,14.52305,13.66528,14.54194), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.66528,14.54194,22.38416,14.55416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.38416,14.55416,13.66528,14.54194), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.68416,14.5725,22.38416,14.55416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.4161,14.60027,13.68416,14.5725), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.66083,14.64583,22.4161,14.60027), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.70249,14.69139,13.79416,14.73277), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.79416,14.73277,22.70249,14.69139), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.76722,14.84805,22.66888,14.85416), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.66888,14.85416,13.76722,14.84805), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.75333,14.97694,13.85916,15.03778), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((13.85916,15.03778,22.86832,15.09444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.86832,15.09444,22.93583,15.11611), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.93583,15.11611,22.86832,15.09444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.98471,15.23111,22.93583,15.11611), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.99527,15.38583,22.91944,15.50972), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.91944,15.50972,22.93777,15.5625), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((22.93777,15.5625,22.91944,15.50972), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.38138,15.68527,23.99971,15.69944), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.99971,15.69944,23.11832,15.71055), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.11832,15.71055,23.99971,15.69944), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.47916,15.72611,14.36889,15.73388), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((14.36889,15.73388,23.47916,15.72611), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.68527,15.75611,14.36889,15.73388), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.48861,16.90027,15.52404,17.33394), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.52404,17.33394,15.48861,16.90027), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.59249,18.60444,23.99944,18.96638), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.99944,18.96638,15.59249,18.60444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((23.99956,19.49776,15.75389,19.93249), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.75389,19.93249,15.99666,20.35305), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.99666,20.35305,15.75389,19.93249), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.56861,20.77972,21.39471,20.85221), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((21.39471,20.85221,15.55833,20.88444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.55833,20.88444,21.39471,20.85221), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.58444,20.92999,15.62722,20.95527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.62722,20.95527,15.58444,20.92999), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.28444,21.44527,15.2025,21.49582), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.2025,21.49582,15.28444,21.44527), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.19472,21.99888,18.36361,22.34444), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((18.36361,22.34444,15.19472,21.99888), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.00063,23.00037,16.00083,23.45055), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((15.00063,23.00037,16.00083,23.45055), mapfile, tile_dir, 0, 11, "td-chad")
	render_tiles((16.00083,23.45055,15.00063,23.00037), mapfile, tile_dir, 0, 11, "td-chad")