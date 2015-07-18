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
    # Region: GT
    # Region Name: Guatemala

	render_tiles((-90.09509,13.74547,-90.10583,13.83583), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.10583,13.83583,-90.33974,13.84305), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.33974,13.84305,-90.10583,13.83583), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.03751,13.90027,-90.91446,13.92055), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.91446,13.92055,-90.59778,13.92639), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.59778,13.92639,-90.91446,13.92055), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.22528,13.93833,-90.59778,13.92639), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.47528,14.02389,-89.73778,14.04361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.73778,14.04361,-89.8546,14.05946), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.8546,14.05946,-89.73778,14.04361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.74306,14.08389,-89.8546,14.05946), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.69307,14.14694,-89.64612,14.19972), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.64612,14.19972,-89.49271,14.24125), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.49271,14.24125,-89.57501,14.27722), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.57501,14.27722,-91.90112,14.27944), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.90112,14.27944,-89.57501,14.27722), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.55345,14.28625,-91.90112,14.27944), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.59029,14.32194,-89.55345,14.28625), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.52695,14.38638,-89.57333,14.41333), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.57333,14.41333,-89.54222,14.42027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.54222,14.42027,-89.57333,14.41333), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.35487,14.42769,-92.09084,14.43222), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.09084,14.43222,-89.35487,14.42769), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.39612,14.45166,-92.09084,14.43222), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.29944,14.515,-92.24594,14.54808), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.24594,14.54808,-89.29944,14.515), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.15445,14.5825,-92.18721,14.58833), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.18721,14.58833,-89.15445,14.5825), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.14445,14.64861,-92.18721,14.58833), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.1664,14.77277,-92.18611,14.84361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.18611,14.84361,-89.22472,14.86805), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.22472,14.86805,-92.18611,14.84361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.15083,14.97027,-89.15417,14.98138), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.15417,14.98138,-92.15083,14.97027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.18138,15.00333,-89.15417,14.98138), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.15083,15.07361,-92.06583,15.07777), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.06583,15.07777,-89.15083,15.07361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.0089,15.12527,-92.06583,15.07777), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.2114,15.26222,-88.66833,15.35), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.66833,15.35,-92.2114,15.26222), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-92.00917,15.60472,-88.32779,15.63528), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.32779,15.63528,-92.00917,15.60472), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.31389,15.67138,-88.62251,15.69778), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.62251,15.69778,-88.21306,15.72305), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.21306,15.72305,-88.63445,15.72416), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.63445,15.72416,-88.21306,15.72305), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.55695,15.78778,-88.39223,15.83111), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.39223,15.83111,-88.49695,15.83694), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.49695,15.83694,-88.39223,15.83111), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.55724,15.85083,-88.87195,15.86111), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.87195,15.86111,-88.79501,15.86222), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.79501,15.86222,-88.87195,15.86111), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.21777,15.88972,-88.91096,15.89272), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.91096,15.89272,-89.21777,15.88972), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.98083,15.89805,-88.91096,15.89272), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.55862,15.90666,-88.98083,15.89805), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.61612,15.94694,-91.80556,15.94722), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.80556,15.94722,-88.61612,15.94694), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-88.60335,15.96361,-91.80556,15.94722), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.72917,16.07499,-90.44666,16.075), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.44666,16.075,-91.72917,16.07499), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.02917,16.07555,-91.37944,16.07582), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.67944,16.07555,-91.37944,16.07582), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.37944,16.07582,-91.02917,16.07555), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.20222,16.13861,-91.37944,16.07582), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.44583,16.22888,-89.20222,16.13861), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.40388,16.35221,-90.39389,16.41082), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.39389,16.41082,-90.40388,16.35221), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.18056,16.47499,-90.5975,16.48027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.5975,16.48027,-89.18056,16.47499), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.64584,16.54972,-90.5975,16.48027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.67888,16.67582,-90.64584,16.54972), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.80139,16.80361,-89.15834,16.81139), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.15834,16.81139,-90.80139,16.80361), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.94194,16.86166,-91.03944,16.89499), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.03944,16.89499,-90.95084,16.89638), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.95084,16.89638,-91.03944,16.89499), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.98332,16.90305,-90.95084,16.89638), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.20473,17.0611,-89.14417,17.1475), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.14417,17.1475,-91.27722,17.17833), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.27722,17.17833,-89.14417,17.1475), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.98416,17.25611,-91.09111,17.25666), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.09111,17.25666,-90.98416,17.25611), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.43694,17.2575,-91.09111,17.25666), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-91.43694,17.2575,-91.09111,17.25666), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.14278,17.48333,-90.98332,17.59194), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.98332,17.59194,-89.14278,17.48333), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.14244,17.81868,-89.22084,17.81916), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.22084,17.81916,-89.14244,17.81868), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.98277,17.82,-89.57333,17.82027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.57333,17.82027,-90.98277,17.82), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.63028,17.82055,-89.57333,17.82027), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-89.92583,17.82083,-90.63028,17.82055), mapfile, tile_dir, 0, 11, "gt-guatemala")
	render_tiles((-90.27779,17.82111,-89.92583,17.82083), mapfile, tile_dir, 0, 11, "gt-guatemala")