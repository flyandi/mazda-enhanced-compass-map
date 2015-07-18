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
    # Region: South Dakota
    # Region Name: SD

	render_tiles((-96.50132,42.48275,-96.44551,42.49063), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.44551,42.49063,-96.50132,42.48275), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.61149,42.50609,-96.47745,42.50959), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.47745,42.50959,-96.52514,42.51023), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52514,42.51023,-96.47745,42.50959), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.49297,42.51728,-96.52514,42.51023), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.62795,42.5271,-96.49297,42.51728), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.47695,42.55608,-96.48002,42.56133), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.48002,42.56133,-96.65875,42.56643), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.65875,42.56643,-96.48002,42.56133), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.7093,42.60375,-96.65875,42.56643), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52677,42.64118,-96.69764,42.65914), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.69764,42.65914,-96.77818,42.66299), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.77818,42.66299,-96.69764,42.65914), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.5916,42.68808,-96.80165,42.69877), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.80165,42.69877,-96.80737,42.70068), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.80737,42.70068,-96.80165,42.69877), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.96568,42.72453,-96.6247,42.7255), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.6247,42.7255,-96.96568,42.72453), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.9068,42.7338,-96.6247,42.7255), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.01563,42.75653,-97.02485,42.76243), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.02485,42.76243,-98.03503,42.76421), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.03503,42.76421,-97.02485,42.76243), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.95015,42.76962,-97.13133,42.77193), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.13133,42.77193,-97.95015,42.76962), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.62188,42.77926,-97.13133,42.77193), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.16507,42.79162,-97.905,42.79887), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.905,42.79887,-97.16507,42.79162), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.1047,42.80848,-97.905,42.79887), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.21396,42.82014,-96.57794,42.82765), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.57794,42.82765,-97.21396,42.82014), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.14806,42.84001,-98.15259,42.84115), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.15259,42.84115,-98.14806,42.84001), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.70103,42.8438,-97.45218,42.84605), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.45218,42.84605,-97.70103,42.8438), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.48492,42.85,-97.63544,42.85181), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.63544,42.85181,-97.87689,42.85266), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.87689,42.85266,-97.23787,42.85314), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.23787,42.85314,-97.87689,42.85266), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.51595,42.85375,-97.23787,42.85314), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.34118,42.85588,-97.59926,42.85623), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.59926,42.85623,-97.34118,42.85588), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.80134,42.858,-97.59926,42.85623), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.23192,42.86114,-97.80134,42.858), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.85796,42.86509,-97.30208,42.86566), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.30208,42.86566,-97.41707,42.86592), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.41707,42.86592,-97.30208,42.86566), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.28001,42.875,-96.53785,42.87848), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.53785,42.87848,-98.28001,42.875), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.30819,42.88649,-96.53785,42.87848), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.54047,42.9086,-98.38645,42.91841), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.38645,42.91841,-96.54169,42.92258), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.54169,42.92258,-98.38645,42.91841), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.4345,42.92923,-96.54169,42.92258), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.50031,42.95939,-98.47892,42.96354), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.47892,42.96354,-96.50031,42.95939), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52025,42.97764,-98.47892,42.96354), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.00043,42.99753,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.22811,42.99787,-100.19841,42.99798), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.19841,42.99798,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.19841,42.99798,-101.22811,42.99787), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.85004,42.99817,-99.53406,42.9982), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.53406,42.9982,-99.25446,42.99822), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.25446,42.99822,-99.53406,42.9982), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.84799,42.99826,-99.25446,42.99822), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.49855,42.99856,-98.84799,42.99826), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.08249,42.99914,-102.40864,42.99963), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.08255,42.99914,-102.40864,42.99963), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.40864,42.99963,-102.79211,43.00004), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.79211,43.00004,-103.0009,43.00026), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.0009,43.00026,-102.79211,43.00004), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05313,43.00059,-103.5051,43.00076), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.5051,43.00076,-103.47613,43.00077), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.47613,43.00077,-103.5051,43.00076), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.49269,43.00509,-103.47613,43.00077), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.51161,43.03993,-96.4582,43.06755), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.4582,43.06755,-96.4521,43.08255), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.4521,43.08255,-96.4582,43.06755), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.43934,43.11392,-96.45885,43.14336), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45885,43.14336,-96.43934,43.11392), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52208,43.22096,-96.47557,43.22105), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.47557,43.22105,-96.52208,43.22096), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.55296,43.24728,-96.55903,43.25756), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.55903,43.25756,-96.55296,43.24728), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05388,43.2898,-96.57882,43.2911), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.57882,43.2911,-104.05388,43.2898), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.53039,43.30003,-96.57882,43.2911), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52429,43.34721,-96.52157,43.38564), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52157,43.38564,-96.52429,43.34721), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.59425,43.43415,-96.5846,43.46961), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.5846,43.46961,-104.05468,43.47782), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05468,43.47782,-96.5846,43.46961), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45326,43.50039,-96.59893,43.50046), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.59893,43.50046,-96.45326,43.50039), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05479,43.50333,-96.59893,43.50046), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45332,43.5523,-104.05503,43.5586), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05503,43.5586,-96.45332,43.5523), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45291,43.84951,-104.05549,43.85348), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45291,43.84951,-104.05549,43.85348), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05549,43.85348,-96.45291,43.84951), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05549,43.85348,-96.45291,43.84951), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05542,44.14108,-104.05541,44.18038), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05541,44.18038,-96.45244,44.19678), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45244,44.19678,-96.45244,44.1968), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45244,44.1968,-96.45244,44.19678), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05539,44.24998,-96.45244,44.1968), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45221,44.36015,-104.05539,44.24998), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45329,44.54364,-104.0557,44.57099), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.0557,44.57099,-96.45329,44.54364), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45381,44.63134,-104.05581,44.69134), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.05581,44.69134,-96.45381,44.63134), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45483,44.80555,-104.05581,44.69134), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45584,44.97735,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.0577,44.99743,-104.03914,44.99852), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.0577,44.99743,-104.03914,44.99852), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.03914,44.99852,-104.0577,44.99743), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.03998,45.12499,-104.04014,45.21289), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04014,45.21289,-96.45755,45.2689), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45755,45.2689,-96.45778,45.30761), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.45778,45.30761,-96.47008,45.3268), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.47008,45.3268,-104.04036,45.33595), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04036,45.33595,-96.47008,45.3268), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.48256,45.34627,-104.04036,45.33595), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.52179,45.37565,-96.56214,45.38609), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.56214,45.38609,-96.52179,45.37565), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.61773,45.40809,-96.67545,45.41022), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.67545,45.41022,-96.61773,45.40809), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.71079,45.43693,-96.67545,45.41022), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.74251,45.47872,-104.04176,45.49079), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04176,45.49079,-96.74251,45.47872), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.78104,45.53597,-104.04194,45.55792), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04194,45.55792,-96.78104,45.53597), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.83542,45.58613,-96.84396,45.594), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.84396,45.594,-96.83542,45.58613), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.85162,45.61941,-96.84396,45.594), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.82616,45.65416,-96.85162,45.61941), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.74509,45.70158,-96.67267,45.73234), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.67267,45.73234,-104.0426,45.75), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.0426,45.75,-96.67267,45.73234), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.63051,45.78116,-104.0426,45.75), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.58709,45.81645,-96.63051,45.78116), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04378,45.86471,-96.57187,45.87185), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.57187,45.87185,-104.04378,45.86471), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04413,45.88198,-96.57187,45.87185), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-96.56367,45.93525,-97.5426,45.93526), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.5426,45.93526,-96.56367,45.93525), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.77704,45.93539,-97.5426,45.93526), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.22829,45.93566,-97.08209,45.93584), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.08209,45.93584,-97.97878,45.93593), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-97.97878,45.93593,-98.0081,45.93601), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.0081,45.93601,-97.97878,45.93593), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.07052,45.93618,-98.0081,45.93601), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.41452,45.9365,-98.07052,45.93618), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.62538,45.93823,-98.72437,45.93867), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-98.72437,45.93867,-98.62538,45.93823), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.00564,45.93994,-99.09287,45.94018), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.00575,45.93994,-99.09287,45.94018), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.09287,45.94018,-99.34496,45.9403), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.34496,45.9403,-99.49025,45.94036), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.49025,45.94036,-99.34496,45.9403), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.71807,45.94091,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.71807,45.94091,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.61116,45.9411,-99.71807,45.94091), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.88006,45.94167,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-99.88029,45.94167,-99.61116,45.9411), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.15208,45.94249,-100.29413,45.94327), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.29413,45.94327,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.49935,45.94363,-100.51179,45.94365), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.51179,45.94365,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.51195,45.94365,-100.49935,45.94363), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-100.76211,45.94377,-100.51179,45.94365), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.10683,45.94398,-101.36528,45.94409), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.36528,45.94409,-101.55728,45.9441), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.55728,45.9441,-101.36528,45.94409), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.79461,45.9444,-102.00068,45.94454), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.00068,45.94454,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-101.99862,45.94454,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.08756,45.9446,-102.00068,45.94454), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.32823,45.94481,-102.08756,45.9446), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.55095,45.94502,-102.88025,45.94507), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.88025,45.94507,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.70487,45.94507,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.94207,45.94509,-102.88025,45.94507), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-102.99567,45.94512,-102.94207,45.94509), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.2184,45.94521,-103.66078,45.94524), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.66078,45.94524,-103.2184,45.94521), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-103.43485,45.94529,-104.04544,45.94531), mapfile, tile_dir, 0, 11, "south dakota-sd")
	render_tiles((-104.04544,45.94531,-103.43485,45.94529), mapfile, tile_dir, 0, 11, "south dakota-sd")