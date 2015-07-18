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
    # Region: FI
    # Region Name: Finland

	render_tiles((19.76139,60.07221,19.95778,60.08943), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.76139,60.07221,19.95778,60.08943), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.95778,60.08943,20.05083,60.08971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.05083,60.08971,19.95778,60.08943), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.17167,60.16388,20.04417,60.17194), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.04417,60.17194,20.08555,60.17416), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.08555,60.17416,20.04417,60.17194), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.94583,60.18082,20.01639,60.18138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.01639,60.18138,19.94583,60.18082), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.055,60.18888,19.7925,60.19138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.7925,60.19138,19.82111,60.19388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.82111,60.19388,19.7925,60.19138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.92167,60.22054,20.10583,60.22137), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.10583,60.22137,19.92167,60.22054), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.08916,60.22777,20.10583,60.22137), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.64833,60.25665,20.02111,60.26332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.02111,60.26332,19.68528,60.26388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.68528,60.26388,20.02111,60.26332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.97222,60.2686,19.68528,60.26388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.2775,60.27415,19.97222,60.2686), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.93528,60.28277,20.2775,60.27415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.03722,60.29832,19.78638,60.29971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.78638,60.29971,20.03722,60.29832), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.85083,60.31248,19.78638,60.29971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.14139,60.33054,19.89861,60.33971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.89861,60.33971,20.14139,60.33054), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.78,60.34915,19.9275,60.34999), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.9275,60.34999,19.78,60.34915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((19.90583,60.39999,19.9275,60.34999), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.47277,59.99693,22.73611,60.00304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.47277,59.99693,22.73611,60.00304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.73611,60.00304,22.57694,60.00443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.57694,60.00443,22.73611,60.00304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.83916,60.09637,22.42416,60.10832), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.42416,60.10832,22.83916,60.09637), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.59777,60.13165,22.83555,60.13805), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.83555,60.13805,22.42167,60.14388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.42167,60.14388,22.83555,60.13805), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.45166,60.18471,22.44361,60.21333), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.44361,60.21333,22.81555,60.22499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.81555,60.22499,22.44361,60.21333), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.95361,60.28777,22.81555,60.22499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.90972,59.80499,22.93472,59.84055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.93472,59.84055,23.25305,59.8436), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.25305,59.8436,22.93472,59.84055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.21389,59.88416,23.16555,59.8861), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.16555,59.8861,23.21389,59.88416), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.15916,59.91721,23.25722,59.91943), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.25722,59.91943,23.15916,59.91721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.22497,59.925,23.34694,59.92776), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.34694,59.92776,23.22497,59.925), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.15055,59.93332,23.34694,59.92776), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.43444,59.95055,23.70194,59.95443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.70194,59.95443,23.43444,59.95055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.10778,59.96832,23.70194,59.95443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.41333,59.9911,24.48222,59.99138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.48222,59.99138,24.41333,59.9911), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.32333,59.99249,24.48222,59.99138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.22472,60.00332,24.015,60.00416), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.015,60.00416,23.22472,60.00332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.47944,60.00721,24.015,60.00416), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.33833,60.01999,23.31944,60.02499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.31944,60.02499,24.345,60.02583), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.345,60.02583,23.31944,60.02499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.45778,60.02749,24.345,60.02583), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.04944,60.0361,24.015,60.03749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.015,60.03749,23.25,60.0386), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.25,60.0386,24.015,60.03749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.16111,60.04305,24.46055,60.04527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.46055,60.04527,24.16111,60.04305), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.54611,60.06721,24.36666,60.06805), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.36666,60.06805,23.54611,60.06721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.97222,60.09193,24.67139,60.10055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.67139,60.10055,22.97222,60.09193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.6025,60.11749,24.5875,60.12888), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.5875,60.12888,24.85083,60.13666), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.85083,60.13666,24.965,60.14138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.965,60.14138,25.03111,60.14499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.03111,60.14499,24.965,60.14138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.87944,60.15083,24.55389,60.15443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.55389,60.15443,22.87944,60.15083), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.85778,60.15443,22.87944,60.15083), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.01639,60.17194,25.19527,60.18638), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.19527,60.18638,24.84278,60.19221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.84278,60.19221,25.19527,60.18638), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.9825,60.20026,25.02444,60.20138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.02444,60.20138,24.9825,60.20026), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.64222,60.22027,22.59027,60.22999), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.59027,60.22999,25.78389,60.23804), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.78389,60.23804,25.37833,60.23999), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.37833,60.23999,25.8875,60.24165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.8875,60.24165,25.20444,60.24221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.20444,60.24221,25.8875,60.24165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.44722,60.24443,25.20444,60.24221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.92861,60.2486,22.44722,60.24443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.51889,60.25555,25.92861,60.2486), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.78028,60.27221,25.90222,60.28555), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.90222,60.28555,25.65361,60.29166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.65361,60.29166,26.08305,60.29499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.08305,60.29499,25.65361,60.29166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.76361,60.31165,25.52694,60.32388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.52694,60.32388,26.11166,60.33027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.11166,60.33027,23.06222,60.33193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.06222,60.33193,26.11166,60.33027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.705,60.33887,25.99611,60.34471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.99611,60.34471,25.705,60.33887), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.05583,60.35332,25.91528,60.35582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.91528,60.35582,23.05583,60.35332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.6725,60.36277,22.56694,60.36555), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.56694,60.36555,25.6725,60.36277), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.28527,60.37138,26.34638,60.37276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.34638,60.37276,22.28527,60.37138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.63139,60.3811,26.34638,60.37276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.00333,60.39193,22.47667,60.40027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.47667,60.40027,25.83805,60.4011), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.83805,60.4011,22.47667,60.40027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.18833,60.40999,26.69971,60.4136), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.69971,60.4136,26.04667,60.41582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.04667,60.41582,26.69971,60.4136), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.06111,60.42387,22.17583,60.43138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.17583,60.43138,26.48222,60.43694), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.48222,60.43694,26.51444,60.44027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.51444,60.44027,22.03167,60.44221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.03167,60.44221,26.51444,60.44027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.96749,60.44527,22.03167,60.44221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.57611,60.45026,26.96749,60.44527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.44639,60.46138,25.94083,60.46665), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.94083,60.46665,27.44639,60.46138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.62611,60.46665,27.44639,60.46138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.87416,60.47276,26.48004,60.47452), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.48004,60.47452,21.87416,60.47276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.47305,60.47638,26.48004,60.47452), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.78361,60.48166,25.93694,60.48305), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.93694,60.48305,26.79472,60.48415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.79472,60.48415,21.55944,60.48471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.55944,60.48471,26.79472,60.48415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.64861,60.48804,21.62861,60.4886), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.62861,60.4886,27.64861,60.48804), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.75166,60.50054,27.60667,60.5061), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.60667,60.5061,27.48722,60.50694), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.48722,60.50694,26.46194,60.50749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.46194,60.50749,27.48722,60.50694), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.58333,60.5161,27.68333,60.51749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.68333,60.51749,21.8375,60.51832), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.8375,60.51832,27.68333,60.51749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.9375,60.52138,27.14583,60.52165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.14583,60.52165,21.9375,60.52138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.04028,60.54166,21.67028,60.54332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.67028,60.54332,27.04028,60.54166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.79477,60.54756,27.735,60.55166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.735,60.55166,27.21444,60.55471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.21444,60.55471,27.735,60.55166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.8,60.56693,26.75389,60.57166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.75389,60.57166,27.77472,60.57388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.77472,60.57388,26.75389,60.57166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.41083,60.58221,21.845,60.58721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.845,60.58721,21.41083,60.58221), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.595,60.59805,21.46583,60.60582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.46583,60.60582,26.595,60.59805), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.83194,60.62276,21.39528,60.63693), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.39528,60.63693,26.61555,60.64027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.61555,60.64027,26.69527,60.64082), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.69527,60.64082,26.61555,60.64027), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.355,60.6836,21.44167,60.69693), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.44167,60.69693,21.355,60.6836), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.38944,60.7461,21.44167,60.69693), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.31972,60.86304,21.42333,60.86388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.42333,60.86388,21.31972,60.86304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.42805,60.90554,21.42416,60.9061), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.42416,60.9061,28.42805,60.90554), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.63888,60.96609,21.42416,60.9061), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.71222,61.04166,21.29673,61.05728), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.29673,61.05728,21.48278,61.05971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.48278,61.05971,21.29673,61.05728), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.43472,61.13915,29.0025,61.17721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.0025,61.17721,21.55944,61.20388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.55944,61.20388,29.0025,61.17721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.4775,61.24776,21.54444,61.27637), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.54444,61.27637,21.4775,61.24776), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.52722,61.37444,21.44527,61.40166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.44527,61.40166,21.53167,61.40527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.53167,61.40527,21.44527,61.40166), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.56,61.42721,21.53167,61.40527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.58666,61.47721,21.42389,61.48388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.42389,61.48388,21.58666,61.47721), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.58583,61.49249,21.42389,61.48388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.71639,61.52055,21.48694,61.53944), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.48694,61.53944,21.71639,61.52055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.73444,61.56526,21.49333,61.57388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.49333,61.57388,29.73444,61.56526), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.61055,61.58749,21.49333,61.57388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.83972,61.65749,21.54305,61.67527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.54305,61.67527,29.83972,61.65749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.49389,61.73471,30.03976,61.78497), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.03976,61.78497,21.49389,61.73471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.15298,61.85712,30.03976,61.78497), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.30917,61.93332,21.37861,61.93471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.37861,61.93471,21.30917,61.93332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.24833,61.99999,21.37861,61.93471), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.54583,62.10749,21.39,62.2036), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.39,62.2036,30.54583,62.10749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.19222,62.33332,21.2775,62.3361), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.2775,62.3361,21.19222,62.33332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.32777,62.35221,21.2775,62.3361), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.1875,62.39193,21.125,62.4061), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.125,62.4061,21.1875,62.39193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.26917,62.4061,21.1875,62.39193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.26472,62.51276,21.12527,62.54749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.12527,62.54749,31.26472,62.51276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.20694,62.59026,21.06416,62.59637), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.06416,62.59637,21.20694,62.59026), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.08055,62.67194,21.12583,62.68443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.12583,62.68443,21.08055,62.67194), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.12861,62.73055,31.43,62.75888), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.43,62.75888,21.1125,62.77443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.1125,62.77443,21.185,62.78499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.185,62.78499,21.1125,62.77443), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.37083,62.85915,21.24,62.8686), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.24,62.8686,21.37083,62.85915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.5873,62.91383,21.4625,62.94943), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.4625,62.94943,31.5873,62.91383), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.62,63.0186,21.68278,63.02971), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.68278,63.02971,21.62,63.0186), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.43916,63.04249,21.62805,63.05499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.62805,63.05499,21.51972,63.0661), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.51972,63.0661,21.62805,63.05499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.275,63.11776,21.9825,63.14054), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.9825,63.14054,31.275,63.11776), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.88166,63.17527,21.95916,63.17915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.95916,63.17915,21.88166,63.17527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.49583,63.18721,31.25055,63.19165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.25055,63.19165,21.64722,63.19193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.64722,63.19193,31.25055,63.19165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.49778,63.20999,22.16555,63.22193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.16555,63.22193,21.70305,63.22332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.70305,63.22332,22.16555,63.22193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.54277,63.23193,31.19611,63.23415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((31.19611,63.23415,21.54277,63.23193), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.04556,63.24055,31.19611,63.23415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.89083,63.25332,22.04556,63.24055), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.31805,63.26916,21.89083,63.25332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.3475,63.2861,22.28,63.29527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.28,63.29527,22.3475,63.2861), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.96249,63.30971,22.28,63.29527), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.35444,63.34499,30.90416,63.35749), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.90416,63.35749,22.35444,63.34499), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.40083,63.41637,22.19806,63.42138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.19806,63.42138,22.40083,63.41637), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.40805,63.45221,22.19778,63.46555), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.19778,63.46555,30.48166,63.47859), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.48166,63.47859,22.19778,63.46555), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.26444,63.50082,22.33778,63.51832), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.33778,63.51832,22.28389,63.52415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.28389,63.52415,22.33778,63.51832), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.49305,63.57082,22.28389,63.52415), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.74083,63.61777,22.50278,63.62388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.50278,63.62388,22.74083,63.61777), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.82444,63.64138,22.50278,63.62388), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.5925,63.66332,22.82444,63.64138), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.68055,63.69777,22.58694,63.70582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.58694,63.70582,22.68055,63.69777), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.0275,63.71804,22.58694,63.70582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.00472,63.75277,22.93139,63.75582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.93139,63.75582,23.00417,63.75694), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.00417,63.75694,22.93139,63.75582), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.90916,63.77249,23.00417,63.75694), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.92333,63.8011,22.99111,63.8086), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.99111,63.8086,22.92333,63.8011), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.25583,63.81776,22.99111,63.8086), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.04305,63.83887,30.25583,63.81776), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.24389,63.88638,23.41666,63.92165), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.41666,63.92165,23.24389,63.88638), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.3675,63.99693,23.61,64.02414), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.61,64.02414,30.59528,64.04692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.59528,64.04692,23.62416,64.04747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.62416,64.04747,30.59528,64.04692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.39583,64.04997,23.62416,64.04747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.61444,64.08664,23.5675,64.09914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.5675,64.09914,30.61444,64.08664), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.63722,64.11664,23.5675,64.09914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.55944,64.15608,23.63722,64.11664), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.55805,64.20026,23.83139,64.22414), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.83139,64.22414,30.56083,64.24442), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.56083,64.24442,23.94555,64.24609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.94555,64.24609,30.56083,64.24442), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.85639,64.26053,23.94555,64.24609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.35944,64.3197,30.15611,64.35414), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.15611,64.35414,23.99944,64.3858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.99944,64.3858,30.05166,64.40553), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.05166,64.40553,23.99944,64.3858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.20916,64.45081,30.08722,64.48303), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.08722,64.48303,24.20916,64.45081), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.00277,64.51775,30.08722,64.48303), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.98083,64.5858,30.17888,64.63164), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.17888,64.63164,30.20916,64.67274), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.20916,64.67274,30.17888,64.63164), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.13139,64.72163,30.13028,64.75304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.13028,64.75304,24.56055,64.76831), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.56055,64.76831,30.13028,64.75304), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.675,64.7847,30.08778,64.79164), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.08778,64.79164,29.79416,64.7972), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.79416,64.7972,30.08778,64.79164), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.26083,64.81358,24.68083,64.82915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.68083,64.82915,25.36416,64.83192), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.36416,64.83192,24.68083,64.82915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.30305,64.90387,25.36083,64.90665), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.36083,64.90665,25.11278,64.90942), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.11278,64.90942,25.36083,64.90665), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.645,64.91386,25.11278,64.90942), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.42083,64.94414,25.4486,64.96053), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.4486,64.96053,25.18833,64.96609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.18833,64.96609,25.4486,64.96053), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.21861,64.98915,29.60638,65.0072), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.60638,65.0072,25.21861,64.98915), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.40833,65.03859,29.63722,65.06831), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.63722,65.06831,25.40833,65.03859), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.21305,65.12469,29.87444,65.12579), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.87444,65.12579,25.21305,65.12469), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.82333,65.14886,29.87444,65.12579), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.85861,65.17552,29.82333,65.14886), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.84305,65.21997,29.60944,65.23524), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.60944,65.23524,25.29972,65.23747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.29972,65.23747,29.60944,65.23524), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.59888,65.2597,25.29972,65.23747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.31889,65.31442,29.74944,65.34775), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.74944,65.34775,25.26639,65.3772), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.26639,65.3772,29.74944,65.34775), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.35527,65.41081,25.26639,65.3772), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.34222,65.48442,29.74611,65.51442), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.74611,65.51442,25.34222,65.48442), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.84555,65.55414,29.85527,65.57608), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.85527,65.57608,29.84555,65.55414), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.74583,65.62108,25.00444,65.62248), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.00444,65.62248,29.74583,65.62108), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.67056,65.63332,29.72888,65.6422), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.72888,65.6422,24.67056,65.63332), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.12389,65.66498,29.72888,65.6422), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.02444,65.69107,30.12389,65.66498), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.13416,65.71913,24.55472,65.73248), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.55472,65.73248,30.13416,65.71913), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.21444,65.77498,24.24722,65.77609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.24722,65.77609,24.21444,65.77498), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.55639,65.78693,24.51722,65.79109), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.51722,65.79109,24.55639,65.78693), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.16707,65.81284,24.23666,65.81636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.23666,65.81636,24.16707,65.81284), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.64805,65.88164,24.23666,65.81636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.94833,66.0497,24.005,66.05275), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.005,66.05275,29.94833,66.0497), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.71916,66.20497,29.61833,66.34581), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.61833,66.34581,23.65777,66.40692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.65777,66.40692,23.65889,66.46053), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.65889,66.46053,29.54833,66.49551), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.54833,66.49551,23.65889,66.46053), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.8875,66.56998,29.38713,66.62485), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.38713,66.62485,23.8875,66.56998), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.35027,66.68246,29.38713,66.62485), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.9,66.7547,29.15944,66.79913), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.15944,66.79913,24.00777,66.80052), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.00777,66.80052,29.15944,66.79913), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.075,66.87996,23.93583,66.88414), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.93583,66.88414,29.075,66.87996), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.08778,66.96997,23.72583,67.01276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.72583,67.01276,29.13416,67.0136), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.13416,67.0136,23.72583,67.01276), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.57389,67.16774,23.61139,67.2072), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.61139,67.2072,29.40611,67.20914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.40611,67.20914,23.61139,67.2072), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.58444,67.22386,29.40611,67.20914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.61444,67.26331,23.74166,67.2847), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.74166,67.2847,23.61444,67.26331), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.78194,67.32637,23.74166,67.2847), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.76194,67.42024,23.50055,67.43636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.50055,67.43636,23.76194,67.42024), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.54916,67.45331,23.43111,67.46553), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.43111,67.46553,23.54916,67.45331), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.43888,67.49858,29.93277,67.51387), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.93277,67.51387,23.43888,67.49858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.97777,67.57248,23.55361,67.58748), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.55361,67.58748,29.97777,67.57248), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.98638,67.61525,23.55361,67.58748), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((30.02861,67.69469,29.98638,67.61525), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.47722,67.77498,29.69166,67.81525), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.69166,67.81525,23.47722,67.77498), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.49249,67.87579,23.66555,67.92996), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.66555,67.92996,23.65694,67.95081), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.65694,67.95081,23.66555,67.92996), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.54222,67.97803,23.65694,67.95081), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.35694,68.08247,23.14777,68.12746), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.14777,68.12746,23.29444,68.1422), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.29444,68.1422,23.14777,68.12746), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.15944,68.17885,28.69666,68.1933), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.69666,68.1933,23.15944,68.17885), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.11972,68.24191,28.69666,68.1933), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.67389,68.42081,22.05055,68.47914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.05055,68.47914,28.45861,68.5347), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.45861,68.5347,24.88139,68.55858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.88139,68.55858,24.91833,68.5619), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.91833,68.5619,24.88139,68.55858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.89722,68.56998,21.7575,68.57692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.7575,68.57692,21.89722,68.56998), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.93555,68.59386,21.7575,68.57692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.20416,68.62857,25.09583,68.62914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.09583,68.62914,23.20416,68.62857), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.66861,68.6308,25.09583,68.62914), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.79778,68.64081,21.66861,68.6308), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.69027,68.67636,23.06388,68.69551), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.06388,68.69551,22.39833,68.71109), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.39833,68.71109,23.70361,68.71526), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.70361,68.71526,22.39833,68.71109), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.75916,68.75192,24.13139,68.77997), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.13139,68.77997,25.16666,68.7858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.16666,68.7858,24.13139,68.77997), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.20777,68.8197,28.80583,68.82137), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.80583,68.82137,21.20777,68.8197), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((24.04472,68.82387,28.80583,68.82137), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((23.85638,68.83247,24.04472,68.82387), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((22.34861,68.84247,23.85638,68.83247), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.73777,68.87579,25.38472,68.88052), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.38472,68.88052,25.61222,68.88135), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.61222,68.88135,25.38472,68.88052), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.43222,68.88637,25.61222,68.88135), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.89528,68.89359,28.43222,68.88637), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.45694,68.9183,20.89138,68.92685), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.89138,68.92685,28.45694,68.9183), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.93111,68.94635,20.89138,68.92685), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.86666,69.00497,25.80916,69.01137), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.80916,69.01137,20.86666,69.00497), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.0675,69.03914,28.96477,69.05191), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.96477,69.05191,20.58609,69.06314), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.58609,69.06314,28.96477,69.05191), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((20.73833,69.09636,21.11305,69.10802), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.11305,69.10802,28.78694,69.11913), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.78694,69.11913,21.06611,69.12636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.06611,69.12636,28.78694,69.11913), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.78167,69.1497,21.06611,69.12636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.03972,69.18246,25.70861,69.20663), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.70861,69.20663,21.055,69.22858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.055,69.22858,28.82694,69.23802), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.82694,69.23802,21.055,69.22858), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.68194,69.2847,21.32083,69.32608), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((21.32083,69.32608,25.75111,69.33609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.75111,69.33609,21.32083,69.32608), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.83055,69.3783,25.75111,69.33609), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.28555,69.46359,29.29472,69.49524), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.29472,69.49524,29.28555,69.46359), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.85305,69.54692,25.93444,69.56636), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.93444,69.56636,25.85305,69.54692), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.97583,69.63025,25.94222,69.66747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.94222,69.66747,25.97583,69.63025), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((25.98888,69.70663,29.09888,69.70802), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((29.09888,69.70802,25.98888,69.70663), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.37972,69.82747,26.36972,69.84998), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.36972,69.84998,28.37972,69.82747), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.03861,69.90831,26.46055,69.93192), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.46055,69.93192,28.09416,69.93857), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.09416,69.93857,26.46055,69.93192), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.2925,69.95053,26.80777,69.95192), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((26.80777,69.95192,27.2925,69.95053), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.37722,70.00276,26.80777,69.95192), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.01524,70.06937,27.80666,70.07942), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((28.01524,70.06937,27.80666,70.07942), mapfile, tile_dir, 0, 11, "fi-finland")
	render_tiles((27.80666,70.07942,28.01524,70.06937), mapfile, tile_dir, 0, 11, "fi-finland")