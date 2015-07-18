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
    # Zone: us
    # Region: Idaho
    # Region Name: ID

	render_tiles((-113.81796,41.98858,-113.49655,41.99331), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.49655,41.99331,-114.04172,41.99372), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.04172,41.99372,-113.49655,41.99331), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.28186,41.99421,-114.59827,41.99451), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.59827,41.99451,-114.28186,41.99421), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.31388,41.9961,-113.24916,41.9962), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.24916,41.9962,-115.31388,41.9961), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.87018,41.99677,-116.33276,41.99728), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.33276,41.99728,-116.62595,41.99738), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.62595,41.99738,-115.62591,41.99742), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.62591,41.99742,-116.62595,41.99738), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.10951,41.9976,-115.62591,41.99742), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.10953,41.9976,-115.62591,41.99742), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.00082,41.99822,-115.03825,41.99863), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.00082,41.99822,-115.03825,41.99863), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.03825,41.99863,-112.16464,41.9988), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.16464,41.9988,-115.03825,41.99863), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.75078,41.99933,-111.50781,41.99969), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.50781,41.99969,-111.47138,41.99974), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.50785,41.99969,-111.47138,41.99974), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.47138,41.99974,-111.50781,41.99969), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.01829,41.99984,-117.0262,41.99989), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.0262,41.99989,-114.89921,41.99991), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.89921,41.99991,-117.0262,41.99989), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.64802,42.00031,-114.89921,41.99991), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.37413,42.00089,-112.26494,42.00099), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.26494,42.00099,-111.37413,42.00089), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04669,42.00157,-112.26494,42.00099), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04708,42.34942,-117.02655,42.37856), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02655,42.37856,-111.04708,42.34942), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04554,42.51311,-117.02655,42.37856), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04356,42.72262,-117.02625,42.80745), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02625,42.80745,-111.04356,42.72262), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04396,42.96445,-111.04405,43.02005), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04405,43.02005,-117.02665,43.02513), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02665,43.02513,-111.04405,43.02005), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04414,43.07236,-117.02665,43.02513), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04462,43.31572,-111.04536,43.50114), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04536,43.50114,-117.02689,43.59603), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02689,43.59603,-117.02566,43.68041), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02566,43.68041,-111.04611,43.68785), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04611,43.68785,-117.02566,43.68041), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.02358,43.82381,-116.98555,43.88118), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.98555,43.88118,-116.97602,43.89555), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.97602,43.89555,-111.04652,43.90838), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04652,43.90838,-116.97602,43.89555), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.95987,43.98293,-111.04722,43.98345), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04722,43.98345,-116.95987,43.98293), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.93734,44.02938,-111.04722,43.98345), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.97735,44.08536,-111.04845,44.11483), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04845,44.11483,-116.97735,44.08536), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.89791,44.15262,-116.89593,44.1543), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.89593,44.1543,-116.89791,44.15262), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.90275,44.17947,-116.9655,44.19413), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.9655,44.19413,-116.90275,44.17947), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.97196,44.23568,-117.05935,44.23724), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.05935,44.23724,-116.97196,44.23568), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.17034,44.25889,-117.12104,44.27759), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.12104,44.27759,-117.21697,44.28836), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.21697,44.28836,-117.21201,44.29643), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.21201,44.29643,-117.21697,44.28836), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.1922,44.32863,-117.21691,44.36016), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.21691,44.36016,-111.04915,44.37493), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04915,44.37493,-112.88177,44.38032), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.88177,44.38032,-111.04915,44.37493), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.24303,44.39097,-112.88177,44.38032), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.24303,44.39097,-112.88177,44.38032), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.82669,44.40527,-112.8219,44.40744), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.8219,44.40744,-112.82669,44.40527), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.95115,44.4167,-112.8219,44.40744), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.21507,44.42716,-112.95115,44.4167), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.82819,44.44247,-112.38739,44.44806), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.38739,44.44806,-112.82819,44.44247), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.00685,44.47172,-111.04897,44.47407), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.04897,44.47407,-113.00685,44.47172), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.22593,44.47939,-112.47321,44.48003), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.47321,44.48003,-117.22593,44.47939), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.60186,44.49102,-111.12265,44.49366), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.12265,44.49366,-112.60186,44.49102), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.73508,44.49916,-112.70782,44.50302), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.70782,44.50302,-112.73508,44.49916), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.80791,44.51172,-113.00683,44.51844), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.00683,44.51844,-117.16719,44.52343), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.16719,44.52343,-113.00683,44.51844), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.1251,44.52853,-112.35892,44.52885), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.35892,44.52885,-112.1251,44.52853), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.14356,44.53573,-112.03413,44.53772), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.03413,44.53772,-111.14356,44.53573), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.2217,44.54352,-112.03413,44.53772), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.56281,44.55521,-111.61712,44.55713), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.61712,44.55713,-117.14293,44.55724), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.14293,44.55724,-111.61712,44.55713), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.70422,44.56021,-117.14293,44.55724), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.8705,44.56403,-111.70422,44.56021), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-112.28619,44.56847,-111.8705,44.56403), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.20146,44.5757,-113.06107,44.57733), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.06107,44.57733,-111.20146,44.5757), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.51913,44.58292,-113.06107,44.57733), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.22416,44.6234,-113.04935,44.62938), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.04935,44.62938,-111.22416,44.6234), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.09497,44.65201,-111.26875,44.66828), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.26875,44.66828,-111.46883,44.67934), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.46883,44.67934,-111.26875,44.66828), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.45603,44.6969,-113.10115,44.70858), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.10115,44.70858,-111.45603,44.6969), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.43879,44.72055,-111.32367,44.72447), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.32367,44.72447,-117.06227,44.72714), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.06227,44.72714,-111.32367,44.72447), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.37779,44.75152,-111.38501,44.75513), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-111.38501,44.75513,-117.0138,44.75684), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.0138,44.75684,-111.38501,44.75513), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.13139,44.76474,-117.0138,44.75684), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.9318,44.78718,-113.30151,44.79899), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.30151,44.79899,-116.9318,44.78718), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.24717,44.82295,-113.37715,44.83486), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.37715,44.83486,-116.8893,44.84052), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.8893,44.84052,-113.42238,44.8426), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.42238,44.8426,-116.8893,44.84052), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.86534,44.8706,-113.42238,44.8426), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.47457,44.91085,-116.83363,44.92898), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.83363,44.92898,-113.47457,44.91085), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.44896,44.95354,-116.83363,44.92898), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.85831,44.97876,-113.44896,44.95354), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.43773,45.00697,-116.84131,45.03091), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.84131,45.03091,-113.43773,45.00697), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.45197,45.05925,-116.78371,45.07697), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.78371,45.07697,-116.78279,45.07815), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.78279,45.07815,-116.78371,45.07697), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.51082,45.0999,-116.75464,45.11397), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.75464,45.11397,-113.51082,45.0999), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.57467,45.12841,-116.75464,45.11397), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.65006,45.23471,-116.69605,45.25468), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.69605,45.25468,-116.69083,45.26922), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.69083,45.26922,-116.69605,45.25468), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.67465,45.31434,-113.7356,45.32527), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.7356,45.32527,-116.67465,45.31434), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.73239,45.38506,-113.76337,45.42773), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.76337,45.42773,-116.5882,45.44292), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.5882,45.44292,-113.76337,45.42773), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.27922,45.48062,-113.75999,45.48074), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.75999,45.48074,-114.27922,45.48062), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.36852,45.49272,-113.75999,45.48074), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.80285,45.52316,-114.25184,45.53781), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.25184,45.53781,-114.45676,45.54398), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.45676,45.54398,-114.18647,45.54554), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.18647,45.54554,-114.45676,45.54398), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.50634,45.55922,-116.50276,45.56661), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.50276,45.56661,-114.50634,45.55922), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.52369,45.5852,-113.80673,45.60215), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.80673,45.60215,-114.08315,45.604), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.08315,45.604,-113.80673,45.60215), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.53813,45.60683,-114.08315,45.604), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.4635,45.61579,-113.8614,45.62366), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.8614,45.62366,-116.4635,45.61579), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.89888,45.64417,-114.53577,45.65061), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.53577,45.65061,-114.01497,45.65401), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.01497,45.65401,-114.53577,45.65061), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.49964,45.66904,-116.52827,45.68147), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.52827,45.68147,-113.94825,45.68252), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.94825,45.68252,-116.52827,45.68147), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.01563,45.69613,-113.97157,45.70064), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-113.97157,45.70064,-114.01563,45.69613), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.50487,45.72218,-116.5357,45.73423), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.5357,45.73423,-114.50487,45.72218), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.593,45.77854,-114.56251,45.77993), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.56251,45.77993,-116.593,45.77854), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.66534,45.782,-114.56251,45.77993), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.73627,45.82618,-114.51714,45.83599), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.51714,45.83599,-116.78752,45.8402), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.78752,45.8402,-114.51714,45.83599), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.7992,45.85105,-114.42296,45.85538), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.42296,45.85538,-116.7992,45.85105), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.38824,45.88234,-116.8598,45.90726), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.8598,45.90726,-114.41317,45.91148), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.41317,45.91148,-116.8598,45.90726), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.88684,45.95862,-114.40226,45.96149), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.40226,45.96149,-116.88684,45.95862), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.44119,45.98845,-116.91599,45.99541), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.91599,45.99541,-114.44119,45.98845), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.48024,46.03033,-116.94266,46.061), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.94266,46.061,-116.98196,46.08492), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.98196,46.08492,-114.46005,46.0971), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.46005,46.0971,-116.98196,46.08492), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.5213,46.12529,-116.93547,46.14245), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.93547,46.14245,-114.5213,46.12529), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.51471,46.16773,-116.92396,46.17092), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.92396,46.17092,-114.44593,46.17393), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.44593,46.17393,-116.92396,46.17092), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.96297,46.19968,-114.44593,46.17393), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.44982,46.23712,-116.96438,46.25328), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.96438,46.25328,-114.44982,46.23712), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.44133,46.2738,-116.96438,46.25328), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.99726,46.30315,-114.43171,46.31074), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.43171,46.31074,-116.99726,46.30315), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.06275,46.35362,-114.42246,46.3871), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.42246,46.3871,-117.03555,46.41001), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03555,46.41001,-114.38476,46.41178), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.38476,46.41178,-117.03555,46.41001), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03661,46.42564,-114.38476,46.41178), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03977,46.47178,-114.40302,46.49868), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.40302,46.49868,-114.35166,46.50812), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.35166,46.50812,-114.40302,46.49868), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03978,46.54178,-114.35166,46.50812), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.33134,46.57778,-117.03978,46.54178), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.54732,46.64449,-114.32067,46.64696), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.32067,46.64696,-114.45324,46.64927), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.45324,46.64927,-114.32067,46.64696), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.59121,46.65257,-114.33587,46.65535), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.33587,46.65535,-114.59121,46.65257), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.62148,46.65814,-114.33587,46.65535), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.36071,46.66906,-114.62148,46.65814), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.6267,46.71289,-114.67689,46.73186), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.67689,46.73186,-114.76718,46.73883), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.76718,46.73883,-114.69901,46.74022), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.69901,46.74022,-114.76718,46.73883), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.79004,46.77873,-114.88059,46.81179), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.88059,46.81179,-114.79004,46.77873), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.94328,46.86797,-114.92743,46.91419), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.92743,46.91419,-114.96142,46.93289), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-114.96142,46.93289,-114.92743,46.91419), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.03165,46.97155,-114.96142,46.93289), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.07125,47.02208,-115.12092,47.06124), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.12092,47.06124,-115.07125,47.02208), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03983,47.12726,-115.18945,47.13103), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.18945,47.13103,-117.03983,47.12726), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03984,47.15473,-115.25579,47.17473), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.25579,47.17473,-117.03984,47.15473), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.29211,47.20986,-115.25579,47.17473), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.3269,47.25591,-117.04016,47.25927), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04016,47.25927,-115.3269,47.25591), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.37183,47.26521,-117.04016,47.25927), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.47096,47.28487,-115.37183,47.26521), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.53197,47.31412,-115.47096,47.28487), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04049,47.3661,-115.57862,47.36701), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.57862,47.36701,-117.04049,47.3661), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.71034,47.41778,-115.69293,47.45724), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.69293,47.45724,-115.63468,47.48176), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.63468,47.48176,-115.69293,47.45724), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.71702,47.53269,-115.72121,47.57632), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.72121,47.57632,-115.71702,47.53269), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.69428,47.62346,-115.73627,47.65476), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.73627,47.65476,-115.69428,47.62346), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.72377,47.69667,-117.04163,47.7353), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04163,47.7353,-115.83537,47.76096), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.83537,47.76096,-117.04163,47.7353), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.84547,47.81497,-115.90093,47.84306), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.90093,47.84306,-115.84547,47.81497), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-115.95995,47.89814,-115.90093,47.84306), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.03075,47.97335,-117.04131,47.97739), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04131,47.97739,-116.03075,47.97335), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.03838,47.98437,-117.04131,47.97739), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04915,47.99992,-116.03838,47.98437), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04121,48.04556,-116.04915,47.99992), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.04111,48.1249,-116.04891,48.12493), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04891,48.12493,-117.04111,48.1249), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04893,48.21584,-116.04891,48.12493), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04895,48.30985,-116.04893,48.21584), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03529,48.42273,-116.04916,48.48125), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04916,48.48125,-116.04916,48.50206), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04916,48.50206,-116.04916,48.48125), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03367,48.6569,-116.04916,48.50206), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03294,48.84656,-117.03235,48.99919), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-117.03235,48.99919,-116.75723,48.99994), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.75723,48.99994,-116.4175,49.0001), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.4175,49.0001,-116.75723,48.99994), mapfile, tile_dir, 0, 11, "idaho-id")
	render_tiles((-116.04919,49.00091,-116.4175,49.0001), mapfile, tile_dir, 0, 11, "idaho-id")