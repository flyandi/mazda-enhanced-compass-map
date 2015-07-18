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
    # Region: Maryland
    # Region Name: MD

	render_tiles((-75.98465,37.93812,-76.04653,37.95359), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.04653,37.95359,-75.98465,37.93812), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.98009,38.00489,-76.04621,38.02553), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.04621,38.02553,-76.00734,38.03671), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.04621,38.02553,-76.00734,38.03671), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.00734,38.03671,-76.04621,38.02553), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.89269,37.91685,-75.86073,37.91831), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.86073,37.91831,-75.89269,37.91685), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.66971,37.9508,-75.72266,37.97131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.72266,37.97131,-75.78382,37.97259), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.78382,37.97259,-75.72266,37.97131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.89896,37.97451,-75.78382,37.97259), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.62434,37.99421,-75.89896,37.97451), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.24227,38.02721,-76.32209,38.0365), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.32209,38.0365,-75.85751,38.03878), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.85751,38.03878,-76.32209,38.0365), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.85888,38.06014,-76.0059,38.07717), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.0059,38.07717,-76.37179,38.07957), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.37179,38.07957,-76.0059,38.07717), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.04869,38.08673,-76.37179,38.07957), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.1938,38.09601,-76.33079,38.09933), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.33079,38.09933,-75.86381,38.10097), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.86381,38.10097,-76.33079,38.09933), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.48104,38.11587,-76.43043,38.11938), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.43043,38.11938,-76.01192,38.12221), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.01192,38.12221,-75.93709,38.12421), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.93709,38.12421,-76.09555,38.12512), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.09555,38.12512,-75.93709,38.12421), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.17739,38.13001,-76.09555,38.12512), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.32014,38.13834,-75.17739,38.13001), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.54038,38.15299,-76.32014,38.13834), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.35352,38.17814,-75.94238,38.18707), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.94238,38.18707,-76.03199,38.18742), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.03199,38.18742,-75.94238,38.18707), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.08864,38.19265,-76.03199,38.18742), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.8641,38.20086,-76.08864,38.19265), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.59064,38.21421,-75.87545,38.21971), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.87545,38.21971,-75.14323,38.22048), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.14323,38.22048,-75.87545,38.21971), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.13551,38.23219,-76.67346,38.2344), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.67346,38.2344,-76.74006,38.23523), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.74006,38.23523,-76.67346,38.2344), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.39267,38.23966,-75.88851,38.24142), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.88851,38.24142,-76.39267,38.23966), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.92377,38.24629,-75.9445,38.24915), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.9445,38.24915,-75.92377,38.24629), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.80595,38.25228,-76.03894,38.25493), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.03894,38.25493,-76.80595,38.25228), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.82704,38.2583,-76.03894,38.25493), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.86429,38.26895,-76.82704,38.2583), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.37448,38.29635,-76.21761,38.30568), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.21761,38.30568,-76.92218,38.31134), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.92218,38.31134,-76.40289,38.3114), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.40289,38.3114,-76.92218,38.31134), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.10295,38.31153,-76.40289,38.3114), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.40019,38.31987,-75.08552,38.32427), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.08552,38.32427,-76.25767,38.32486), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.25767,38.32486,-75.08552,38.32427), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.97549,38.34733,-76.387,38.36127), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.387,38.36127,-76.25,38.3623), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.25,38.3623,-76.387,38.36127), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.21119,38.38066,-76.39338,38.38948), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.39338,38.38948,-77.21119,38.38066), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.28055,38.40314,-77.12333,38.41065), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.12333,38.41065,-76.28055,38.40314), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.00164,38.42195,-77.07549,38.42471), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.07549,38.42471,-77.00164,38.42195), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.25996,38.43582,-76.45094,38.44242), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.45094,38.44242,-77.01637,38.44557), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.01637,38.44557,-76.45094,38.44242), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.18546,38.45101,-75.04894,38.45126), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.04894,38.45126,-75.18546,38.45101), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.34129,38.45244,-75.04894,38.45126), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.47928,38.4537,-75.34129,38.45244), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.69372,38.46013,-75.47928,38.4537), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.4927,38.48285,-76.33636,38.49224), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.33636,38.49224,-76.4927,38.48285), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.24658,38.53834,-76.51751,38.53915), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.51751,38.53915,-77.24658,38.53834), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.27746,38.54185,-75.70038,38.54274), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.70038,38.54274,-76.27746,38.54185), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.70178,38.56077,-76.29004,38.56916), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.29004,38.56916,-75.70178,38.56077), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.18377,38.6007,-76.27959,38.60952), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.27959,38.60952,-76.16544,38.6102), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.16544,38.6102,-76.20307,38.61074), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.20307,38.61074,-76.16544,38.6102), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23119,38.61401,-77.12908,38.61436), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.12908,38.61436,-76.23119,38.61401), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.51128,38.61575,-77.12908,38.61436), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.1302,38.63502,-75.70755,38.63534), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.70755,38.63534,-75.70756,38.63539), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.70756,38.63539,-75.70755,38.63534), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.17016,38.64084,-75.70756,38.63539), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.52892,38.66389,-76.20033,38.67077), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.20033,38.67077,-76.17516,38.67324), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.17516,38.67324,-77.1325,38.67382), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.1325,38.67382,-76.17516,38.67324), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.32242,38.6793,-77.1325,38.67382), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.348,38.68623,-76.32242,38.6793), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.08578,38.70528,-77.0795,38.70952), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.0795,38.70952,-77.0532,38.70992), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.0532,38.70992,-77.0795,38.70952), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.27502,38.71271,-76.52709,38.71275), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.52709,38.71275,-76.27502,38.71271), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23873,38.71285,-76.52709,38.71275), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.52666,38.72443,-76.34054,38.73034), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.34054,38.73034,-76.52666,38.72443), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.041,38.73791,-76.34054,38.73034), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.04067,38.74669,-77.041,38.73791), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.55874,38.75635,-76.39035,38.757), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.39035,38.757,-76.55874,38.75635), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.03924,38.78534,-76.52698,38.78702), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.52698,38.78702,-76.37974,38.78831), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.37974,38.78831,-76.52698,38.78702), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.03901,38.79165,-76.37974,38.78831), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.31008,38.79685,-77.03901,38.79165), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.21933,38.81237,-76.31008,38.79685), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.19109,38.82966,-75.7231,38.82983), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.7231,38.82983,-76.19109,38.82966), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.9795,38.83781,-76.48988,38.83872), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.48988,38.83872,-76.9795,38.83781), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.3762,38.85046,-76.51694,38.85116), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.51694,38.85116,-76.27158,38.85177), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.27158,38.85177,-76.51694,38.85116), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.19687,38.85574,-76.27158,38.85177), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.33402,38.86024,-76.19687,38.85574), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.49068,38.88481,-76.20506,38.89273), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.20506,38.89273,-76.90939,38.89285), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.90939,38.89285,-76.20506,38.89273), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.47398,38.90269,-76.46938,38.90761), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.46938,38.90761,-76.31795,38.91131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.31795,38.91131,-76.46938,38.90761), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.25087,38.92825,-76.20364,38.92838), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.20364,38.92838,-76.25087,38.92825), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.11976,38.93434,-76.36173,38.93918), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.36173,38.93918,-76.45028,38.94111), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.45028,38.94111,-76.36173,38.93918), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.47128,38.95651,-77.1466,38.96421), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.1466,38.96421,-77.00255,38.96553), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.00255,38.96553,-77.1466,38.96421), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.2025,38.96791,-77.00255,38.96553), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.44898,38.98281,-77.2498,38.98591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.2498,38.98591,-76.44898,38.98281), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.04102,38.99555,-77.2498,38.98591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.3223,39.00638,-76.39408,39.01131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.39408,39.01131,-76.3223,39.00638), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23177,39.01852,-76.39408,39.01131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.2484,39.02689,-77.2484,39.02691), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.2484,39.02691,-77.2484,39.02689), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.26504,39.02855,-77.2484,39.02691), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.30185,39.03965,-76.42039,39.04207), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.42039,39.04207,-76.30185,39.03965), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.31071,39.05201,-77.33004,39.05595), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.33004,39.05595,-77.31071,39.05201), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.3597,39.062,-77.33004,39.05595), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.46262,39.07625,-76.42186,39.08144), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.42186,39.08144,-77.46262,39.07625), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23346,39.09139,-76.42186,39.08144), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.48128,39.10566,-76.24648,39.11959), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.24648,39.11959,-77.51993,39.12093), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.51993,39.12093,-76.24648,39.11959), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.42868,39.13171,-77.51993,39.12093), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.74815,39.14313,-76.27853,39.14576), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.27853,39.14576,-75.74815,39.14313), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.52122,39.16106,-76.27853,39.14576), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.52579,39.17791,-77.48597,39.18567), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.48597,39.18567,-76.52579,39.17791), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.50401,39.19929,-76.49838,39.20481), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.49838,39.20481,-76.42528,39.20571), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.42528,39.20571,-76.46348,39.20591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.46348,39.20591,-79.48687,39.20596), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.48687,39.20596,-76.46348,39.20591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.24317,39.21336,-77.45988,39.21868), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.45988,39.21868,-77.46007,39.21884), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.46007,39.21884,-77.45988,39.21868), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.42441,39.22817,-76.39551,39.2317), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.39551,39.2317,-79.42441,39.22817), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.75644,39.24669,-76.34999,39.24882), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.34999,39.24882,-75.75644,39.24669), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.49661,39.25105,-76.34999,39.24882), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.21125,39.26981,-76.32542,39.27291), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.32542,39.27291,-76.21125,39.26981), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.35375,39.27804,-77.55311,39.27927), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.55311,39.27927,-79.35375,39.27804), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.76044,39.29679,-76.1777,39.2987), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.1777,39.2987,-75.76044,39.29679), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.29661,39.30114,-77.58824,39.30196), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.58824,39.30196,-76.29661,39.30114), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.28372,39.30964,-77.66613,39.31701), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.66613,39.31701,-77.6777,39.31794), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.6777,39.31794,-77.66613,39.31701), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.71952,39.32131,-77.6777,39.31794), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.26239,39.32624,-77.71952,39.32131), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.15967,39.33591,-79.48437,39.3443), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.48437,39.3443,-76.15967,39.33591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.48437,39.3443,-76.15967,39.33591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.22416,39.35278,-77.74593,39.35322), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.74593,39.35322,-76.22416,39.35278), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.11053,39.37226,-75.7669,39.3775), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.7669,39.3775,-75.7669,39.37765), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.7669,39.37765,-75.7669,39.3775), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.0615,39.38775,-76.04096,39.39424), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.04096,39.39424,-76.0615,39.38775), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.1665,39.40089,-77.74001,39.40169), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.74001,39.40169,-79.1665,39.40089), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.14637,39.40531,-77.74001,39.40169), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.00688,39.41453,-76.14637,39.40531), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.95675,39.44026,-76.06093,39.45221), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.06093,39.45221,-76.03765,39.45264), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.03765,39.45264,-76.06093,39.45221), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.01231,39.45312,-76.03765,39.45264), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.09133,39.47241,-79.06783,39.4728), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.06783,39.4728,-79.09133,39.47241), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.03562,39.47334,-79.06783,39.4728), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.7982,39.47572,-79.03562,39.47334), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.94262,39.47961,-77.7982,39.47572), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.81094,39.50074,-78.94262,39.47961), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.82376,39.52591,-78.46095,39.52599), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.46095,39.52599,-77.82376,39.52591), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.46827,39.52622,-78.46095,39.52599), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.59065,39.53019,-79.48237,39.53169), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.48237,39.53169,-78.59065,39.53019), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.65504,39.54438,-78.85102,39.55404), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.85102,39.55404,-78.7071,39.55586), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.7071,39.55586,-78.85102,39.55404), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.43818,39.56352,-78.7071,39.55586), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.82981,39.58729,-78.00673,39.60134), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.00673,39.60134,-77.92599,39.60764), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.92599,39.60764,-78.73905,39.6097), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.73905,39.6097,-77.92599,39.60764), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.02763,39.62066,-78.38296,39.62225), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.38296,39.62225,-78.02763,39.62066), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.33279,39.62853,-78.31303,39.631), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.31303,39.631,-78.33279,39.62853), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.77114,39.63839,-78.31303,39.631), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.22508,39.65888,-78.08226,39.67117), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.08226,39.67117,-78.22508,39.65888), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.99106,39.72006,-76.99932,39.72007), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.99932,39.72007,-76.99106,39.72006), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.21702,39.72022,-77.23995,39.72023), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.23995,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.46915,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.46927,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.45943,39.72023,-77.21702,39.72022), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.7871,39.72105,-79.47666,39.72108), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.47666,39.72108,-76.7871,39.72105), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.71577,39.72139,-79.39246,39.72144), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.39246,39.72144,-76.56948,39.72146), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.56948,39.72146,-79.39246,39.72144), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.41898,39.72153,-77.76864,39.72154), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-77.76864,39.72154,-76.41898,39.72153), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23968,39.72164,-76.23349,39.72165), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23349,39.72165,-76.23968,39.72164), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.23328,39.72165,-76.23968,39.72164), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-76.1357,39.72177,-76.23349,39.72165), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.81208,39.72217,-75.7886,39.7222), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-75.7886,39.7222,-75.81208,39.72217), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.07586,39.72245,-78.09897,39.72247), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.09897,39.72247,-78.07586,39.72245), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.34259,39.72266,-78.38048,39.7227), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.34283,39.72266,-78.38048,39.7227), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.38048,39.7227,-78.34259,39.72266), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-79.04558,39.72293,-78.93118,39.723), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.93118,39.723,-79.04558,39.72293), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.8083,39.72307,-78.72358,39.72312), mapfile, tile_dir, 0, 11, "maryland-md")
	render_tiles((-78.72358,39.72312,-78.8083,39.72307), mapfile, tile_dir, 0, 11, "maryland-md")