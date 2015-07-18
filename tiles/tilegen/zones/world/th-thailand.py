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
    # Region: TH
    # Region Name: Thailand

	render_tiles((98.31081,7.75583,98.39914,7.81167), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.31081,7.75583,98.39914,7.81167), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.39914,7.81167,98.35831,7.83361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.35831,7.83361,98.39914,7.81167), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.26498,7.8775,98.35831,7.83361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.43137,8.09194,98.37415,8.09583), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.37415,8.09583,98.43137,8.09194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.33273,8.16226,98.28247,8.18639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.28247,8.18639,98.33273,8.16226), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1397,5.63194,101.2489,5.69916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2489,5.69916,101.0841,5.71417), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0841,5.71417,101.2489,5.69916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0244,5.73278,101.8241,5.73944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.8241,5.73944,101.0244,5.73278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.685,5.77083,101.8241,5.73944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2819,5.80444,100.9936,5.80611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9936,5.80611,101.2819,5.80444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.6589,5.86056,101.9438,5.86194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.9438,5.86194,101.6589,5.86056), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.57,5.91667,101.9461,5.96472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.9461,5.96472,101.1202,5.99055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1202,5.99055,101.9461,5.96472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.0916,6.11055,101.0791,6.16083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0791,6.16083,101.1216,6.18722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1216,6.18722,101.0791,6.16083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.0944,6.23618,100.9458,6.23861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9458,6.23861,102.0944,6.23618), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8522,6.24278,100.9458,6.23861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1155,6.24889,100.8522,6.24278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9897,6.2775,101.1155,6.24889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8541,6.32555,100.9897,6.2775), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1286,6.41779,100.655,6.44833), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.655,6.44833,101.7936,6.47), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.7936,6.47,100.1714,6.47667), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1714,6.47667,101.7936,6.47), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.7486,6.50361,100.0425,6.5175), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.0425,6.5175,100.7486,6.50361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.095,6.53639,100.3711,6.5475), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3711,6.5475,100.095,6.53639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.95636,6.65805,100.19,6.6875), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.19,6.6875,100.2989,6.70167), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2989,6.70167,100.2086,6.71056), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2086,6.71056,100.2989,6.70167), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0355,6.84333,101.5119,6.86389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.5119,6.86389,101.2197,6.86639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2197,6.86639,101.5119,6.86389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.3089,6.8725,101.2197,6.86639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.68442,6.88,101.3089,6.8725), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.3594,6.88916,99.68442,6.88), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2897,6.93778,100.7936,6.95806), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.7936,6.95806,101.2897,6.93778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.68082,6.99416,100.7936,6.95806), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.67303,7.11916,100.4747,7.125), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4747,7.125,99.74969,7.13), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.74969,7.13,100.4747,7.125), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.61276,7.13639,100.5522,7.13917), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5522,7.13917,99.61276,7.13639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.6683,7.1675,100.5675,7.18917), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5675,7.18917,100.393,7.20555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.393,7.20555,100.5944,7.21278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5944,7.21278,100.5819,7.21639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5819,7.21639,100.5944,7.21278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.53497,7.24139,100.5819,7.21639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4239,7.26639,99.47803,7.275), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.47803,7.275,100.4239,7.26639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.53247,7.29111,99.42831,7.29555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.42831,7.29555,99.53247,7.29111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3753,7.31444,99.42831,7.29555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.52136,7.35417,99.49303,7.36805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.49303,7.36805,99.60387,7.37111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.60387,7.37111,99.33998,7.37194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.33998,7.37194,99.60387,7.37111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2661,7.37833,99.33998,7.37194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4472,7.43861,100.2661,7.37833), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4047,7.52361,100.303,7.53), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.303,7.53,100.4047,7.52361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2655,7.54055,100.303,7.53), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3769,7.565,100.3283,7.5675), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3283,7.5675,100.3769,7.565), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.153,7.61555,100.3239,7.64694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3239,7.64694,99.25941,7.65639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.25941,7.65639,100.3239,7.64694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.20137,7.6825,99.10471,7.69194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.10471,7.69194,99.20137,7.6825), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.03804,7.70305,99.10471,7.69194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1478,7.72278,99.03804,7.70305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.02525,7.75861,99.1297,7.76333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.1297,7.76333,99.02525,7.75861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2858,7.7875,100.2539,7.78944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2539,7.78944,100.2858,7.7875), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.03137,7.89722,99.07025,7.91778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.07025,7.91778,99.03137,7.89722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3266,7.98333,98.83054,8.00194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.83054,8.00194,100.3266,7.98333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.91304,8.05055,98.83054,8.00194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.41969,8.14333,98.46776,8.21778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.46776,8.21778,98.27664,8.22389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.27664,8.22389,98.46776,8.21778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.70526,8.27139,98.64609,8.27472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.64609,8.27472,98.70526,8.27139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.61914,8.29222,98.68498,8.305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.68498,8.305,98.41776,8.30916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.41776,8.30916,98.68498,8.305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.63942,8.36305,100.1719,8.38527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1719,8.38527,100.238,8.40666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.238,8.40666,100.1103,8.40944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1103,8.40944,100.238,8.40666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1758,8.50333,98.24692,8.51111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.24692,8.51111,100.1758,8.50333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.25081,8.535,98.19331,8.55694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.19331,8.55694,98.25081,8.535), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.20636,8.58639,99.96581,8.60527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.96581,8.60527,98.20636,8.58639), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.21942,8.7275,99.96581,8.60527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.91887,8.97222,98.36359,9.02055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.36359,9.02055,98.32275,9.03583), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.32275,9.03583,98.36359,9.02055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.38387,9.08444,99.91942,9.08889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.91942,9.08889,98.38387,9.08444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.34331,9.12722,99.91942,9.08889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.37665,9.18917,99.49525,9.21083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.49525,9.21083,99.25775,9.22528), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.25775,9.22528,99.49525,9.21083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.61331,9.26861,99.53664,9.28111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.53664,9.28111,99.61331,9.26861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.65387,9.2975,99.22664,9.30472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.22664,9.30472,99.80942,9.31083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.80942,9.31083,99.22664,9.30472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.27054,9.36889,99.32082,9.3925), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.32082,9.3925,99.27054,9.36889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.49193,9.52917,99.22304,9.54361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.22304,9.54361,98.45804,9.55527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.45804,9.55527,99.22304,9.54361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.48581,9.57194,98.53693,9.57333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.53693,9.57333,98.48581,9.57194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.46498,9.62416,98.53693,9.57333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.53497,9.70166,98.50359,9.74361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.50359,9.74361,98.55887,9.74472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.55887,9.74472,98.50359,9.74361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.14581,9.775,98.55887,9.74472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.54831,9.80694,99.14581,9.775), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.51526,9.84139,98.57109,9.86861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.57109,9.86861,98.53554,9.87555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.53554,9.87555,98.57109,9.86861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.59608,9.9725,98.53554,9.87555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.15025,10.11028,98.67386,10.11805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.67386,10.11805,99.15025,10.11028), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.19582,10.20472,99.25081,10.22083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.25081,10.22083,99.19582,10.20472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.15831,10.29916,98.74255,10.34643), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.74255,10.34643,99.27582,10.35416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.27582,10.35416,98.74255,10.34643), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.14914,10.36555,99.27582,10.35416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.28886,10.39889,99.14914,10.36555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.24025,10.45417,99.28886,10.39889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.82387,10.51667,99.24025,10.45417), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.30054,10.62611,98.78192,10.66666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.78192,10.66666,99.30054,10.62611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.85553,10.76722,99.42581,10.78305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.42581,10.78305,98.85553,10.76722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.00331,10.82861,99.50415,10.86083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.50415,10.86083,99.47081,10.87666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.47081,10.87666,99.50415,10.86083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.07747,10.94861,99.02025,10.96333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.02025,10.96333,99.07747,10.94861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.49193,11.11555,99.58331,11.18805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.58331,11.18805,99.5547,11.19777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.5547,11.19777,99.58331,11.18805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.56053,11.30861,99.35831,11.35), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.35831,11.35,99.40276,11.38889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.40276,11.38889,99.35831,11.35), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.46553,11.60472,99.55746,11.62778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.55746,11.62778,99.48996,11.62916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.48996,11.62916,99.55746,11.62778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.9142,11.63342,99.48996,11.62916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.73415,11.68694,102.9142,11.63342), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.9164,11.74277,102.9016,11.74583), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.9016,11.74583,102.9164,11.74277), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.82275,11.77806,102.9016,11.74583), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.6608,11.82055,99.82275,11.77806), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.58942,11.8725,99.82359,11.89111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.82359,11.89111,102.7705,11.90833), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7705,11.90833,99.82359,11.89111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.86081,11.97694,102.7819,11.99972), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7819,11.99972,99.86081,11.97694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7622,12.02944,102.5833,12.05083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5833,12.05083,102.555,12.0525), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.555,12.0525,102.5833,12.05083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.693,12.09056,102.5364,12.09861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5364,12.09861,102.693,12.09056), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.47664,12.13028,99.5733,12.13667), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.5733,12.13667,99.47664,12.13028), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.0175,12.18389,102.5541,12.19861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5541,12.19861,102.283,12.19889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.283,12.19889,102.5541,12.19861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.6025,12.22083,102.283,12.19889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.48579,12.25972,102.2605,12.28806), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.2605,12.28806,102.2653,12.29805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.2653,12.29805,102.7369,12.30361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7369,12.30361,102.2653,12.29805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.3077,12.31573,102.2866,12.32528), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.2866,12.32528,102.3077,12.31573), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.3636,12.33694,102.2866,12.32528), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.3397,12.355,102.3636,12.33694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.283,12.38889,102.2414,12.40389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.2414,12.40389,102.283,12.38889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7886,12.42472,102.2414,12.40389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.41441,12.45444,102.7886,12.42472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.0655,12.48805,102.0325,12.51028), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.0325,12.51028,101.9447,12.52694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.9447,12.52694,102.0428,12.53417), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.0428,12.53417,102.1075,12.53528), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.1075,12.53528,102.0428,12.53417), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.9686,12.53972,102.1075,12.53528), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.43886,12.57111,101.4178,12.58611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.4178,12.58611,99.43886,12.57111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9466,12.60555,101.4178,12.58611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5925,12.62666,101.4939,12.63139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.4939,12.63139,102.5925,12.62666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.6447,12.64555,100.8608,12.64694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8608,12.64694,101.6447,12.64555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.8566,12.65139,100.8608,12.64694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2455,12.65694,100.9261,12.65722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9261,12.65722,101.2455,12.65694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5122,12.665,100.9261,12.65722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.075,12.67416,102.5122,12.665), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.6839,12.69528,100.8391,12.70139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8391,12.70139,99.96498,12.70611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.96498,12.70611,101.7503,12.70666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.7503,12.70666,99.96498,12.70611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8505,12.74972,102.525,12.76694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.525,12.76694,100.8986,12.76917), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8986,12.76917,102.525,12.76694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9178,12.81805,99.20358,12.83305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.20358,12.83305,100.9178,12.81805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8633,12.92389,99.19357,12.96944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.19357,12.96944,102.4958,13.01194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.4958,13.01194,100.9336,13.01889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9336,13.01889,102.4958,13.01194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1047,13.04305,99.11414,13.05083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.11414,13.05083,100.1047,13.04305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8797,13.09028,99.11414,13.05083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.1208,13.17583,100.9436,13.19972), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9436,13.19972,99.20859,13.20667), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.20859,13.20667,100.9436,13.19972), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.98831,13.23194,99.20859,13.20667), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.3514,13.26722,99.95747,13.27722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.95747,13.27722,102.3514,13.26722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9141,13.305,99.95747,13.27722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.21365,13.33534,100.0173,13.36432), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.0173,13.36432,100.9855,13.36861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9855,13.36861,100.0173,13.36432), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9225,13.46305,100.7708,13.48889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.7708,13.48889,100.2661,13.49055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.2661,13.49055,100.7708,13.48889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9905,13.49389,100.2661,13.49055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5808,13.52139,100.9905,13.49389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5932,13.55115,102.5272,13.56778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5272,13.56778,102.3602,13.57166), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.3602,13.57166,102.5272,13.56778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.593,13.60611,102.6227,13.60694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.6227,13.60694,100.593,13.60611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5605,13.6525,102.5633,13.68139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5633,13.68139,102.5605,13.6525), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.18053,13.7125,102.5633,13.68139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.7222,13.76361,99.18053,13.7125), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.79,13.93444,98.93718,14.10222), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.93718,14.10222,102.9197,14.10472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.9197,14.10472,98.93718,14.10222), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.958,14.20305,98.7597,14.2175), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.7597,14.2175,105.0694,14.21833), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.0694,14.21833,98.7597,14.2175), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.1372,14.23972,105.033,14.24777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.033,14.24777,105.1372,14.23972), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.66969,14.29972,103.2286,14.33278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.2286,14.33278,105.2104,14.35256), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.2104,14.35256,103.9736,14.36444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.9736,14.36444,104.5716,14.36472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.5716,14.36472,103.9736,14.36444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.4433,14.37222,104.5716,14.36472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.57137,14.38194,105.0019,14.38333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.0019,14.38333,98.57137,14.38194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.1875,14.38667,105.0019,14.38333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.7008,14.39055,104.1875,14.38667), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.5078,14.39666,103.7008,14.39055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.8805,14.4225,104.7822,14.43055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.7822,14.43055,105.4419,14.43222), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4419,14.43222,104.7822,14.43055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.715,14.43777,103.6958,14.44), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.6958,14.44,104.715,14.43777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.6591,14.44778,103.6958,14.44), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.5378,14.57777,98.44774,14.60583), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.44774,14.60583,105.5378,14.57777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.5214,14.79,98.26414,14.80694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.26414,14.80694,105.5214,14.79), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.6191,14.99166,105.4689,15.125), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4689,15.125,105.4936,15.20527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4936,15.20527,98.20026,15.21555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.20026,15.21555,105.4936,15.20527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.41359,15.26,105.5964,15.27472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.5964,15.27472,98.41359,15.26), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.31024,15.30305,98.55885,15.33), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.55885,15.33,105.588,15.33552), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.588,15.33552,98.55885,15.33), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.483,15.34166,105.588,15.33552), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.42247,15.35999,105.483,15.37694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.483,15.37694,98.50359,15.38722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.50359,15.38722,105.483,15.37694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.59358,15.41694,105.6,15.43305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.6,15.43305,98.59358,15.41694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.6344,15.59472,105.6325,15.67861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.6325,15.67861,98.56525,15.72278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.56525,15.72278,105.5964,15.72805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.5964,15.72805,98.56525,15.72278), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4269,15.77583,105.5964,15.72805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.3497,15.93805,98.61746,15.97), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.61746,15.97,105.4069,15.99222), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4069,15.99222,98.61746,15.97), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.4119,16.0186,105.4069,15.99222), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.57137,16.0461,105.183,16.05888), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.183,16.05888,98.57137,16.0461), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.81497,16.10361,98.77441,16.12333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.77441,16.12333,105.0466,16.1286), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.0466,16.1286,98.77441,16.12333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.86024,16.17083,105.0466,16.1286), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((105.0216,16.23638,98.71581,16.27638), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.71581,16.27638,98.68968,16.28499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.68968,16.28499,98.71581,16.27638), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.9397,16.32249,98.68968,16.28499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.9283,16.37916,104.9397,16.32249), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.86497,16.47666,98.61719,16.51861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.61719,16.51861,104.7461,16.53111), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.7461,16.53111,98.61719,16.51861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.7689,16.69749,98.47942,16.72972), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.47942,16.72972,104.7689,16.69749), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.49135,16.77389,98.54663,16.80777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.54663,16.80777,98.49135,16.77389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.5172,16.86361,104.753,16.88778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.753,16.88778,98.5172,16.86361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.51109,16.9411,104.753,16.88778), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.34608,17.04555,98.42358,17.05333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.42358,17.05333,98.34608,17.04555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.8094,17.19388,98.42358,17.05333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.10802,17.38221,104.7894,17.41472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.7894,17.41472,98.10802,17.38221), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1522,17.4611,98.02885,17.46416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.02885,17.46416,101.1522,17.4611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0991,17.49805,101.23,17.52753), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.23,17.52753,104.6664,17.54249), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.6664,17.54249,101.23,17.52753), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9189,17.56889,97.91164,17.58139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.91164,17.58139,100.9189,17.56889), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9102,17.59638,97.91164,17.58139), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9625,17.66721,104.4444,17.67332), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.4444,17.67332,100.9625,17.66721), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.79385,17.68416,101.3905,17.69249), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.3905,17.69249,97.79385,17.68416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.9805,17.765,101.558,17.78472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.558,17.78472,102.6666,17.8025), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.6666,17.8025,101.5591,17.81479), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.5591,17.81479,102.6666,17.8025), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.69885,17.82888,101.0303,17.83611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0303,17.83611,97.69885,17.82888), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5891,17.845,101.0303,17.83611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.6716,17.86194,102.5891,17.845), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.6208,17.88194,102.6716,17.86194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.2608,17.88194,102.6716,17.86194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.7269,17.90916,102.6127,17.9186), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.6127,17.9186,101.7269,17.90916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.5903,17.95777,97.75026,17.97749), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.75026,17.97749,103.0186,17.97861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.0186,17.97861,97.75026,17.97749), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.9386,18.0036,103.0672,18.02499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.0672,18.02499,101.8903,18.03), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.8903,18.03,103.0672,18.02499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.2933,18.05222,101.8316,18.05389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.8316,18.05389,101.7725,18.05444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.7725,18.05444,101.8316,18.05389), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1908,18.05777,104.15,18.06083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((104.15,18.06083,101.1908,18.05777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.0901,18.13741,103.1408,18.16527), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.1408,18.16527,103.0901,18.13741), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.1764,18.19555,102.1055,18.21027), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((102.1055,18.21027,102.1764,18.19555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.61664,18.24277,103.1705,18.24777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.1705,18.24777,97.61664,18.24277), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1516,18.25777,97.52469,18.265), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.52469,18.265,101.1516,18.25777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.64775,18.27361,103.8686,18.27999), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.8686,18.27999,97.64775,18.27361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.48497,18.29416,103.2825,18.29888), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.2825,18.29888,97.48497,18.29416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.9958,18.3075,97.62302,18.3125), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.62302,18.3125,103.9958,18.3075), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.56358,18.32777,103.95,18.33055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.95,18.33055,97.56358,18.32777), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.1805,18.3336,103.95,18.33055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.2366,18.34666,103.6758,18.35194), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.6758,18.35194,103.2366,18.34666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0916,18.37694,97.44412,18.3961), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.44412,18.3961,103.2816,18.40722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.2816,18.40722,97.44412,18.3961), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((103.3972,18.43499,101.0622,18.45444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0622,18.45444,103.3972,18.43499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.53413,18.48971,97.44246,18.49722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.44246,18.49722,97.53413,18.48971), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.14,18.52749,97.34581,18.54416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.34581,18.54416,101.14,18.52749), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.6633,18.56611,97.77469,18.56999), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.77469,18.56999,97.6633,18.56611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.36191,18.58388,97.77469,18.56999), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2689,18.68694,101.2286,18.72916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2286,18.72916,101.2689,18.68694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2544,18.78499,101.2286,18.72916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.74886,18.85944,97.6783,18.92805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.6783,18.92805,97.74886,18.85944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.3519,19.05166,97.83386,19.09694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.83386,19.09694,101.2872,19.1075), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2872,19.1075,97.83386,19.09694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.84219,19.20444,97.7883,19.27694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.7883,19.27694,97.83748,19.29361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.83748,19.29361,97.7883,19.27694), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2028,19.39083,97.79219,19.39582), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.79219,19.39582,101.2028,19.39083), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.7753,19.48444,100.575,19.49361), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.575,19.49361,100.7753,19.48444), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2852,19.52138,100.4844,19.54472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4844,19.54472,100.6366,19.55055), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.6366,19.55055,100.4844,19.54472), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.19,19.56861,101.2694,19.57916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2694,19.57916,101.19,19.56861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.2191,19.59555,101.2694,19.57916), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((101.0322,19.61944,100.8947,19.61971), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.8947,19.61971,101.0322,19.61944), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((97.98358,19.62388,100.8947,19.61971), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.03497,19.64388,97.98358,19.62388), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.25359,19.67305,98.5558,19.67499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.5558,19.67499,98.25359,19.67305), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.5097,19.71249,98.90886,19.74805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.90886,19.74805,98.67163,19.7486), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.67163,19.7486,98.90886,19.74805), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4061,19.76444,98.67163,19.7486), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.04552,19.79833,98.08081,19.80861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.08081,19.80861,98.83691,19.81638), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((98.83691,19.81638,98.08081,19.80861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.02637,19.82555,98.83691,19.81638), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4833,19.85805,99.02637,19.82555), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.31914,20.06666,99.06693,20.08611), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.06693,20.08611,99.31914,20.06666), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.17024,20.12805,100.5205,20.14499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5205,20.14499,99.53386,20.14722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.53386,20.14722,100.5205,20.14499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.5811,20.15762,99.53386,20.14722), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.4528,20.19333,99.5558,20.19971), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.5558,20.19971,100.4528,20.19333), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.1597,20.23499,100.0972,20.2561), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.0972,20.2561,100.1597,20.23499), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.66637,20.31194,99.79747,20.33138), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.79747,20.33138,100.3761,20.34416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3761,20.34416,100.0923,20.34929), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.0923,20.34929,100.3761,20.34416), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.25,20.37916,99.45609,20.38861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.45609,20.38861,100.3086,20.3936), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.45609,20.38861,100.3086,20.3936), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((100.3086,20.3936,99.45609,20.38861), mapfile, tile_dir, 0, 11, "th-thailand")
	render_tiles((99.95914,20.45527,100.3086,20.3936), mapfile, tile_dir, 0, 11, "th-thailand")