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
    # Region: MA
    # Region Name: Morocco

	render_tiles((-13.17693,27.66508,-5.4525,27.77499), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-13.17693,27.66508,-5.4525,27.77499), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-13.17693,27.66508,-5.4525,27.77499), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-13.17693,27.66508,-5.4525,27.77499), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.93389,27.66666,-5.4525,30.62777), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.93389,27.66666,-5.4525,30.62777), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.93389,27.66666,-5.4525,30.62777), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.93389,27.66666,-5.4525,30.62777), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.66929,27.66885,-13.17693,33.26999), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.66929,27.66885,-13.17693,33.26999), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.66929,27.66885,-13.17693,33.26999), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.66929,27.66885,-13.17693,33.26999), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-13.02361,27.77499,-5.4525,27.9286), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-13.02361,27.77499,-5.4525,27.9286), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-13.02361,27.77499,-5.4525,27.9286), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-13.02361,27.77499,-5.4525,27.9286), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-12.95833,27.9286,-5.4525,27.77499), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-12.95833,27.9286,-5.4525,27.77499), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-12.95833,27.9286,-5.4525,27.77499), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-12.95833,27.9286,-5.4525,27.77499), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-12.81667,27.97166,-5.4525,27.9286), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-12.81667,27.97166,-5.4525,27.9286), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-12.81667,27.97166,-5.4525,27.9286), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-12.81667,27.97166,-5.4525,27.9286), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-12.18667,28.06388,-5.4525,28.11527), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-12.18667,28.06388,-5.4525,28.11527), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-12.18667,28.06388,-5.4525,28.11527), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-12.18667,28.06388,-5.4525,28.11527), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-12.025,28.11527,-5.4525,28.06388), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-12.025,28.11527,-5.4525,28.06388), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-12.025,28.11527,-5.4525,28.06388), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-12.025,28.11527,-5.4525,28.06388), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-11.50111,28.31138,-5.4525,28.40194), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-11.50111,28.31138,-5.4525,28.40194), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-11.50111,28.31138,-5.4525,28.40194), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-11.50111,28.31138,-5.4525,28.40194), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-11.40944,28.40194,-5.4525,28.31138), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-11.40944,28.40194,-5.4525,28.31138), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-11.40944,28.40194,-5.4525,28.31138), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-11.40944,28.40194,-5.4525,28.31138), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.66929,28.70929,-13.17693,33.26999), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.66929,28.70929,-13.17693,33.26999), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.66929,28.70929,-13.17693,33.26999), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.66929,28.70929,-13.17693,33.26999), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-11.04417,28.76055,-5.4525,28.40194), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-11.04417,28.76055,-5.4525,28.40194), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-11.04417,28.76055,-5.4525,28.40194), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-11.04417,28.76055,-5.4525,28.40194), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.49555,28.79055,-13.17693,33.25332), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.49555,28.79055,-13.17693,33.25332), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.49555,28.79055,-13.17693,33.25332), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.49555,28.79055,-13.17693,33.25332), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.35778,28.91916,-13.17693,33.28471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.35778,28.91916,-13.17693,33.28471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.35778,28.91916,-13.17693,33.28471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.35778,28.91916,-13.17693,33.28471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-10.5575,28.99833,-5.4525,29.25388), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-10.5575,28.99833,-5.4525,29.25388), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-10.5575,28.99833,-5.4525,29.25388), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-10.5575,28.99833,-5.4525,29.25388), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.04667,29.09361,-13.17693,33.44777), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.04667,29.09361,-13.17693,33.44777), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.04667,29.09361,-13.17693,33.44777), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.04667,29.09361,-13.17693,33.44777), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-10.31722,29.25388,-5.4525,29.46666), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-10.31722,29.25388,-5.4525,29.46666), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-10.31722,29.25388,-5.4525,29.46666), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-10.31722,29.25388,-5.4525,29.46666), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.62278,29.39194,-13.17693,33.63138), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.62278,29.39194,-13.17693,33.63138), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.62278,29.39194,-13.17693,33.63138), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.62278,29.39194,-13.17693,33.63138), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.43417,29.39722,-13.17693,33.6961), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.43417,29.39722,-13.17693,33.6961), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.43417,29.39722,-13.17693,33.6961), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.43417,29.39722,-13.17693,33.6961), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-10.11722,29.46666,-5.4525,27.66666), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-10.11722,29.46666,-5.4525,27.66666), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-10.11722,29.46666,-5.4525,27.66666), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-10.11722,29.46666,-5.4525,27.66666), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.57556,29.57111,-5.4525,29.6336), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.57556,29.57111,-5.4525,29.6336), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.57556,29.57111,-5.4525,29.6336), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.57556,29.57111,-5.4525,29.6336), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.19972,29.59472,-13.17693,33.83054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.19972,29.59472,-13.17693,33.83054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.19972,29.59472,-13.17693,33.83054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.19972,29.59472,-13.17693,33.83054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.51111,29.6336,-5.4525,29.83305), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.51111,29.6336,-5.4525,29.83305), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.51111,29.6336,-5.4525,29.83305), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.51111,29.6336,-5.4525,29.83305), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.40028,29.80444,-13.17693,34.72137), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.40028,29.80444,-13.17693,34.72137), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.40028,29.80444,-13.17693,34.72137), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.40028,29.80444,-13.17693,34.72137), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.19139,29.80972,-13.17693,35.23693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.19139,29.80972,-13.17693,35.23693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.19139,29.80972,-13.17693,35.23693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.19139,29.80972,-13.17693,35.23693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.47444,29.83305,-5.4525,29.6336), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.47444,29.83305,-5.4525,29.6336), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.47444,29.83305,-5.4525,29.6336), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.47444,29.83305,-5.4525,29.6336), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.79139,29.87,-5.4525,30.6125), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.79139,29.87,-5.4525,30.6125), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.79139,29.87,-5.4525,30.6125), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.79139,29.87,-5.4525,30.6125), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.59389,29.89694,-13.17693,35.83027), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.59389,29.89694,-13.17693,35.83027), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.59389,29.89694,-13.17693,35.83027), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.59389,29.89694,-13.17693,35.83027), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.39611,29.97416,-13.17693,35.91241), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.39611,29.97416,-13.17693,35.91241), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.39611,29.97416,-13.17693,35.91241), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.39611,29.97416,-13.17693,35.91241), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.64028,30.16805,-5.4525,30.41277), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.64028,30.16805,-5.4525,30.41277), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.64028,30.16805,-5.4525,30.41277), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.64028,30.16805,-5.4525,30.41277), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.145,30.18666,-13.17693,35.57471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.145,30.18666,-13.17693,35.57471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.145,30.18666,-13.17693,35.57471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.145,30.18666,-13.17693,35.57471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.61195,30.41277,-5.4525,30.16805), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.61195,30.41277,-5.4525,30.16805), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.61195,30.41277,-5.4525,30.16805), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.61195,30.41277,-5.4525,30.16805), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.9425,30.49221,-13.17693,35.25804), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.9425,30.49221,-13.17693,35.25804), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.9425,30.49221,-13.17693,35.25804), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.9425,30.49221,-13.17693,35.25804), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.79361,30.6125,-5.4525,29.87), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.79361,30.6125,-5.4525,29.87), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.79361,30.6125,-5.4525,29.87), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.79361,30.6125,-5.4525,29.87), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.59195,30.62638,-5.4525,30.63721), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.59195,30.62638,-5.4525,30.63721), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.59195,30.62638,-5.4525,30.63721), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.59195,30.62638,-5.4525,30.63721), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.88611,30.62777,-5.4525,31.40027), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.88611,30.62777,-5.4525,31.40027), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.88611,30.62777,-5.4525,31.40027), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.88611,30.62777,-5.4525,31.40027), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.43028,30.63721,-13.17693,35.15054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.43028,30.63721,-13.17693,35.15054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.43028,30.63721,-13.17693,35.15054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.43028,30.63721,-13.17693,35.15054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.88611,30.67749,-5.4525,31.40027), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.88611,30.67749,-5.4525,31.40027), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.88611,30.67749,-5.4525,31.40027), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.88611,30.67749,-5.4525,31.40027), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.83778,30.75555,-5.4525,31.40027), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.83778,30.75555,-5.4525,31.40027), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.83778,30.75555,-5.4525,31.40027), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.83778,30.75555,-5.4525,31.40027), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.17389,30.76611,-5.4525,30.90666), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.17389,30.76611,-5.4525,30.90666), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.17389,30.76611,-5.4525,30.90666), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.17389,30.76611,-5.4525,30.90666), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.82861,30.84388,-5.4525,30.75555), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.82861,30.84388,-5.4525,30.75555), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.82861,30.84388,-5.4525,30.75555), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.82861,30.84388,-5.4525,30.75555), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.02722,30.90666,-13.17693,35.26054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.02722,30.90666,-13.17693,35.26054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.02722,30.90666,-13.17693,35.26054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.02722,30.90666,-13.17693,35.26054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.64667,30.9611,-13.17693,35.2861), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.64667,30.9611,-13.17693,35.2861), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.64667,30.9611,-13.17693,35.2861), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.64667,30.9611,-13.17693,35.2861), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.60222,30.99138,-5.4525,31.08611), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.60222,30.99138,-5.4525,31.08611), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.60222,30.99138,-5.4525,31.08611), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.60222,30.99138,-5.4525,31.08611), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.59805,31.08611,-5.4525,30.99138), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.59805,31.08611,-5.4525,30.99138), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.59805,31.08611,-5.4525,30.99138), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.59805,31.08611,-5.4525,30.99138), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.81556,31.15249,-5.4525,31.69555), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.81556,31.15249,-5.4525,31.69555), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.81556,31.15249,-5.4525,31.69555), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.81556,31.15249,-5.4525,31.69555), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.82556,31.18277,-13.17693,35.19916), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.82556,31.18277,-13.17693,35.19916), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.82556,31.18277,-13.17693,35.19916), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.82556,31.18277,-13.17693,35.19916), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.72444,31.1886,-5.4525,31.39471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.72444,31.1886,-5.4525,31.39471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.72444,31.1886,-5.4525,31.39471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.72444,31.1886,-5.4525,31.39471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.79639,31.22166,-5.4525,31.33916), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.79639,31.22166,-5.4525,31.33916), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.79639,31.22166,-5.4525,31.33916), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.79639,31.22166,-5.4525,31.33916), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.80417,31.33916,-5.4525,31.22166), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.80417,31.33916,-5.4525,31.22166), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.80417,31.33916,-5.4525,31.22166), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.80417,31.33916,-5.4525,31.22166), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.80445,31.34277,-5.4525,30.6125), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.80445,31.34277,-5.4525,30.6125), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.80445,31.34277,-5.4525,30.6125), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.80445,31.34277,-5.4525,30.6125), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.72472,31.39471,-5.4525,31.1886), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.72472,31.39471,-5.4525,31.1886), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.72472,31.39471,-5.4525,31.1886), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.72472,31.39471,-5.4525,31.1886), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.84528,31.40027,-5.4525,30.75555), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.84528,31.40027,-5.4525,30.75555), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.84528,31.40027,-5.4525,30.75555), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.84528,31.40027,-5.4525,30.75555), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.83556,31.46693,-5.4525,31.65027), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.83556,31.46693,-5.4525,31.65027), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.83556,31.46693,-5.4525,31.65027), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.83556,31.46693,-5.4525,31.65027), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.73278,31.56499,-5.4525,31.70861), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.73278,31.56499,-5.4525,31.70861), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.73278,31.56499,-5.4525,31.70861), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.73278,31.56499,-5.4525,31.70861), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.83528,31.65027,-5.4525,31.46693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.83528,31.65027,-5.4525,31.46693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.83528,31.65027,-5.4525,31.46693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.83528,31.65027,-5.4525,31.46693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.51889,31.67277,-13.17693,35.22665), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.51889,31.67277,-13.17693,35.22665), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.51889,31.67277,-13.17693,35.22665), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.51889,31.67277,-13.17693,35.22665), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.81861,31.69555,-5.4525,31.15249), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.81861,31.69555,-5.4525,31.15249), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.81861,31.69555,-5.4525,31.15249), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.81861,31.69555,-5.4525,31.15249), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.67945,31.70861,-5.4525,30.16805), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.67945,31.70861,-5.4525,30.16805), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.67945,31.70861,-5.4525,30.16805), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.67945,31.70861,-5.4525,30.16805), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.71472,31.71638,-5.4525,31.1886), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.71472,31.71638,-5.4525,31.1886), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.71472,31.71638,-5.4525,31.1886), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.71472,31.71638,-5.4525,31.1886), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.9725,31.85083,-13.17693,35.43638), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.9725,31.85083,-13.17693,35.43638), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.9725,31.85083,-13.17693,35.43638), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.9725,31.85083,-13.17693,35.43638), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.93833,32.02887,-13.17693,35.33654), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.93833,32.02887,-13.17693,35.33654), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.93833,32.02887,-13.17693,35.33654), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.93833,32.02887,-13.17693,35.33654), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.35195,32.04388,-13.17693,32.56221), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.35195,32.04388,-13.17693,32.56221), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.35195,32.04388,-13.17693,32.56221), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.35195,32.04388,-13.17693,32.56221), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.86528,32.08471,-13.17693,35.12693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.86528,32.08471,-13.17693,35.12693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.86528,32.08471,-13.17693,35.12693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.86528,32.08471,-13.17693,35.12693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.21306,32.08971,-13.17693,32.16693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.21306,32.08971,-13.17693,32.16693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.21306,32.08971,-13.17693,32.16693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.21306,32.08971,-13.17693,32.16693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.5,32.10804,-13.17693,32.97915), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.5,32.10804,-13.17693,32.97915), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.5,32.10804,-13.17693,32.97915), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.5,32.10804,-13.17693,32.97915), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.17778,32.11499,-13.17693,32.4047), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.17778,32.11499,-13.17693,32.4047), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.17778,32.11499,-13.17693,32.4047), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.17778,32.11499,-13.17693,32.4047), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.22028,32.14999,-13.17693,35.09972), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.22028,32.14999,-13.17693,35.09972), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.22028,32.14999,-13.17693,35.09972), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.22028,32.14999,-13.17693,35.09972), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.29278,32.15915,-13.17693,32.21471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.29278,32.15915,-13.17693,32.21471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.29278,32.15915,-13.17693,32.21471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.29278,32.15915,-13.17693,32.21471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.19861,32.16693,-13.17693,32.4047), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.19861,32.16693,-13.17693,32.4047), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.19861,32.16693,-13.17693,32.4047), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.19861,32.16693,-13.17693,32.4047), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.25389,32.21471,-13.17693,32.32693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.25389,32.21471,-13.17693,32.32693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.25389,32.21471,-13.17693,32.32693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.25389,32.21471,-13.17693,32.32693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.265,32.22749,-13.17693,32.56221), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.265,32.22749,-13.17693,32.56221), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.265,32.22749,-13.17693,32.56221), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.265,32.22749,-13.17693,32.56221), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.25,32.32693,-13.17693,32.21471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.25,32.32693,-13.17693,32.21471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.25,32.32693,-13.17693,32.21471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.25,32.32693,-13.17693,32.21471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.19639,32.4047,-13.17693,32.16693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.19639,32.4047,-13.17693,32.16693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.19639,32.4047,-13.17693,32.16693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.19639,32.4047,-13.17693,32.16693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.07806,32.44137,-13.17693,32.50832), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.07806,32.44137,-13.17693,32.50832), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.07806,32.44137,-13.17693,32.50832), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.07806,32.44137,-13.17693,32.50832), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.01056,32.50832,-13.17693,32.44137), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.01056,32.50832,-13.17693,32.44137), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.01056,32.50832,-13.17693,32.44137), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.01056,32.50832,-13.17693,32.44137), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-9.27306,32.56221,-13.17693,32.22749), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-9.27306,32.56221,-13.17693,32.22749), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-9.27306,32.56221,-13.17693,32.22749), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-9.27306,32.56221,-13.17693,32.22749), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.38305,32.72443,-13.17693,32.15915), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.38305,32.72443,-13.17693,32.15915), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.38305,32.72443,-13.17693,32.15915), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.38305,32.72443,-13.17693,32.15915), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.86195,32.89054,-5.4525,27.66885), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.86195,32.89054,-5.4525,27.66885), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.86195,32.89054,-5.4525,27.66885), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.86195,32.89054,-5.4525,27.66885), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.54278,32.93942,-13.17693,32.10804), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.54278,32.93942,-13.17693,32.10804), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.54278,32.93942,-13.17693,32.10804), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.54278,32.93942,-13.17693,32.10804), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.48722,32.97915,-13.17693,33.05804), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.48722,32.97915,-13.17693,33.05804), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.48722,32.97915,-13.17693,33.05804), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.48722,32.97915,-13.17693,33.05804), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.47889,33.05804,-13.17693,32.97915), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.47889,33.05804,-13.17693,32.97915), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.47889,33.05804,-13.17693,32.97915), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.47889,33.05804,-13.17693,32.97915), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.47528,33.25332,-5.4525,28.79055), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.47528,33.25332,-5.4525,28.79055), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.47528,33.25332,-5.4525,28.79055), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.47528,33.25332,-5.4525,28.79055), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.66556,33.25665,-13.17693,33.38332), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.66556,33.25665,-13.17693,33.38332), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.66556,33.25665,-13.17693,33.38332), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.66556,33.25665,-13.17693,33.38332), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.51945,33.26999,-5.4525,28.79055), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.51945,33.26999,-5.4525,28.79055), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.51945,33.26999,-5.4525,28.79055), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.51945,33.26999,-5.4525,28.79055), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.39611,33.28471,-5.4525,28.91916), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.39611,33.28471,-5.4525,28.91916), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.39611,33.28471,-5.4525,28.91916), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.39611,33.28471,-5.4525,28.91916), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.30528,33.37554,-5.4525,28.91916), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.30528,33.37554,-5.4525,28.91916), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.30528,33.37554,-5.4525,28.91916), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.30528,33.37554,-5.4525,28.91916), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.66667,33.38332,-13.17693,33.25665), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.66667,33.38332,-13.17693,33.25665), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.66667,33.38332,-13.17693,33.25665), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.66667,33.38332,-13.17693,33.25665), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-8.055,33.44777,-5.4525,29.09361), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-8.055,33.44777,-5.4525,29.09361), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-8.055,33.44777,-5.4525,29.09361), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-8.055,33.44777,-5.4525,29.09361), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.60028,33.55693,-13.17693,33.65082), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.60028,33.55693,-13.17693,33.65082), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.60028,33.55693,-13.17693,33.65082), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.60028,33.55693,-13.17693,33.65082), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.52528,33.63138,-13.17693,33.6961), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.52528,33.63138,-13.17693,33.6961), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.52528,33.63138,-13.17693,33.6961), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.52528,33.63138,-13.17693,33.6961), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.64194,33.65082,-13.17693,34.08498), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.64194,33.65082,-13.17693,34.08498), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.64194,33.65082,-13.17693,34.08498), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.64194,33.65082,-13.17693,34.08498), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.72667,33.69553,-13.17693,34.74721), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.72667,33.69553,-13.17693,34.74721), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.72667,33.69553,-13.17693,34.74721), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.72667,33.69553,-13.17693,34.74721), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.43972,33.6961,-5.4525,29.39722), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.43972,33.6961,-5.4525,29.39722), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.43972,33.6961,-5.4525,29.39722), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.43972,33.6961,-5.4525,29.39722), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-7.11833,33.83054,-5.4525,29.59472), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-7.11833,33.83054,-5.4525,29.59472), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-7.11833,33.83054,-5.4525,29.59472), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-7.11833,33.83054,-5.4525,29.59472), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.92611,33.94943,-13.17693,33.83054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.92611,33.94943,-13.17693,33.83054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.92611,33.94943,-13.17693,33.83054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.92611,33.94943,-13.17693,33.83054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.65361,34.08498,-13.17693,33.65082), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.65361,34.08498,-13.17693,33.65082), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.65361,34.08498,-13.17693,33.65082), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.65361,34.08498,-13.17693,33.65082), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.67056,34.26527,-5.4525,29.57111), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.67056,34.26527,-5.4525,29.57111), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.67056,34.26527,-5.4525,29.57111), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.67056,34.26527,-5.4525,29.57111), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.79333,34.37832,-13.17693,34.74721), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.79333,34.37832,-13.17693,34.74721), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.79333,34.37832,-13.17693,34.74721), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.79333,34.37832,-13.17693,34.74721), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.68639,34.48526,-13.17693,33.38332), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.68639,34.48526,-13.17693,33.38332), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.68639,34.48526,-13.17693,33.38332), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.68639,34.48526,-13.17693,33.38332), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.85333,34.60054,-13.17693,34.37832), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.85333,34.60054,-13.17693,34.37832), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.85333,34.60054,-13.17693,34.37832), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.85333,34.60054,-13.17693,34.37832), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.37945,34.72137,-5.4525,29.80444), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.37945,34.72137,-5.4525,29.80444), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.37945,34.72137,-5.4525,29.80444), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.37945,34.72137,-5.4525,29.80444), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-1.74722,34.74721,-13.17693,33.69553), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-1.74722,34.74721,-13.17693,33.69553), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-1.74722,34.74721,-13.17693,33.69553), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-1.74722,34.74721,-13.17693,33.69553), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.20528,35.05138,-13.17693,35.09972), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.20528,35.05138,-13.17693,35.09972), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.20528,35.05138,-13.17693,35.09972), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.20528,35.05138,-13.17693,35.09972), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.62806,35.09832,-13.17693,35.09832), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.62806,35.09832,-13.17693,35.09832), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.62806,35.09832,-13.17693,35.09832), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.62806,35.09832,-13.17693,35.09832), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.51722,35.09832,-13.17693,35.15332), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.51722,35.09832,-13.17693,35.15332), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.51722,35.09832,-13.17693,35.15332), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.51722,35.09832,-13.17693,35.15332), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.21152,35.09972,-13.17693,35.05138), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.74722,35.11555,-13.17693,35.20165), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.74722,35.11555,-13.17693,35.20165), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.74722,35.11555,-13.17693,35.20165), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.74722,35.11555,-13.17693,35.20165), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.84167,35.12693,-13.17693,35.20165), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.84167,35.12693,-13.17693,35.20165), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.84167,35.12693,-13.17693,35.20165), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.84167,35.12693,-13.17693,35.20165), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.40944,35.15054,-5.4525,30.63721), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.40944,35.15054,-5.4525,30.63721), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.40944,35.15054,-5.4525,30.63721), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.40944,35.15054,-5.4525,30.63721), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.41945,35.15332,-13.17693,35.09832), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.41945,35.15332,-13.17693,35.09832), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.41945,35.15332,-13.17693,35.09832), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.41945,35.15332,-13.17693,35.09832), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.90195,35.16305,-13.17693,35.2336), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.90195,35.16305,-13.17693,35.2336), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.90195,35.16305,-13.17693,35.2336), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.90195,35.16305,-13.17693,35.2336), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.32667,35.19276,-5.4525,31.67277), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.32667,35.19276,-5.4525,31.67277), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.32667,35.19276,-5.4525,31.67277), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.32667,35.19276,-5.4525,31.67277), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.8275,35.19916,-5.4525,31.18277), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.8275,35.19916,-5.4525,31.18277), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.8275,35.19916,-5.4525,31.18277), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.8275,35.19916,-5.4525,31.18277), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.83389,35.20165,-13.17693,35.12693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.83389,35.20165,-13.17693,35.12693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.83389,35.20165,-13.17693,35.12693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.83389,35.20165,-13.17693,35.12693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.76389,35.21832,-13.17693,35.27249), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.76389,35.21832,-13.17693,35.27249), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.76389,35.21832,-13.17693,35.27249), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.76389,35.21832,-13.17693,35.27249), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.90306,35.22166,-13.17693,35.26054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.90306,35.22166,-13.17693,35.26054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.90306,35.22166,-13.17693,35.26054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.90306,35.22166,-13.17693,35.26054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.5775,35.22665,-5.4525,31.08611), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.5775,35.22665,-5.4525,31.08611), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.5775,35.22665,-5.4525,31.08611), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.5775,35.22665,-5.4525,31.08611), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.89028,35.2336,-13.17693,35.16305), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.89028,35.2336,-13.17693,35.16305), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.89028,35.2336,-13.17693,35.16305), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.89028,35.2336,-13.17693,35.16305), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-6.14,35.23693,-5.4525,29.80972), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-6.14,35.23693,-5.4525,29.80972), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-6.14,35.23693,-5.4525,29.80972), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-6.14,35.23693,-5.4525,29.80972), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-4.80944,35.25804,-5.4525,30.49221), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-4.80944,35.25804,-5.4525,30.49221), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-4.80944,35.25804,-5.4525,30.49221), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-4.80944,35.25804,-5.4525,30.49221), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.91833,35.26054,-13.17693,35.22166), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.91833,35.26054,-13.17693,35.22166), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.91833,35.26054,-13.17693,35.22166), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.91833,35.26054,-13.17693,35.22166), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.92268,35.27007,-13.17693,35.33654), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.92268,35.27007,-13.17693,35.33654), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.92268,35.27007,-13.17693,35.33654), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.92268,35.27007,-13.17693,35.33654), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.74,35.27249,-5.4525,31.39471), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.74,35.27249,-5.4525,31.39471), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.74,35.27249,-5.4525,31.39471), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.74,35.27249,-5.4525,31.39471), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.6675,35.2861,-5.4525,30.9611), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.6675,35.2861,-5.4525,30.9611), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.6675,35.2861,-5.4525,30.9611), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.6675,35.2861,-5.4525,30.9611), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-3.05944,35.29638,-13.17693,35.43638), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-3.05944,35.29638,-13.17693,35.43638), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-3.05944,35.29638,-13.17693,35.43638), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-3.05944,35.29638,-13.17693,35.43638), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.93328,35.33654,-13.17693,32.02887), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.93328,35.33654,-13.17693,32.02887), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.93328,35.33654,-13.17693,32.02887), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.93328,35.33654,-13.17693,32.02887), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.9525,35.43499,-13.17693,32.02887), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.9525,35.43499,-13.17693,32.02887), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.9525,35.43499,-13.17693,32.02887), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.9525,35.43499,-13.17693,32.02887), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-2.98417,35.43638,-13.17693,31.85083), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-2.98417,35.43638,-13.17693,31.85083), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-2.98417,35.43638,-13.17693,31.85083), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-2.98417,35.43638,-13.17693,31.85083), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.25,35.57471,-13.17693,35.79054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.25,35.57471,-13.17693,35.79054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.25,35.57471,-13.17693,35.79054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.25,35.57471,-13.17693,35.79054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.92677,35.78024,-13.17693,35.23693), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.92677,35.78024,-13.17693,35.23693), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.92677,35.78024,-13.17693,35.23693), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.92677,35.78024,-13.17693,35.23693), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.33917,35.79054,-13.17693,35.83552), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.33917,35.79054,-13.17693,35.83552), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.33917,35.79054,-13.17693,35.83552), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.33917,35.79054,-13.17693,35.83552), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.57361,35.83027,-5.4525,29.89694), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.57361,35.83027,-5.4525,29.89694), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.57361,35.83027,-5.4525,29.89694), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.57361,35.83027,-5.4525,29.89694), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.3422,35.83552,-13.17693,35.79054), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.3422,35.83552,-13.17693,35.79054), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.3422,35.83552,-13.17693,35.79054), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.3422,35.83552,-13.17693,35.79054), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.39346,35.91241,-5.4525,29.97416), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.39346,35.91241,-5.4525,29.97416), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.39346,35.91241,-5.4525,29.97416), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.39346,35.91241,-5.4525,29.97416), mapfile, tile_dir, 17, 17, "ma-morocco")
	render_tiles((-5.4525,35.91527,-5.4525,29.97416), mapfile, tile_dir, 0, 11, "ma-morocco")
	render_tiles((-5.4525,35.91527,-5.4525,29.97416), mapfile, tile_dir, 13, 13, "ma-morocco")
	render_tiles((-5.4525,35.91527,-5.4525,29.97416), mapfile, tile_dir, 15, 15, "ma-morocco")
	render_tiles((-5.4525,35.91527,-5.4525,29.97416), mapfile, tile_dir, 17, 17, "ma-morocco")