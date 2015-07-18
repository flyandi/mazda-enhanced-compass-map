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
    # Region: GH
    # Region Name: Ghana

	render_tiles((-2.08972,4.72778,-1.96472,4.75167), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-1.96472,4.75167,-2.08972,4.72778), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-1.87,4.83361,-2.28722,4.895), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.28722,4.895,-1.87,4.83361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.56528,4.96139,-1.63639,4.96333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-1.63639,4.96333,-2.56528,4.96139), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-1.62611,5.01278,-1.63639,4.96333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.96639,5.07861,-3.10555,5.08591), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.10555,5.08591,-2.96639,5.07861), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.92858,5.09918,-3.08972,5.11055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.08972,5.11055,-3.10231,5.11271), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.10231,5.11271,-2.73472,5.11278), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.73472,5.11278,-3.10231,5.11271), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-1.12944,5.16305,-0.79972,5.20667), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.79972,5.20667,-0.90861,5.21194), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.90861,5.21194,-0.79972,5.20667), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.71814,5.34631,-0.58778,5.34972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.58778,5.34972,-2.77333,5.35), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.77333,5.35,-0.58778,5.34972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.49333,5.36778,-2.77333,5.35), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.39722,5.47389,-0.49333,5.36778), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.76403,5.59034,-2.77667,5.61333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.77667,5.61333,-2.90639,5.61639), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.90639,5.61639,-2.77667,5.61333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.96056,5.62722,-2.90639,5.61639), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.06444,5.66333,-2.96056,5.62722), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.01389,5.7075,-2.94861,5.71305), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.94861,5.71305,-3.01389,5.7075), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.68417,5.75361,0.92,5.77194), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.92,5.77194,0.30972,5.77639), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.30972,5.77639,0.65111,5.77833), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.65111,5.77833,0.30972,5.77639), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.98972,5.81805,0.65111,5.77833), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.00667,5.85778,0.98972,5.81805), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.62111,5.93555,0.63444,5.94805), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.63444,5.94805,0.62111,5.93555), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((1.06167,6.00222,0.37222,6.02222), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.37222,6.02222,0.50639,6.03361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.50639,6.03361,0.37222,6.02222), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.37833,6.04944,0.50083,6.06194), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.50083,6.06194,0.43306,6.07083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.43306,6.07083,0.50083,6.06194), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.40667,6.08306,0.20806,6.08944), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.20806,6.08944,0.40667,6.08306), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((1.20385,6.0991,0.24417,6.105), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.24417,6.105,1.20385,6.0991), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((1.20111,6.16,1.1,6.16055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((1.1,6.16055,1.20111,6.16), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.16945,6.27722,1.00028,6.32778), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((1.00028,6.32778,0.90472,6.32944), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.90472,6.32944,1.00028,6.32778), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.73361,6.49083,0.68371,6.58733), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.68371,6.58733,-3.24917,6.61139), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.24917,6.61139,0.68371,6.58733), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.18639,6.71639,0.57111,6.81333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.57111,6.81333,-3.22417,6.81361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.22417,6.81361,0.57111,6.81333), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.52917,6.82583,-3.22417,6.81361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.56389,6.9175,0.51389,6.97083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.51389,6.97083,0.60806,7.01444), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.60806,7.01444,0.51389,6.97083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.09,7.06194,-3.02417,7.07305), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-3.02417,7.07305,-3.09,7.06194), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.66583,7.30361,0.57028,7.38528), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.57028,7.38528,0.64667,7.4), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.64667,7.4,0.57028,7.38528), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.51056,7.46055,0.64667,7.4), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.52111,7.58583,0.58194,7.62083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.58194,7.62083,0.52111,7.58583), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.58722,7.69416,0.58194,7.62083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.62694,7.76917,-2.82389,7.82361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.82389,7.82361,-2.78528,7.85361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.78528,7.85361,-2.82389,7.82361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.74111,7.93889,-2.77472,7.94666), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.77472,7.94666,-2.74111,7.93889), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.68417,8.0125,-2.58889,8.04083), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.58889,8.04083,-2.68417,8.0125), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.61139,8.13778,0.58889,8.1975), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.58889,8.1975,-2.48806,8.19777), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.48806,8.19777,0.58889,8.1975), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.72694,8.285,-2.48806,8.19777), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.67417,8.40555,0.63389,8.49166), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.63389,8.49166,0.67417,8.40555), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.4725,8.59277,0.63389,8.49166), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.38306,8.76222,-2.58465,8.78153), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.58465,8.78153,0.39,8.7875), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.39,8.7875,0.4825,8.79166), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.4825,8.79166,0.39,8.7875), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.51444,8.91194,-2.65583,9.01305), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.65583,9.01305,0.44806,9.02), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.44806,9.02,-2.65583,9.01305), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.77306,9.05527,0.44806,9.02), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.6625,9.26916,-2.71611,9.31166), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.71611,9.31166,-2.6625,9.26916), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.55194,9.36111,-2.66806,9.38277), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.66806,9.38277,0.55194,9.36111), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.54889,9.415,0.28778,9.42138), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.28778,9.42138,0.54889,9.415), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.22901,9.43228,0.28778,9.42138), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.22111,9.47277,0.275,9.48055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.275,9.48055,-2.68685,9.48234), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.68685,9.48234,0.275,9.48055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.46278,9.48833,0.36444,9.48972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.36444,9.48972,0.46278,9.48833), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.29722,9.50972,0.22306,9.52861), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.22306,9.52861,0.29722,9.50972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.34861,9.56444,0.23306,9.57222), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.23306,9.57222,0.34861,9.56444), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.37639,9.59055,0.28528,9.59111), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.28528,9.59111,0.37639,9.59055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.34917,9.61166,0.28528,9.59111), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.33583,9.64972,0.25306,9.65361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.25306,9.65361,0.33583,9.64972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.27528,9.68,0.34944,9.69222), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.34944,9.69222,0.27528,9.68), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.3225,9.72916,-2.79445,9.73583), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.79445,9.73583,0.3225,9.72916), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.73861,9.82611,-2.79445,9.73583), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.36806,10.0275,-2.79639,10.05777), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.79639,10.05777,0.39944,10.06111), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.39944,10.06111,-2.79639,10.05777), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.34972,10.11527,0.39944,10.06111), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.75472,10.265,0.38324,10.27363), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.38324,10.27363,-2.75472,10.265), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.39333,10.30833,-2.84361,10.32638), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.84361,10.32638,0.32083,10.33055), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.32083,10.33055,-2.84361,10.32638), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.19639,10.39778,0.28639,10.41389), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.28639,10.41389,-2.76861,10.41666), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.76861,10.41666,0.28639,10.41389), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.85778,10.45722,-2.76861,10.41666), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.04583,10.58611,-2.9325,10.63), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.9325,10.63,-0.06833,10.63389), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.06833,10.63389,-2.9325,10.63), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.07139,10.73777,-2.88028,10.80416), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.88028,10.80416,-0.07139,10.73777), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.61,10.91722,-0.665,10.95472), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.665,10.95472,0.0325,10.98972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.0325,10.98972,-0.56778,10.99222), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.56778,10.99222,0.0325,10.98972), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.67972,10.9975,-0.48889,10.99833), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.48889,10.99833,-0.67972,10.9975), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.23028,11.00027,-0.48889,10.99833), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.83106,11.00341,-2.23028,11.00027), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-2.83106,11.00341,-2.23028,11.00027), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.35278,11.06889,0.03444,11.075), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((0.03444,11.075,-0.35278,11.06889), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.42306,11.09694,-0.02111,11.10361), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.02111,11.10361,-0.38194,11.10861), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.38194,11.10861,-0.13389,11.11139), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.13389,11.11139,-0.38194,11.10861), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.27528,11.13,-0.15096,11.13927), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.15096,11.13927,-0.27528,11.13), mapfile, tile_dir, 0, 11, "gh-ghana")
	render_tiles((-0.28528,11.16666,-0.15096,11.13927), mapfile, tile_dir, 0, 11, "gh-ghana")