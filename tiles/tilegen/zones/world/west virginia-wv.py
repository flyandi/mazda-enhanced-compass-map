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
    # Region: West Virginia
    # Region Name: WV

	render_tiles((-81.6786,37.20247,-81.56063,37.20666), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.56063,37.20666,-81.6786,37.20247), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.53307,37.22341,-81.2251,37.23487), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.2251,37.23487,-81.73906,37.2395), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.73906,37.2395,-81.744,37.24253), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.744,37.24253,-81.73906,37.2395), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.48356,37.2506,-81.744,37.24253), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.42795,37.27102,-81.77475,37.27485), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.77475,37.27485,-81.1126,37.2785), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.1126,37.2785,-81.77475,37.27485), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.84995,37.28523,-81.1126,37.2785), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.99601,37.29955,-80.98085,37.30085), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.98085,37.30085,-80.99601,37.29955), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.91926,37.30616,-80.98085,37.30085), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.896,37.33197,-80.83548,37.33482), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.83548,37.33482,-81.896,37.33197), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.36216,37.33769,-80.83548,37.33482), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.77008,37.37236,-80.88325,37.38393), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.88325,37.38393,-81.9336,37.38922), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.9336,37.38922,-80.88325,37.38393), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.66497,37.41422,-81.93695,37.41992), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.93695,37.41992,-80.86515,37.41993), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.86515,37.41993,-81.93695,37.41992), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.85815,37.42101,-80.85736,37.42113), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.85736,37.42113,-80.85815,37.42101), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.83645,37.42436,-80.46482,37.42614), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.46482,37.42614,-80.83645,37.42436), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.47128,37.43007,-80.46482,37.42614), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.98489,37.45432,-80.39988,37.46231), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.39988,37.46231,-81.98489,37.45432), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.54484,37.4747,-80.39988,37.46231), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.93228,37.51196,-80.29164,37.53651), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.29164,37.53651,-81.9683,37.5378), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.9683,37.5378,-80.29164,37.53651), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.06442,37.54452,-81.9683,37.5378), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.28244,37.58548,-82.14156,37.59517), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.14156,37.59517,-80.28244,37.58548), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.22339,37.62319,-80.2243,37.62399), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.2243,37.62399,-80.22339,37.62319), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.22611,37.65309,-80.2243,37.62399), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.29226,37.68373,-80.29003,37.68614), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.29003,37.68614,-82.29612,37.68617), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.29612,37.68617,-80.29003,37.68614), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.25814,37.72061,-82.32067,37.74597), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.32067,37.74597,-82.32736,37.76223), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.32736,37.76223,-82.32067,37.74597), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.21862,37.78329,-82.36997,37.80175), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.36997,37.80175,-80.21862,37.78329), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.19963,37.82751,-82.39846,37.84305), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.39846,37.84305,-80.19963,37.82751), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.41869,37.87238,-80.13193,37.8895), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.13193,37.8895,-82.41869,37.87238), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.48756,37.91698,-82.47942,37.93856), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.47942,37.93856,-80.05581,37.95188), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.05581,37.95188,-82.47942,37.93856), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.03624,37.96792,-82.46499,37.97686), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.46499,37.97686,-80.03624,37.96792), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.97123,38.04433,-82.54941,38.06306), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.54941,38.06306,-79.96198,38.06361), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.96198,38.06361,-82.54941,38.06306), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.93895,38.11162,-82.62618,38.13484), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.62618,38.13484,-79.93895,38.11162), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.91617,38.18439,-82.59886,38.20101), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.59886,38.20101,-79.91617,38.18439), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.59886,38.20101,-79.91617,38.18439), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.85032,38.23333,-82.58469,38.24051), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.58469,38.24051,-79.85032,38.23333), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.5818,38.24859,-82.58469,38.24051), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.79701,38.26727,-79.78754,38.2733), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.78754,38.2733,-79.79701,38.26727), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.80409,38.31392,-82.57188,38.31578), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.57188,38.31578,-79.80409,38.31392), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.59798,38.34491,-79.7346,38.35673), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.7346,38.35673,-82.59798,38.34491), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.59596,38.38089,-82.56066,38.40434), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.56066,38.40434,-82.50897,38.41464), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.50897,38.41464,-79.29776,38.41644), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.29776,38.41644,-82.50897,38.41464), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.3113,38.41845,-79.29776,38.41644), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.59367,38.42181,-79.3113,38.41845), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.44708,38.42698,-79.3703,38.42724), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.3703,38.42724,-82.44708,38.42698), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.68968,38.43144,-82.38177,38.43478), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.38177,38.43478,-79.68968,38.43144), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.324,38.44927,-79.47664,38.45723), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.47664,38.45723,-79.69109,38.46374), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.69109,38.46374,-79.47664,38.45723), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.23162,38.47404,-79.22826,38.48004), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.22826,38.48004,-79.23162,38.47404), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.30422,38.49631,-79.66913,38.51088), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.66913,38.51088,-82.30422,38.49631), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.20146,38.52782,-79.66913,38.51088), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.54257,38.55322,-82.29327,38.56028), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.29327,38.56028,-79.54257,38.55322), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.28213,38.57986,-79.64908,38.59152), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.64908,38.59152,-82.21897,38.59168), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.21897,38.59168,-79.64908,38.59152), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.27427,38.59368,-82.21897,38.59168), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.15436,38.60652,-82.17517,38.60848), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.17517,38.60848,-79.15436,38.60652), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.09296,38.65952,-82.18557,38.65958), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.18557,38.65958,-79.09296,38.65952), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.08806,38.69012,-82.18557,38.65958), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.20154,38.76037,-79.05725,38.76141), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.05725,38.76141,-82.20154,38.76037), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.86928,38.76299,-79.05725,38.76141), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.02305,38.79861,-82.20929,38.80267), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.20929,38.80267,-79.02305,38.79861), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.16157,38.82463,-78.82117,38.83098), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.82117,38.83098,-82.16157,38.82463), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.99901,38.84007,-78.82117,38.83098), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.77279,38.89374,-82.13477,38.90558), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.13477,38.90558,-78.77279,38.89374), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.68162,38.92584,-81.89847,38.9296), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.89847,38.9296,-78.68162,38.92584), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.82735,38.9459,-82.09887,38.96088), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.09887,38.96088,-81.82735,38.9459), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.08907,38.97598,-81.77573,38.98074), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.77573,38.98074,-78.62045,38.9826), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.62045,38.9826,-81.77573,38.98074), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.94183,38.9933,-78.62045,38.9826), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.56171,39.00901,-82.04156,39.01788), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.04156,39.01788,-78.56171,39.00901), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-82.00706,39.02958,-81.7933,39.04035), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.7933,39.04035,-82.00706,39.02958), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.53215,39.05294,-81.7933,39.04035), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.80786,39.08398,-78.50813,39.08863), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.50813,39.08863,-81.80786,39.08398), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.75027,39.10403,-81.74295,39.10658), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.74295,39.10658,-81.75027,39.10403), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.82816,39.13233,-77.8283,39.13242), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.8283,39.13242,-77.82816,39.13233), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.41394,39.15842,-77.80913,39.16857), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.80913,39.16857,-78.41394,39.15842), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.7523,39.18103,-81.75275,39.18468), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.75275,39.18468,-78.4287,39.18722), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.4287,39.18722,-81.75275,39.18468), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.48687,39.20596,-81.72147,39.21096), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.72147,39.21096,-79.48687,39.20596), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.71163,39.21923,-81.72147,39.21096), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.42441,39.22817,-77.77807,39.22931), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.77807,39.22931,-79.42441,39.22817), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.40498,39.23801,-77.77807,39.22931), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.03284,39.2644,-78.03319,39.26462), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.03319,39.26462,-78.03284,39.2644), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.03318,39.26462,-78.03284,39.2644), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.67833,39.27376,-81.6139,39.27534), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.6139,39.27534,-81.56525,39.27618), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.56525,39.27618,-78.40181,39.27675), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.40181,39.27675,-81.56525,39.27618), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.35375,39.27804,-78.40181,39.27675), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.28372,39.30964,-77.71952,39.32131), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.71952,39.32131,-79.26239,39.32624), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.26239,39.32624,-81.55965,39.33077), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.55965,39.33077,-79.26239,39.32624), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.48437,39.3443,-81.34757,39.34577), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.34757,39.34577,-79.48437,39.3443), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.37039,39.3487,-81.34757,39.34577), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.39379,39.35171,-77.74593,39.35322), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.74593,39.35322,-78.34048,39.35349), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.34048,39.35349,-77.74593,39.35322), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.18737,39.36399,-81.50319,39.37324), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.50319,39.37324,-78.18737,39.36399), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.24909,39.38999,-78.22913,39.39066), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.22913,39.39066,-81.24909,39.38999), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.41271,39.39462,-78.22913,39.39066), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.1665,39.40089,-77.74001,39.40169), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.74001,39.40169,-79.1665,39.40089), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.33713,39.40917,-81.45614,39.40927), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.45614,39.40927,-78.33713,39.40917), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.18595,39.43073,-78.95675,39.44026), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.95675,39.44026,-81.12853,39.44938), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.12853,39.44938,-81.12127,39.4577), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.12127,39.4577,-78.34709,39.46601), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.34709,39.46601,-79.09133,39.47241), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.09133,39.47241,-79.06783,39.4728), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.06783,39.4728,-79.09133,39.47241), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.03562,39.47334,-79.06783,39.4728), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.7982,39.47572,-79.03562,39.47334), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.94262,39.47961,-77.7982,39.47572), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.81094,39.50074,-81.07595,39.50966), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.07595,39.50966,-77.81094,39.50074), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.82376,39.52591,-78.46095,39.52599), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.46095,39.52599,-77.82376,39.52591), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.46827,39.52622,-78.46095,39.52599), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.59065,39.53019,-79.48237,39.53169), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.48237,39.53169,-78.59065,39.53019), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-81.03737,39.53806,-78.65504,39.54438), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.65504,39.54438,-81.03737,39.53806), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.85102,39.55404,-78.7071,39.55586), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.7071,39.55586,-78.85102,39.55404), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.43818,39.56352,-78.7071,39.55586), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.82981,39.58729,-78.00673,39.60134), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.00673,39.60134,-80.94378,39.60693), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.94378,39.60693,-77.92599,39.60764), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-77.92599,39.60764,-80.94378,39.60693), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.73905,39.6097,-77.92599,39.60764), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.02763,39.62066,-78.38296,39.62225), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.38296,39.62225,-78.02763,39.62066), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.33279,39.62853,-78.31303,39.631), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.31303,39.631,-78.33279,39.62853), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.77114,39.63839,-78.31303,39.631), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.22508,39.65888,-80.86558,39.66275), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.86558,39.66275,-78.22508,39.65888), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-78.08226,39.67117,-80.86558,39.66275), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.82976,39.71184,-80.83552,39.71925), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.83552,39.71925,-79.76377,39.72078), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.76377,39.72078,-79.91602,39.72106), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.91602,39.72106,-79.47666,39.72108), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-79.47666,39.72108,-79.91602,39.72106), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.42139,39.72119,-80.0417,39.72129), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.0417,39.72129,-80.07595,39.72135), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.07595,39.72135,-80.51934,39.7214), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51934,39.7214,-80.07595,39.72135), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.86993,39.76356,-80.82497,39.80109), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.82497,39.80109,-80.86993,39.76356), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.82428,39.84716,-80.82344,39.85003), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.82344,39.85003,-80.82428,39.84716), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.80339,39.91876,-80.76448,39.95025), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.76448,39.95025,-80.51916,39.9622), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51916,39.9622,-80.74013,39.97079), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.74013,39.97079,-80.51916,39.9622), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51912,40.01641,-80.73822,40.03354), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.73822,40.03354,-80.51912,40.01641), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.7368,40.08007,-80.73822,40.03354), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.70599,40.15159,-80.70267,40.15699), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.70267,40.15699,-80.51908,40.15967), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51908,40.15967,-80.70267,40.15699), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.68417,40.18702,-80.51908,40.15967), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.6446,40.25127,-80.6066,40.30387), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.6066,40.30387,-80.51904,40.3421), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51904,40.3421,-80.6066,40.30387), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.6316,40.38547,-80.62736,40.39517), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.62736,40.39517,-80.51903,40.39964), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51903,40.39964,-80.62736,40.39517), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.6049,40.44667,-80.51902,40.47736), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51902,40.47736,-80.6049,40.44667), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.6222,40.5205,-80.51902,40.47736), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.66796,40.5825,-80.58363,40.61552), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.58363,40.61552,-80.62717,40.61994), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.62717,40.61994,-80.58363,40.61552), mapfile, tile_dir, 0, 11, "west virginia-wv")
	render_tiles((-80.51899,40.6388,-80.62717,40.61994), mapfile, tile_dir, 0, 11, "west virginia-wv")