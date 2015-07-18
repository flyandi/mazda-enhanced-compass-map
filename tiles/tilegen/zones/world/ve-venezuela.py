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
    # Region: VE
    # Region Name: Venezuela

	render_tiles((-64.04945,10.85861,-64.2939,10.94333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.04945,10.85861,-64.2939,10.94333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.2939,10.94333,-64.17195,10.96), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.17195,10.96,-63.81807,10.96444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.81807,10.96444,-64.40611,10.9675), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.40611,10.9675,-63.81807,10.96444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.04529,10.98666,-64.10583,10.99527), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.10583,10.99527,-64.04529,10.98666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.38197,11.05361,-64.33084,11.06277), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.33084,11.06277,-64.38197,11.05361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.21223,11.08861,-64.33084,11.06277), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.84584,11.12944,-64.21223,11.08861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.89084,11.17305,-63.84584,11.12944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.51917,0.64972,-65.56361,0.67167), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.56361,0.67167,-65.51917,0.64972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.43361,0.69917,-65.58362,0.72583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.58362,0.72583,-66.12944,0.73278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.12944,0.73278,-65.58362,0.72583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.3175,0.75222,-66.12944,0.73278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.97112,0.80389,-65.39,0.83083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.39,0.83083,-65.50917,0.84389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.50917,0.84389,-65.39,0.83083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.51112,0.90472,-65.86223,0.91333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.86223,0.91333,-65.51112,0.90472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.19666,0.92417,-65.30167,0.92667), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.30167,0.92667,-65.19666,0.92417), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.17195,0.95472,-65.55945,0.97111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.55945,0.97111,-65.70361,0.98444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.70361,0.98444,-65.55945,0.97111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.14056,1.1125,-65.01723,1.13389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.01723,1.13389,-65.09862,1.14528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.09862,1.14528,-65.01723,1.13389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.87338,1.22554,-64.75111,1.24361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.75111,1.24361,-66.87338,1.22554), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.69749,1.26278,-64.75111,1.24361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.81555,1.28222,-64.69749,1.26278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.36694,1.36444,-64.34169,1.36694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.34169,1.36694,-64.36694,1.36444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.3864,1.40972,-64.52333,1.4375), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.52333,1.4375,-64.3864,1.40972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.25529,1.48056,-64.34999,1.48417), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.34999,1.48417,-64.25529,1.48056), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.37027,1.51139,-64.34999,1.48417), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.07028,1.65028,-66.99168,1.69583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.99168,1.69583,-64.07028,1.65028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.04388,1.90917,-63.97083,1.96722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.97083,1.96722,-63.76389,1.98028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.76389,1.98028,-67.1339,1.98778), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.1339,1.98778,-63.76389,1.98028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.65111,2.06472,-67.11194,2.09944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.11194,2.09944,-63.65111,2.06472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.1725,2.14806,-63.39306,2.15139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.39306,2.15139,-67.1725,2.14806), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.35778,2.26972,-67.21695,2.27528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.21695,2.27528,-63.35778,2.26972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.17418,2.33694,-67.19249,2.3925), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.19249,2.3925,-63.36584,2.42139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.36584,2.42139,-63.48695,2.42555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.48695,2.42555,-63.36584,2.42139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.28139,2.43889,-63.48695,2.42555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.03694,2.47055,-67.28139,2.43889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.0475,2.51333,-64.03694,2.47055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.99306,2.63305,-67.4939,2.66639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.4939,2.66639,-67.56778,2.68333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.56778,2.68333,-67.4939,2.66639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.84634,2.79245,-67.60722,2.79556), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.60722,2.79556,-67.84634,2.79245), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.01001,2.80222,-67.60722,2.79556), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.75696,2.83889,-67.84335,2.86833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.84335,2.86833,-67.75696,2.83889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.21861,3.12333,-67.44666,3.24194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.44666,3.24194,-67.38417,3.25917), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.38417,3.25917,-67.44666,3.24194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.29083,3.3975,-64.23582,3.43305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.23582,3.43305,-67.30388,3.45194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.30388,3.45194,-64.23582,3.43305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.37444,3.47889,-67.30388,3.45194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.18251,3.54611,-62.875,3.56028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.875,3.56028,-64.18251,3.54611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.98723,3.60083,-62.77528,3.60694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.77528,3.60694,-64.20361,3.61), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.20361,3.61,-62.77528,3.60694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.73556,3.67111,-67.48891,3.72278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.48891,3.72278,-62.72749,3.73111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.72749,3.73111,-67.59584,3.73778), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.59584,3.73778,-62.72749,3.73111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.4975,3.83389,-62.75861,3.83472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.75861,3.83472,-64.4975,3.83389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.50111,3.8575,-63.45667,3.86667), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.45667,3.86667,-63.50111,3.8575), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.98861,3.88305,-64.04333,3.89528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.04333,3.89528,-63.98861,3.88305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.60834,3.94361,-63.31472,3.94667), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.31472,3.94667,-63.60834,3.94361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.69057,3.95861,-63.8475,3.95972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.8475,3.95972,-67.69057,3.95861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.42333,3.96611,-63.3825,3.97083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.3825,3.97083,-63.42333,3.96611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.75389,3.98611,-63.3825,3.97083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.73111,4.03805,-62.54195,4.04167), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.54195,4.04167,-62.73111,4.03805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.68527,4.07055,-67.72917,4.0875), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.72917,4.0875,-64.11694,4.09861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.11694,4.09861,-62.11,4.10083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.11,4.10083,-64.11694,4.09861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.58223,4.12166,-62.24639,4.1225), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.24639,4.1225,-64.58223,4.12166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.53139,4.12639,-62.24639,4.1225), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.24889,4.14805,-61.8925,4.15083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.8925,4.15083,-62.05111,4.15111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.05111,4.15111,-61.8925,4.15083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.40028,4.18194,-64.8011,4.20194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.8011,4.20194,-62.40028,4.18194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.64159,4.22195,-67.80556,4.2275), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.80556,4.2275,-64.64159,4.22195), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.55333,4.24917,-61.74555,4.24944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.74555,4.24944,-61.55333,4.24917), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.69305,4.26722,-64.79805,4.27722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.79805,4.27722,-64.69305,4.26722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.76917,4.28917,-61.5125,4.29805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.5125,4.29805,-64.76917,4.28917), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.505,4.39528,-61.34222,4.4175), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.34222,4.4175,-61.45667,4.42111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.45667,4.42111,-61.34222,4.4175), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.28139,4.45722,-67.81305,4.46528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.81305,4.46528,-61.28139,4.45722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.2664,4.51972,-61.31139,4.52028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.31139,4.52028,-60.985,4.52055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.985,4.52055,-61.31139,4.52028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.87527,4.52528,-60.985,4.52055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.93083,4.58472,-67.87527,4.52528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.885,4.71167,-60.81611,4.72972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.81611,4.72972,-60.885,4.71167), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.64167,4.85555,-60.57973,4.98111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.57973,4.98111,-67.79306,5.05), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.79306,5.05,-67.82889,5.11222), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.82889,5.11222,-67.79306,5.05), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.66277,5.1875,-60.73293,5.20528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.73293,5.20528,-60.66277,5.1875), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.84973,5.30778,-60.73293,5.20528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.73167,5.43055,-67.61333,5.53917), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.61333,5.53917,-67.73167,5.43055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.65167,5.67,-67.62222,5.7875), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.62222,5.7875,-67.65167,5.67), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.39,5.94,-67.41444,5.98722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.41444,5.98722,-61.39,5.94), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.27914,6.06676,-69.25696,6.08389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.25696,6.08389,-69.19499,6.1), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.19499,6.1,-69.25696,6.08389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.42972,6.11861,-61.21916,6.12889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.49194,6.11861,-61.21916,6.12889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.21916,6.12889,-68.64417,6.13416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.64417,6.13416,-61.21916,6.12889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.33139,6.15305,-68.31361,6.16694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.31361,6.16694,-69.33139,6.15305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.11306,6.18722,-68.45418,6.19055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.45418,6.19055,-67.45479,6.19311), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.45479,6.19311,-68.45418,6.19055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.97112,6.19861,-69.09416,6.19916), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.09416,6.19916,-68.97112,6.19861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.00111,6.20667,-69.09416,6.19916), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.15222,6.22333,-68.00111,6.20667), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.57362,6.26611,-61.11389,6.28222), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.11389,6.28222,-67.57362,6.26611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.83556,6.30833,-61.15666,6.32889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.15666,6.32889,-67.83556,6.30833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.15361,6.49222,-61.20472,6.57444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.20472,6.57444,-61.15361,6.49222), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.13805,6.70194,-60.71306,6.75805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.71306,6.75805,-60.89611,6.75972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.89611,6.75972,-60.71306,6.75805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.90944,6.80916,-60.89611,6.75972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.36806,6.93028,-70.3075,6.93972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.3075,6.93972,-60.40833,6.9475), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.40833,6.9475,-70.3075,6.93972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.11916,6.97583,-71.88083,6.98527), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.88083,6.98527,-70.11916,6.97583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.13222,6.99528,-70.96306,7.00278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.96306,7.00278,-71.13222,6.99528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.51306,7.01528,-60.33139,7.02417), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.33139,7.02417,-70.51306,7.01528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.55305,7.04472,-72.06555,7.06167), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.06555,7.06167,-71.55305,7.04472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.82417,7.085,-70.56946,7.09028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.56946,7.09028,-70.82417,7.085), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.27834,7.1125,-60.53556,7.12389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.53556,7.12389,-60.27834,7.1125), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.35778,7.17555,-60.49528,7.18111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.49528,7.18111,-60.35778,7.17555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.61777,7.19444,-72.14555,7.19861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.14555,7.19861,-60.61777,7.19444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.63445,7.25805,-60.58805,7.31639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.58805,7.31639,-60.63445,7.25805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.18332,7.38277,-72.39917,7.40611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.39917,7.40611,-72.18332,7.38277), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.64584,7.43667,-60.69111,7.45583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.69111,7.45583,-60.64584,7.43667), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.47166,7.49194,-60.69111,7.45583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.71973,7.52889,-72.47166,7.49194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.69361,7.56611,-60.63277,7.60111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.63277,7.60111,-60.69361,7.56611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.53528,7.80222,-60.34094,7.84604), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.34094,7.84604,-60.53528,7.80222), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.17805,7.98139,-72.42027,7.99), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.42027,7.99,-60.17805,7.98139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.02333,8.04139,-72.4025,8.04194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.4025,8.04194,-60.02333,8.04139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.33362,8.04944,-72.4025,8.04194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.33556,8.14639,-59.94445,8.21027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-59.94445,8.21027,-59.83195,8.22888), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-59.83195,8.22888,-59.94445,8.21027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-59.80139,8.275,-59.83195,8.22888), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.38417,8.36305,-61.24084,8.42777), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.24084,8.42777,-61.03334,8.46028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.03334,8.46028,-61.07751,8.46083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.07751,8.46083,-61.03334,8.46028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.17529,8.49416,-61.02724,8.50194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.02724,8.50194,-61.17529,8.49416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.47472,8.52639,-59.98905,8.5336), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-59.98905,8.5336,-60.47472,8.52639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.02528,8.55389,-60.98278,8.56861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.98278,8.56861,-60.42663,8.57744), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.42663,8.57744,-60.98278,8.56861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.66028,8.58944,-61.52667,8.59027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.52667,8.59027,-61.66028,8.58944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.21834,8.59111,-61.52667,8.59027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.71917,8.60472,-61.21834,8.59111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.19306,8.62166,-60.40667,8.62639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.40667,8.62639,-61.6025,8.62694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.6025,8.62694,-60.40667,8.62639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.23639,8.6275,-61.6025,8.62694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.66444,8.64111,-60.23639,8.6275), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.19278,8.70194,-72.66444,8.64111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.10583,8.94361,-61.09528,9.04083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.09528,9.04083,-71.63,9.04416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.63,9.04416,-61.11,9.04555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.11,9.04555,-71.63,9.04416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.50557,9.04861,-61.11,9.04555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.92694,9.09833,-72.77223,9.11305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.77223,9.11305,-71.76363,9.11972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.76363,9.11972,-72.77223,9.11305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.04945,9.13833,-72.84528,9.14027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.84528,9.14027,-61.04945,9.13833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.97362,9.1425,-72.84528,9.14027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.2525,9.14583,-72.97362,9.1425), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.985,9.16639,-73.37193,9.16694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.37193,9.16694,-60.985,9.16639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.31841,9.17372,-60.94834,9.17444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.31841,9.17372,-60.94834,9.17444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.94834,9.17444,-73.31841,9.17372), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.3839,9.18972,-71.73334,9.20361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.73334,9.20361,-73.3839,9.18972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.7975,9.23583,-72.98111,9.26083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.98111,9.26083,-71.73695,9.27861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.73695,9.27861,-71.15834,9.28055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.15834,9.28055,-71.73695,9.27861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.0014,9.3025,-71.07362,9.31083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.07362,9.31083,-60.78306,9.31111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.78306,9.31111,-71.07362,9.31083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.05528,9.34833,-71.72557,9.34972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.72557,9.34972,-71.05528,9.34833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.77444,9.36444,-71.72557,9.34972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.79945,9.38139,-71.77444,9.36444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.81946,9.42111,-71.88806,9.42389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.88806,9.42389,-71.81946,9.42111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.84113,9.43861,-71.88918,9.44166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.88918,9.44166,-71.84113,9.43861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.99139,9.46,-71.88918,9.44166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.90695,9.48194,-60.96861,9.48472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.96861,9.48472,-60.90695,9.48194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.91585,9.49583,-71.96167,9.49722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.96167,9.49722,-71.91585,9.49583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.0175,9.51416,-60.96751,9.52944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-60.96751,9.52944,-71.96613,9.53111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.96613,9.53111,-71.09001,9.53194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.09001,9.53194,-71.96613,9.53111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.21278,9.57222,-72.01056,9.57389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.01056,9.57389,-61.21278,9.57222), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-73.09584,9.58166,-72.01056,9.57389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.98279,9.5975,-61.19889,9.60527), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.19889,9.60527,-71.98279,9.5975), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.21194,9.63444,-61.33723,9.63778), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.33723,9.63778,-62.21194,9.63444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.16445,9.68277,-61.74861,9.70389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.74861,9.70389,-62.31806,9.705), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.31806,9.705,-61.74861,9.70389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.03111,9.72222,-61.80945,9.73694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.80945,9.73694,-62.27723,9.7475), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.27723,9.7475,-61.7625,9.75333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.7625,9.75333,-62.27723,9.7475), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.59778,9.77972,-61.45028,9.79194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.45028,9.79194,-61.83917,9.80139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.83917,9.80139,-61.45028,9.79194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.81639,9.81639,-72.12779,9.81861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.12779,9.81861,-61.81639,9.81639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.58862,9.82194,-72.12779,9.81861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.81223,9.83055,-72.96251,9.83694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.96251,9.83694,-61.81223,9.83055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.15889,9.85222,-62.03667,9.86028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.03667,9.86028,-62.18028,9.865), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.18028,9.865,-62.03667,9.86028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.09056,9.88361,-62.03807,9.88639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.03807,9.88639,-71.09056,9.88361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.65028,9.89083,-62.03807,9.88639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.62251,9.90611,-61.65028,9.89083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.24861,9.92694,-62.19112,9.92861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.19112,9.92861,-62.24861,9.92694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.20667,9.94028,-62.19112,9.92861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.23251,9.96333,-72.02501,9.96972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.02501,9.96972,-62.25111,9.97111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.25111,9.97111,-72.02501,9.96972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.05667,9.97861,-62.25111,9.97111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.97888,9.98888,-62.80806,9.98889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.80806,9.98889,-72.97888,9.98888), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.43945,9.99611,-62.92612,9.99861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.92612,9.99861,-62.43945,9.99611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.21639,10.00944,-62.92612,9.99861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.78806,10.03555,-62.72472,10.04666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.72472,10.04666,-65.1239,10.05111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.1239,10.05111,-62.72472,10.04666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.66278,10.05916,-62.88473,10.06139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.88473,10.06139,-62.66278,10.05916), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.01306,10.065,-62.88473,10.06139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.67028,10.07389,-62.92667,10.07416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.92667,10.07416,-62.67028,10.07389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.82973,10.07972,-62.92667,10.07416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.96306,10.08861,-62.61445,10.09361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.61445,10.09361,-64.9425,10.0975), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.9425,10.0975,-62.61445,10.09361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.90417,10.10222,-64.9425,10.0975), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.73279,10.11305,-62.62389,10.11805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.62389,10.11805,-64.73279,10.11305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.46834,10.15166,-62.50333,10.17833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.50333,10.17833,-64.69278,10.19694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.69278,10.19694,-62.62195,10.21083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.62195,10.21083,-64.65056,10.21194), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.65056,10.21194,-62.62195,10.21083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.71918,10.21805,-65.78389,10.21972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.78389,10.21972,-65.71918,10.21805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.59972,10.22972,-65.78389,10.21972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-65.83806,10.24972,-71.37946,10.255), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.37946,10.255,-64.59334,10.25639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.59334,10.25639,-71.37946,10.255), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.00306,10.27583,-64.4075,10.27694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.4075,10.27694,-63.00306,10.27583), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.93639,10.27805,-64.4075,10.27694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.67028,10.285,-62.93639,10.27805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.37723,10.31027,-64.44501,10.32389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.44501,10.32389,-64.37723,10.31027), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.38306,10.33861,-62.95862,10.35305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.95862,10.35305,-64.38306,10.33861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.7525,10.37305,-64.39806,10.37416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.39806,10.37416,-71.7525,10.37305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.83723,10.38166,-64.36806,10.38416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.36806,10.38416,-62.83723,10.38166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.99167,10.38888,-64.36806,10.38416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.95473,10.395,-62.99167,10.38888), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.83056,10.40222,-62.79139,10.40333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.79139,10.40333,-62.93084,10.40417), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.93084,10.40417,-62.79139,10.40333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.24084,10.41166,-62.91723,10.41805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.91723,10.41805,-64.24084,10.41166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.81556,10.44139,-71.62195,10.45028), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.62195,10.45028,-63.81556,10.44139), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.1925,10.47528,-67.82224,10.47972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.82224,10.47972,-64.1925,10.47528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.8364,10.4925,-63.65528,10.49361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.65528,10.49361,-62.8364,10.4925), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.11975,10.49694,-63.65528,10.49361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.17029,10.50083,-66.11975,10.49694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.23306,10.51305,-72.84723,10.52305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.84723,10.52305,-62.88195,10.52389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.88195,10.52389,-72.84723,10.52305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.8,10.5275,-66.09973,10.52972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.09973,10.52972,-63.8,10.5275), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.8775,10.53611,-62.3175,10.53666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.3175,10.53666,-62.8775,10.53611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-67.27391,10.54722,-62.3175,10.53666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.17307,10.56361,-71.54529,10.56555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.54529,10.56555,-64.17307,10.56361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.96362,10.57833,-66.04945,10.57972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.04945,10.57972,-63.96362,10.57833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.25362,10.58889,-66.04945,10.57972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.15057,10.60639,-66.70917,10.61972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-66.70917,10.61972,-64.29945,10.62083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.29945,10.62083,-66.70917,10.61972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.18112,10.62666,-64.29945,10.62083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.23972,10.63305,-64.28946,10.63805), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.28946,10.63805,-63.85834,10.64111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.85834,10.64111,-63.68695,10.64333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.68695,10.64333,-63.85834,10.64111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.4425,10.655,-61.91028,10.665), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.91028,10.665,-64.26529,10.66889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-64.26529,10.66889,-61.91028,10.665), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.0864,10.70277,-63.81639,10.70944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.81639,10.70944,-63.04028,10.71361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-63.04028,10.71361,-71.57722,10.71444), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.57722,10.71444,-63.04028,10.71361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.52667,10.72694,-62.79139,10.73111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.79139,10.73111,-71.52667,10.72694), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-62.55306,10.73639,-62.79139,10.73111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-61.85417,10.74583,-62.55306,10.73639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.32861,10.7725,-71.44667,10.79555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.44667,10.79555,-71.58168,10.80166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.58168,10.80166,-71.44667,10.79555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.43333,10.82416,-71.58168,10.80166), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.31862,10.85111,-68.24445,10.86278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.24445,10.86278,-68.31862,10.85111), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.25168,10.89722,-68.37195,10.90889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.37195,10.90889,-71.41446,10.91361), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.41446,10.91361,-68.37195,10.90889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.27528,10.92472,-68.29834,10.92722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.29834,10.92722,-68.27528,10.92472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.58444,10.93722,-68.32779,10.93861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.32779,10.93861,-72.58444,10.93722), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.49861,10.95639,-68.32779,10.93861), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.66223,10.98583,-71.76556,11.01055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.76556,11.01055,-71.63196,11.01944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.63196,11.01944,-71.76556,11.01055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.735,11.0325,-71.63196,11.01944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.08389,11.07611,-71.75473,11.08639), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.75473,11.08639,-71.08389,11.07611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.49306,11.12111,-72.26501,11.1525), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.26501,11.1525,-68.39612,11.16278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.39612,11.16278,-72.34138,11.165), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-72.34138,11.165,-68.39612,11.16278), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.77473,11.21916,-72.34138,11.165), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.91473,11.31944,-68.78168,11.39083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.35197,11.31944,-68.78168,11.39083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-68.78168,11.39083,-70.14612,11.41472), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.14612,11.41472,-69.7989,11.42777), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.7989,11.42777,-69.88806,11.43), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.88806,11.43,-69.7989,11.42777), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.04001,11.43778,-69.88806,11.43), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.60918,11.45889,-70.15668,11.47416), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.15668,11.47416,-69.60918,11.45889), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.74167,11.48944,-69.36974,11.49055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.36974,11.49055,-69.74167,11.48944), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.66223,11.49639,-69.36974,11.49055), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.04529,11.505,-69.52667,11.50611), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.52667,11.50611,-70.04529,11.505), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.28835,11.53389,-71.97029,11.53666), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.97029,11.53666,-69.28835,11.53389), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.95668,11.59417,-70.18279,11.60305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.18279,11.60305,-71.90889,11.60833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.90889,11.60833,-70.18279,11.60305), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.22697,11.61555,-71.90889,11.60833), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.74278,11.62889,-70.22697,11.61555), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.97722,11.665,-69.81473,11.69083), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.81473,11.69083,-71.97722,11.665), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.37862,11.75333,-70.2439,11.77527), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.2439,11.77527,-71.37862,11.75333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.40361,11.81278,-70.2439,11.77527), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-71.3248,11.85264,-70.30112,11.85333), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.30112,11.85333,-71.3248,11.85264), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.82445,11.99083,-70.20671,12.11121), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.20671,12.11121,-70.12083,12.12972), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.12083,12.12972,-70.20671,12.11121), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-69.93472,12.16972,-70.02724,12.19528), mapfile, tile_dir, 0, 11, "ve-venezuela")
	render_tiles((-70.02724,12.19528,-69.93472,12.16972), mapfile, tile_dir, 0, 11, "ve-venezuela")