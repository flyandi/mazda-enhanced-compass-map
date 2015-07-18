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
    # Region: TM
    # Region Name: Turkmenistan

	render_tiles((62.30554,35.14554,62.62331,35.22498), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.62331,35.22498,62.45943,35.28638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.45943,35.28638,62.25916,35.29777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.25916,35.29777,62.45943,35.28638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.10082,35.39471,61.80248,35.41109), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.80248,35.41109,62.10082,35.39471), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.57665,35.45082,61.97221,35.45998), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.97221,35.45998,61.57665,35.45082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.11054,35.48137,61.97221,35.45998), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.46499,35.52721,63.11054,35.48137), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.27872,35.60675,63.09526,35.62609), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.09526,35.62609,61.36582,35.63971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.36582,35.63971,63.09526,35.62609), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.23109,35.66749,61.36582,35.63971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.23888,35.69553,61.23109,35.66749), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.10416,35.82582,63.31721,35.85221), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.31721,35.85221,63.11943,35.86193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.11943,35.86193,63.31721,35.85221), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.24499,35.89721,63.53888,35.90971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.53888,35.90971,61.24499,35.89721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.59554,35.9622,61.12526,35.97082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.12526,35.97082,63.59554,35.9622), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.06386,36.00027,61.12526,35.97082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.93387,36.03915,64.06386,36.00027), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.05969,36.08804,61.22609,36.11526), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.22609,36.11526,64.05969,36.08804), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.28247,36.15192,64.16942,36.16749), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.16942,36.16749,64.28247,36.15192), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.32164,36.21638,64.45886,36.24721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.45886,36.24721,64.32164,36.21638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.16998,36.30221,64.57053,36.35638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.57053,36.35638,61.14249,36.39276), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.14249,36.39276,64.57053,36.35638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.62775,36.45998,61.14249,36.39276), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.1897,36.56137,64.61525,36.62943), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.61525,36.62943,60.36471,36.64554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.36471,36.64554,61.1572,36.64999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.1572,36.64999,60.36471,36.64554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.79524,36.92304,60.02943,37.03693), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.02943,37.03693,64.77914,37.09582), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.77914,37.09582,64.79802,37.12498), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.79802,37.12498,59.77443,37.13165), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.77443,37.13165,59.62026,37.13193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.62026,37.13193,59.77443,37.13165), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.51971,37.19609,59.56693,37.20888), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.56693,37.20888,59.51971,37.19609), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.07275,37.24443,65.53081,37.2486), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.53081,37.2486,65.07275,37.24443), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.30275,37.3236,54.23165,37.32721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.23165,37.32721,66.30275,37.3236), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.38971,37.33305,65.6273,37.3332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.6273,37.3332,59.38971,37.33305), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.90942,37.35152,66.53876,37.36051), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.53876,37.36051,53.90942,37.35152), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.89665,37.39749,66.5167,37.4047), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5167,37.4047,53.89665,37.39749), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.55165,37.44609,65.64693,37.45888), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.64693,37.45888,54.69165,37.46748), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.69165,37.46748,66.5717,37.4686), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5717,37.4686,54.69165,37.46748), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.37109,37.50388,59.24999,37.51332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.24999,37.51332,66.5169,37.5217), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5169,37.5217,59.24999,37.51332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.76608,37.53416,65.70137,37.53693), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.70137,37.53693,65.76608,37.53416), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.32276,37.54137,65.70137,37.53693), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.80582,37.56165,59.17165,37.56304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.17165,37.56304,54.80582,37.56165), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.78552,37.56888,59.17165,37.56304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.45165,37.64137,58.49804,37.64777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.49804,37.64777,58.45165,37.64137), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.77859,37.66193,66.5478,37.6639), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5478,37.6639,58.77859,37.66193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.92609,37.66998,66.5478,37.6639), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.22387,37.68471,58.92609,37.66998), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.54443,37.7036,58.22387,37.68471), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.83305,37.74638,66.5383,37.7728), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5383,37.7728,58.19804,37.78638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.19804,37.78638,66.5383,37.7728), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.12443,37.79999,58.19804,37.78638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5578,37.8239,58.12443,37.79999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.77832,37.90776,57.50526,37.92999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.50526,37.92999,66.6664,37.9367), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.6664,37.9367,57.50526,37.92999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.80582,37.95388,66.6664,37.9367), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.6703,37.9733,53.80582,37.95388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.34832,37.99887,55.2961,38.00166), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.2961,38.00166,66.6439,38.0031), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.6439,38.0031,55.2961,38.00166), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.5556,38.0383,66.4239,38.0436), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.4239,38.0436,66.5556,38.0383), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.98276,38.07249,56.32665,38.08415), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.32665,38.08415,66.3194,38.0847), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.3194,38.0847,56.32665,38.08415), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.44276,38.0861,66.3194,38.0847), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.37109,38.09304,55.44276,38.0861), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.7986,38.12248,56.35277,38.13276), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.35277,38.13276,55.7986,38.12248), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.2536,38.155,56.31888,38.17332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.31888,38.17332,66.1628,38.1739), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.1628,38.1739,56.31888,38.17332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.05082,38.1936,66.1628,38.1739), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.7486,38.2264,56.38554,38.23137), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.38554,38.23137,66.0717,38.2361), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.85721,38.23137,66.0717,38.2361), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((66.0717,38.2361,56.38554,38.23137), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.99,38.2428,66.0717,38.2361), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.5972,38.2539,65.99,38.2428), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.53888,38.2661,56.69526,38.26693), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.69526,38.26693,56.53888,38.2661), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.8403,38.2736,57.23915,38.27387), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.23915,38.27387,65.8403,38.2736), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.17609,38.28027,65.8953,38.2808), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.8953,38.2808,57.17609,38.28027), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.75638,38.28499,65.8953,38.2808), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((65.2922,38.4108,53.83971,38.43694), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.83971,38.43694,65.2922,38.4108), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.82443,38.52138,53.83971,38.43694), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.9872,38.6267,53.85443,38.66637), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.85443,38.66637,64.9872,38.6267), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.6653,38.7419,53.85443,38.66637), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.4972,38.8542,53.97943,38.89777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.97943,38.89777,53.96555,38.93916), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.96555,38.93916,64.2147,38.9536), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.2147,38.9536,64.1739,38.955), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.1739,38.955,64.2147,38.9536), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((64.3403,38.9911,53.88165,39.00361), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.88165,39.00361,64.3403,38.9911), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.76471,39.02721,53.85249,39.03638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.85249,39.03638,53.76471,39.02721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.7311,39.07777,53.68193,39.07915), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.68193,39.07915,53.7311,39.07777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.63999,39.12444,53.68193,39.07915), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.65721,39.20082,63.7128,39.2061), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.7128,39.2061,53.1811,39.20999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.1811,39.20999,63.7128,39.2061), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.71749,39.23999,53.16888,39.26027), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.16888,39.26027,53.71749,39.23999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.44554,39.31443,53.57193,39.32526), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.57193,39.32526,53.44554,39.31443), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.15221,39.34026,53.29332,39.34637), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.29332,39.34637,53.15221,39.34026), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.5264,39.3936,53.08443,39.40193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.08443,39.40193,63.5264,39.3936), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.56999,39.46387,53.63416,39.48832), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.63416,39.48832,53.56999,39.46387), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.22777,39.51527,53.73444,39.52388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.73444,39.52388,53.58054,39.52554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.58054,39.52554,53.73444,39.52388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.4236,39.53526,53.58054,39.52554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.21749,39.54971,53.4236,39.53526), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.63304,39.56443,53.21749,39.54971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.65471,39.59499,53.61555,39.61721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.61555,39.61721,53.24721,39.63194), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.24721,39.63194,63.0386,39.6444), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((63.0386,39.6444,53.24721,39.63194), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.31194,39.65777,53.45776,39.66805), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.45776,39.66805,53.40694,39.67082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.40694,39.67082,53.54638,39.67277), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.54638,39.67277,53.40694,39.67082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.42221,39.7236,53.02082,39.77277), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.02082,39.77277,62.8194,39.7844), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.8194,39.7844,53.02082,39.77277), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.5844,39.9097,53.28054,39.9386), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.28054,39.9386,53.57582,39.9661), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.57582,39.9661,52.99055,39.97221), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.99055,39.97221,53.57582,39.9661), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.89832,39.99471,53.1586,39.9961), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.1586,39.9961,52.89832,39.99471), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.39693,40.00166,53.1586,39.9961), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.92055,40.01193,53.39693,40.00166), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.4411,40.0311,52.92055,40.01193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.73749,40.05638,62.4411,40.0311), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.4181,40.1047,52.73749,40.05638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.76721,40.16055,62.4408,40.1839), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.4408,40.1839,52.76721,40.16055), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.4067,40.2292,52.69027,40.27193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.69027,40.27193,62.4067,40.2292), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.3939,40.3242,52.7086,40.34193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.7086,40.34193,62.3939,40.3242), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.3533,40.3725,52.7086,40.34193), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.3553,40.4203,62.3533,40.3725), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.275,40.4692,62.2014,40.4861), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.2014,40.4861,62.275,40.4692), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.1231,40.5842,53.75304,40.6161), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.75304,40.6161,53.7086,40.63499), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.7086,40.63499,53.75304,40.6161), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.0311,40.65388,62.0972,40.6539), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.0972,40.6539,54.0311,40.65388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.9361,40.66749,54.39749,40.66916), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.39749,40.66916,53.9361,40.66749), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.2236,40.67971,52.86832,40.68694), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.86832,40.68694,54.09527,40.69082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.09527,40.69082,52.86832,40.68694), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.05999,40.71304,52.8261,40.7161), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.8261,40.7161,54.42693,40.71915), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.42693,40.71915,54.31165,40.71999), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.31165,40.71999,54.42693,40.71915), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((62.0425,40.7236,54.23693,40.72582), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.23693,40.72582,62.0425,40.7236), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.4736,40.72887,54.23693,40.72582), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.24805,40.75971,53.18832,40.76276), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.18832,40.76276,53.24805,40.75971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.11166,40.76777,53.55777,40.77055), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.55777,40.77055,53.33415,40.77304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.33415,40.77304,53.55777,40.77055), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.37971,40.7761,53.33415,40.77304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.48693,40.79943,53.25665,40.81388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.25665,40.81388,54.44027,40.81416), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.44027,40.81416,53.25665,40.81388), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.3511,40.82471,53.38416,40.82638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.38416,40.82638,53.3511,40.82471), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.87693,40.82638,53.3511,40.82471), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.06138,40.83388,53.38416,40.82638), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.55666,40.84832,61.9958,40.8492), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.9958,40.8492,53.55666,40.84832), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.26832,40.85194,61.9958,40.8492), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.47471,40.85666,53.65721,40.85721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.65721,40.85721,54.47471,40.85666), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.06221,40.87777,54.7111,40.87832), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.7111,40.87832,53.06221,40.87777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.59666,40.89443,54.7111,40.87832), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.31749,40.92666,53.59666,40.89443), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.75999,40.97332,61.975,40.9997), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.975,40.9997,52.95832,41.0061), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.95832,41.0061,61.975,40.9997), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.7661,41.0386,52.96582,41.05027), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.96582,41.05027,54.7661,41.0386), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.67582,41.06805,52.88332,41.07304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.88332,41.07304,52.84721,41.07443), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.84721,41.07443,52.88332,41.07304), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.9186,41.08166,52.84721,41.07443), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.9111,41.10888,61.8933,41.1117), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.8933,41.1117,52.9111,41.10888), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.2733,41.1614,61.3553,41.1872), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.3553,41.1872,54.59415,41.18888), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.59415,41.18888,61.3553,41.1872), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.1672,41.1989,61.0017,41.2083), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.0017,41.2083,61.1672,41.1989), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.4883,41.2194,61.0469,41.2214), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.0469,41.2214,60.4883,41.2194), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.1419,41.2319,61.38532,41.23426), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.38532,41.23426,61.1419,41.2319), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.0858,41.2372,61.38532,41.23426), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.0331,41.2458,60.9558,41.2486), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.9558,41.2486,61.0331,41.2458), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.8119,41.2578,60.705,41.2581), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.705,41.2581,60.8119,41.2578), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.5375,41.2622,57.05,41.2628), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.05,41.2628,55.5375,41.2622), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.6525,41.2672,61.6114,41.2675), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.6114,41.2675,55.6525,41.2672), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.4569,41.2867,56.6669,41.2881), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.6669,41.2881,55.4569,41.2867), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.8122,41.29,56.6669,41.2881), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.4033,41.2922,55.8122,41.29), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.7289,41.2922,55.8122,41.29), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.8275,41.2994,61.4247,41.3025), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((61.4247,41.3025,56.8275,41.2994), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.3386,41.3086,61.4247,41.3025), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.0922,41.3219,55.99845,41.32547), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.99845,41.32547,57.0922,41.3219), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.78638,41.3336,55.99845,41.32547), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.9039,41.3336,55.99845,41.32547), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.82138,41.34444,52.78638,41.3336), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.1883,41.3711,55.3947,41.3872), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.3947,41.3872,57.0958,41.4014), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.0958,41.4014,52.82332,41.40721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.82332,41.40721,60.0911,41.4103), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0911,41.4103,52.82332,41.40721), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.2892,41.4397,60.0769,41.4425), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0769,41.4425,55.2892,41.4397), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.0428,41.4669,55.2433,41.4847), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.2433,41.4847,54.06471,41.4886), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.06471,41.4886,55.2433,41.4847), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.55666,41.51554,60.0994,41.5375), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0994,41.5375,52.55666,41.51554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.0358,41.575,52.58833,41.58777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.58833,41.58777,60.1864,41.5944), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.1864,41.5944,52.58833,41.58777), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.58332,41.65332,55.1208,41.6581), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((55.1208,41.6581,52.91554,41.65971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.91554,41.65971,55.1208,41.6581), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.77277,41.65971,55.1208,41.6581), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.9761,41.6647,52.91554,41.65971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.87916,41.69888,52.7761,41.71971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.7761,41.71971,60.0803,41.7242), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0803,41.7242,52.7761,41.71971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.44332,41.7445,56.9947,41.7497), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.44332,41.7445,56.9947,41.7497), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.9947,41.7497,52.44332,41.7445), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0697,41.7578,56.9947,41.7497), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.2569,41.7725,60.0697,41.7578), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.275,41.7978,54.9717,41.8022), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.9717,41.8022,53.98888,41.80332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.98888,41.80332,54.9717,41.8022), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.1225,41.805,53.98888,41.80332), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.2561,41.8228,54.9567,41.8292), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.9567,41.8292,60.2561,41.8228), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.9694,41.8433,54.9567,41.8292), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.1778,41.8622,56.9694,41.8433), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((56.9783,41.885,53.89638,41.89971), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.89638,41.89971,54.9567,41.9125), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.9567,41.9125,60.1228,41.9217), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.1228,41.9217,54.9567,41.9125), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.1197,41.9417,60.1228,41.9217), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9342,41.9622,59.9256,41.9803), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9256,41.9803,59.9372,41.9947), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9372,41.9947,53.91666,42.00166), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.91666,42.00166,59.9372,41.9947), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0247,42.015,53.91666,42.00166), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.7611,42.0589,53.07888,42.0636), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.07888,42.0636,54.7611,42.0589), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.27805,42.07054,53.89249,42.07526), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.89249,42.07526,53.27805,42.07054), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.2458,42.0953,53.15083,42.09554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.15083,42.09554,57.2458,42.0953), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((52.9281,42.0967,53.15083,42.09554), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.80388,42.12388,59.9922,42.1319), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9922,42.1319,53.015,42.1389), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.015,42.1389,53.63082,42.14082), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.63082,42.14082,53.015,42.1389), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.6644,42.1536,57.3869,42.1625), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.3869,42.1625,57.6644,42.1536), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.4739,42.1753,57.7522,42.1842), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.7522,42.1842,60.0644,42.1883), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((60.0644,42.1883,57.8692,42.1906), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.8692,42.1906,60.0644,42.1883), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.875,42.2064,57.8692,42.1906), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.2989,42.2297,54.3653,42.2353), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.3653,42.2353,57.8478,42.2378), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.8478,42.2378,54.3653,42.2353), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9567,42.2433,57.8478,42.2378), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.9047,42.2606,53.4628,42.2714), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.4628,42.2714,57.9047,42.2606), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.6631,42.2892,59.4572,42.2928), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.4572,42.2928,53.6631,42.2892), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.4119,42.2967,59.9161,42.2978), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.9161,42.2978,58.4119,42.2967), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.5106,42.3011,59.9161,42.2978), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.8439,42.315,58.5142,42.3181), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.5142,42.3181,59.8439,42.315), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.7358,42.3225,58.5142,42.3181), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((54.1986,42.3325,59.3714,42.3381), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.3714,42.3381,54.1986,42.3325), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.2847,42.3478,53.9956,42.3486), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((53.9956,42.3486,59.2847,42.3478), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.9636,42.3672,59.2639,42.3722), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.2639,42.3722,57.9636,42.3672), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.4469,42.3778,59.2639,42.3722), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.4114,42.3894,58.4469,42.3778), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.3381,42.4414,57.9239,42.4419), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.9239,42.4419,58.3381,42.4414), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((57.97,42.45,59.2586,42.4517), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.2586,42.4517,57.97,42.45), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.1467,42.4622,59.2586,42.4517), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.2256,42.4761,58.1467,42.4622), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.1017,42.5039,58.0336,42.5053), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.0336,42.5053,58.1017,42.5039), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.0472,42.5239,58.9517,42.5408), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.9517,42.5408,59.1558,42.5411), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((59.1558,42.5411,58.9517,42.5408), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.315,42.5483,59.1558,42.5411), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.1642,42.6011,58.1431,42.6308), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.1431,42.6308,58.8019,42.6394), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.8019,42.6394,58.1431,42.6308), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.1644,42.6519,58.4225,42.6608), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.4225,42.6608,58.5628,42.6625), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.5628,42.6625,58.4225,42.6608), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.2939,42.6975,58.5628,42.6625), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.5978,42.7842,58.6175,42.7975), mapfile, tile_dir, 0, 11, "tm-turkmenistan")
	render_tiles((58.6175,42.7975,58.5978,42.7842), mapfile, tile_dir, 0, 11, "tm-turkmenistan")