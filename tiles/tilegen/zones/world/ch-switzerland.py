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
    # Region: CH
    # Region Name: Switzerland

	render_tiles((9.03167,45.82388,8.55972,46.0123), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.03167,45.82388,8.55972,46.0123), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.03167,45.82388,8.55972,46.0123), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.03167,45.82388,8.55972,46.0123), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.96305,45.83554,8.55972,45.97331), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.96305,45.83554,8.55972,45.97331), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.96305,45.83554,8.55972,45.97331), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.96305,45.83554,8.55972,45.97331), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.09944,45.8836,9.03167,47.34554), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.09944,45.8836,9.03167,47.34554), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.09944,45.8836,9.03167,47.34554), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.09944,45.8836,9.03167,47.34554), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.08555,45.89915,8.55972,46.12109), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.08555,45.89915,8.55972,46.12109), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.08555,45.89915,8.55972,46.12109), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.08555,45.89915,8.55972,46.12109), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.39333,45.91609,9.03167,47.43967), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.39333,45.91609,9.03167,47.43967), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.39333,45.91609,9.03167,47.43967), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.39333,45.91609,9.03167,47.43967), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.91724,45.91716,8.55972,45.93277), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.91724,45.91716,8.55972,45.93277), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.91724,45.91716,8.55972,45.93277), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.91724,45.91716,8.55972,45.93277), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.85278,45.91998,9.03167,47.58832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.85278,45.91998,9.03167,47.58832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.85278,45.91998,9.03167,47.58832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.85278,45.91998,9.03167,47.58832), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.03875,45.93172,9.03167,47.37165), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.03875,45.93172,9.03167,47.37165), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.03875,45.93172,9.03167,47.37165), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.03875,45.93172,9.03167,47.37165), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.90778,45.93277,8.55972,45.9351), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.90778,45.93277,8.55972,45.9351), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.90778,45.93277,8.55972,45.9351), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.90778,45.93277,8.55972,45.9351), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.90717,45.9351,8.55972,45.93277), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.90717,45.9351,8.55972,45.93277), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.90717,45.9351,8.55972,45.93277), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.90717,45.9351,8.55972,45.93277), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.89727,45.95275,8.55972,45.95582), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.89727,45.95275,8.55972,45.95582), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.89727,45.95275,8.55972,45.95582), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.89727,45.95275,8.55972,45.95582), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.89555,45.95582,8.55972,45.95275), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.89555,45.95582,8.55972,45.95275), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.89555,45.95582,8.55972,45.95275), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.89555,45.95582,8.55972,45.95275), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.67734,45.96124,9.03167,47.60638), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.67734,45.96124,9.03167,47.60638), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.67734,45.96124,9.03167,47.60638), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.67734,45.96124,9.03167,47.60638), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.99666,45.97331,8.55972,45.98965), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.99666,45.97331,8.55972,45.98965), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.99666,45.97331,8.55972,45.98965), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.99666,45.97331,8.55972,45.98965), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.57889,45.98332,9.03167,47.58456), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.57889,45.98332,9.03167,47.58456), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.57889,45.98332,9.03167,47.58456), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.57889,45.98332,9.03167,47.58456), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.00625,45.98965,8.55972,45.97331), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.00625,45.98965,8.55972,45.97331), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.00625,45.98965,8.55972,45.97331), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.00625,45.98965,8.55972,45.97331), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.78528,45.98998,9.03167,47.68304), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.78528,45.98998,9.03167,47.68304), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.78528,45.98998,9.03167,47.68304), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.78528,45.98998,9.03167,47.68304), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.9025,45.9911,9.03167,47.5536), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.9025,45.9911,9.03167,47.5536), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.9025,45.9911,9.03167,47.5536), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.9025,45.9911,9.03167,47.5536), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.99805,46.00221,9.03167,47.5536), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.99805,46.00221,9.03167,47.5536), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.99805,46.00221,9.03167,47.5536), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.99805,46.00221,9.03167,47.5536), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.01953,46.0123,8.55972,45.82388), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.01953,46.0123,8.55972,45.82388), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.01953,46.0123,8.55972,45.82388), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.01953,46.0123,8.55972,45.82388), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.87139,46.05193,9.03167,47.35693), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.87139,46.05193,9.03167,47.35693), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.87139,46.05193,9.03167,47.35693), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.87139,46.05193,9.03167,47.35693), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.92917,46.06526,9.03167,47.29193), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.92917,46.06526,9.03167,47.29193), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.92917,46.06526,9.03167,47.29193), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.92917,46.06526,9.03167,47.29193), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.85055,46.07249,9.03167,47.65582), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.85055,46.07249,9.03167,47.65582), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.85055,46.07249,9.03167,47.65582), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.85055,46.07249,9.03167,47.65582), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.69194,46.10138,8.55972,46.10142), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.69194,46.10138,8.55972,46.10142), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.69194,46.10138,8.55972,46.10142), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.69194,46.10138,8.55972,46.10142), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.71161,46.10142,9.03167,47.69776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.71161,46.10142,9.03167,47.69776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.71161,46.10142,9.03167,47.69776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.71161,46.10142,9.03167,47.69776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.76157,46.10153,8.55972,45.98998), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.76157,46.10153,8.55972,45.98998), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.76157,46.10153,8.55972,45.98998), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.76157,46.10153,8.55972,45.98998), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.81305,46.10165,9.03167,47.73526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.81305,46.10165,9.03167,47.73526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.81305,46.10165,9.03167,47.73526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.81305,46.10165,9.03167,47.73526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.08333,46.12109,8.55972,45.89915), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.08333,46.12109,8.55972,45.89915), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.08333,46.12109,8.55972,45.89915), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.08333,46.12109,8.55972,45.89915), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.88444,46.12609,9.03167,47.35693), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.88444,46.12609,9.03167,47.35693), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.88444,46.12609,9.03167,47.35693), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.88444,46.12609,9.03167,47.35693), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((5.96583,46.14027,8.55972,46.20526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((5.96583,46.14027,8.55972,46.20526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((5.96583,46.14027,8.55972,46.20526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((5.96583,46.14027,8.55972,46.20526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.78805,46.14221,8.55972,46.21776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.78805,46.14221,8.55972,46.21776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.78805,46.14221,8.55972,46.21776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.78805,46.14221,8.55972,46.21776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.15222,46.15359,8.55972,46.59332), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.15222,46.15359,8.55972,46.59332), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.15222,46.15359,8.55972,46.59332), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.15222,46.15359,8.55972,46.59332), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6,46.17416,8.55972,46.20526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6,46.17416,8.55972,46.20526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6,46.17416,8.55972,46.20526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6,46.17416,8.55972,46.20526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.15889,46.17665,9.03167,47.62165), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.15889,46.17665,9.03167,47.62165), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.15889,46.17665,9.03167,47.62165), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.15889,46.17665,9.03167,47.62165), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((5.96611,46.20526,8.55972,46.14027), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((5.96611,46.20526,8.55972,46.14027), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((5.96611,46.20526,8.55972,46.14027), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((5.96611,46.20526,8.55972,46.14027), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.79528,46.21776,8.55972,46.43249), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.79528,46.21776,8.55972,46.43249), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.79528,46.21776,8.55972,46.43249), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.79528,46.21776,8.55972,46.43249), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.06305,46.22276,9.03167,46.86499), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.06305,46.22276,9.03167,46.86499), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.06305,46.22276,9.03167,46.86499), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.06305,46.22276,9.03167,46.86499), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.15944,46.24776,8.55972,46.41193), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.15944,46.24776,8.55972,46.41193), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.15944,46.24776,8.55972,46.41193), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.15944,46.24776,8.55972,46.41193), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.44444,46.2486,8.55972,46.45998), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.44444,46.2486,8.55972,46.45998), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.44444,46.2486,8.55972,46.45998), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.44444,46.2486,8.55972,46.45998), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.06083,46.25054,8.55972,46.46082), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.06083,46.25054,8.55972,46.46082), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.06083,46.25054,8.55972,46.46082), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.06083,46.25054,8.55972,46.46082), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.30555,46.25249,8.55972,46.70448), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.30555,46.25249,8.55972,46.70448), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.30555,46.25249,8.55972,46.70448), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.30555,46.25249,8.55972,46.70448), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.11444,46.25777,8.55972,46.40192), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.11444,46.25777,8.55972,46.40192), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.11444,46.25777,8.55972,46.40192), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.11444,46.25777,8.55972,46.40192), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.09027,46.26054,8.55972,46.17665), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.09027,46.26054,8.55972,46.17665), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.09027,46.26054,8.55972,46.17665), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.09027,46.26054,8.55972,46.17665), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.84444,46.27137,8.55972,46.05193), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.84444,46.27137,8.55972,46.05193), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.84444,46.27137,8.55972,46.05193), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.84444,46.27137,8.55972,46.05193), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.17722,46.27248,9.03167,46.8586), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.17722,46.27248,9.03167,46.8586), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.17722,46.27248,9.03167,46.8586), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.17722,46.27248,9.03167,46.8586), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.61944,46.29305,9.03167,47.35999), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.61944,46.29305,9.03167,47.35999), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.61944,46.29305,9.03167,47.35999), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.61944,46.29305,9.03167,47.35999), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.24222,46.29777,8.55972,46.32239), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.24222,46.29777,8.55972,46.32239), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.24222,46.29777,8.55972,46.32239), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.24222,46.29777,8.55972,46.32239), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.70833,46.29832,8.55972,46.35721), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.70833,46.29832,8.55972,46.35721), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.70833,46.29832,8.55972,46.35721), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.70833,46.29832,8.55972,46.35721), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.99639,46.31832,8.55972,46.37915), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.99639,46.31832,8.55972,46.37915), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.99639,46.31832,8.55972,46.37915), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.99639,46.31832,8.55972,46.37915), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.24393,46.32239,8.55972,46.29777), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.24393,46.32239,8.55972,46.29777), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.24393,46.32239,8.55972,46.29777), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.24393,46.32239,8.55972,46.29777), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.29194,46.32304,8.55972,46.48415), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.29194,46.32304,8.55972,46.48415), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.29194,46.32304,8.55972,46.48415), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.29194,46.32304,8.55972,46.48415), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.11,46.32638,8.55972,46.61137), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.11,46.32638,8.55972,46.61137), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.11,46.32638,8.55972,46.61137), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.11,46.32638,8.55972,46.61137), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.5125,46.33193,9.03167,47.09609), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.5125,46.33193,9.03167,47.09609), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.5125,46.33193,9.03167,47.09609), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.5125,46.33193,9.03167,47.09609), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.45916,46.33832,8.55972,46.45998), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.45916,46.33832,8.55972,46.45998), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.45916,46.33832,8.55972,46.45998), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.45916,46.33832,8.55972,46.45998), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.7675,46.35249,8.55972,46.14221), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.7675,46.35249,8.55972,46.14221), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.7675,46.35249,8.55972,46.14221), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.7675,46.35249,8.55972,46.14221), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.7375,46.35721,8.55972,46.29832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.7375,46.35721,8.55972,46.29832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.7375,46.35721,8.55972,46.29832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.7375,46.35721,8.55972,46.29832), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.24639,46.35777,8.55972,46.32239), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.24639,46.35777,8.55972,46.32239), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.24639,46.35777,8.55972,46.32239), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.24639,46.35777,8.55972,46.32239), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.95,46.37915,8.55972,46.31832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.95,46.37915,8.55972,46.31832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.95,46.37915,8.55972,46.31832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.95,46.37915,8.55972,46.31832), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.12444,46.40192,8.55972,46.59332), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.12444,46.40192,8.55972,46.59332), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.12444,46.40192,8.55972,46.59332), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.12444,46.40192,8.55972,46.59332), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.80494,46.4055,8.55972,46.41109), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.80494,46.4055,8.55972,46.41109), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.80494,46.4055,8.55972,46.41109), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.80494,46.4055,8.55972,46.41109), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.32833,46.40693,8.55972,46.70448), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.32833,46.40693,8.55972,46.70448), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.32833,46.40693,8.55972,46.70448), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.32833,46.40693,8.55972,46.70448), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.80889,46.41109,8.55972,46.4055), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.80889,46.41109,8.55972,46.4055), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.80889,46.41109,8.55972,46.4055), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.80889,46.41109,8.55972,46.4055), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.16305,46.41193,8.55972,46.24776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.16305,46.41193,8.55972,46.24776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.16305,46.41193,8.55972,46.24776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.16305,46.41193,8.55972,46.24776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.79611,46.43249,8.55972,46.21776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.79611,46.43249,8.55972,46.21776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.79611,46.43249,8.55972,46.21776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.79611,46.43249,8.55972,46.21776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.40111,46.45609,9.03167,47.70387), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.40111,46.45609,9.03167,47.70387), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.40111,46.45609,9.03167,47.70387), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.40111,46.45609,9.03167,47.70387), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.45166,46.45998,8.55972,46.2486), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.45166,46.45998,8.55972,46.2486), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.45166,46.45998,8.55972,46.2486), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.45166,46.45998,8.55972,46.2486), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.07805,46.46082,8.55972,46.25054), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.07805,46.46082,8.55972,46.25054), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.07805,46.46082,8.55972,46.25054), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.07805,46.46082,8.55972,46.25054), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.63361,46.46415,9.03167,46.98776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.63361,46.46415,9.03167,46.98776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.63361,46.46415,9.03167,46.98776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.63361,46.46415,9.03167,46.98776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.40194,46.47331,8.55972,46.5086), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.40194,46.47331,8.55972,46.5086), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.40194,46.47331,8.55972,46.5086), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.40194,46.47331,8.55972,46.5086), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.05,46.47971,8.55972,46.22276), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.05,46.47971,8.55972,46.22276), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.05,46.47971,8.55972,46.22276), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.05,46.47971,8.55972,46.22276), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.27222,46.48415,9.03167,47.66304), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.27222,46.48415,9.03167,47.66304), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.27222,46.48415,9.03167,47.66304), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.27222,46.48415,9.03167,47.66304), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.36083,46.5086,8.55972,46.47331), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.36083,46.5086,8.55972,46.47331), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.36083,46.5086,8.55972,46.47331), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.36083,46.5086,8.55972,46.47331), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.45444,46.50943,9.03167,47.05804), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.45444,46.50943,9.03167,47.05804), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.45444,46.50943,9.03167,47.05804), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.45444,46.50943,9.03167,47.05804), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.44972,46.53915,8.55972,46.76305), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.44972,46.53915,8.55972,46.76305), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.44972,46.53915,8.55972,46.76305), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.44972,46.53915,8.55972,46.76305), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.06833,46.55499,9.03167,46.86499), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.06833,46.55499,9.03167,46.86499), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.06833,46.55499,9.03167,46.86499), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.06833,46.55499,9.03167,46.86499), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.30166,46.55582,9.03167,46.95054), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.30166,46.55582,9.03167,46.95054), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.30166,46.55582,9.03167,46.95054), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.30166,46.55582,9.03167,46.95054), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.48528,46.59054,9.03167,46.93498), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.48528,46.59054,9.03167,46.93498), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.48528,46.59054,9.03167,46.93498), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.48528,46.59054,9.03167,46.93498), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.13,46.59332,8.55972,46.40192), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.13,46.59332,8.55972,46.40192), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.13,46.59332,8.55972,46.40192), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.13,46.59332,8.55972,46.40192), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.10528,46.61137,8.55972,46.32638), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.10528,46.61137,8.55972,46.32638), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.10528,46.61137,8.55972,46.32638), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.10528,46.61137,8.55972,46.32638), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.47389,46.63332,9.03167,46.88542), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.47389,46.63332,9.03167,46.88542), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.47389,46.63332,9.03167,46.88542), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.47389,46.63332,9.03167,46.88542), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.23333,46.63998,8.55972,46.27248), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.23333,46.63998,8.55972,46.27248), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.23333,46.63998,8.55972,46.27248), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.23333,46.63998,8.55972,46.27248), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.40528,46.64443,9.03167,47.0011), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.40528,46.64443,9.03167,47.0011), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.40528,46.64443,9.03167,47.0011), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.40528,46.64443,9.03167,47.0011), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.38528,46.68942,9.03167,47.0011), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.38528,46.68942,9.03167,47.0011), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.38528,46.68942,9.03167,47.0011), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.38528,46.68942,9.03167,47.0011), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.32502,46.70448,8.55972,46.40693), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.32502,46.70448,8.55972,46.40693), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.32502,46.70448,8.55972,46.40693), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.32502,46.70448,8.55972,46.40693), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.4475,46.76305,8.55972,46.53915), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.4475,46.76305,8.55972,46.53915), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.4475,46.76305,8.55972,46.53915), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.4475,46.76305,8.55972,46.53915), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.45278,46.77443,9.03167,46.92693), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.45278,46.77443,9.03167,46.92693), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.45278,46.77443,9.03167,46.92693), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.45278,46.77443,9.03167,46.92693), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.17528,46.8586,8.55972,46.27248), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.17528,46.8586,8.55972,46.27248), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.17528,46.8586,8.55972,46.27248), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.17528,46.8586,8.55972,46.27248), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.06333,46.86499,8.55972,46.22276), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.06333,46.86499,8.55972,46.22276), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.06333,46.86499,8.55972,46.22276), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.06333,46.86499,8.55972,46.22276), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.46357,46.86935,9.03167,46.88542), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.46357,46.86935,9.03167,46.88542), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.46357,46.86935,9.03167,46.88542), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.46357,46.86935,9.03167,46.88542), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.46624,46.88542,9.03167,46.86935), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.46624,46.88542,9.03167,46.86935), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.46624,46.88542,9.03167,46.86935), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.46624,46.88542,9.03167,46.86935), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.43472,46.92693,8.55972,46.77443), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.43472,46.92693,8.55972,46.77443), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.43472,46.92693,8.55972,46.77443), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.43472,46.92693,8.55972,46.77443), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.48861,46.93498,8.55972,46.59054), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.48861,46.93498,8.55972,46.59054), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.48861,46.93498,8.55972,46.59054), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.48861,46.93498,8.55972,46.59054), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.31083,46.95054,8.55972,46.55582), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.31083,46.95054,8.55972,46.55582), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.31083,46.95054,8.55972,46.55582), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.31083,46.95054,8.55972,46.55582), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.87361,46.9586,9.03167,47.02248), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.87361,46.9586,9.03167,47.02248), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.87361,46.9586,9.03167,47.02248), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.87361,46.9586,9.03167,47.02248), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.58722,46.98776,8.55972,46.46415), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.58722,46.98776,8.55972,46.46415), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.58722,46.98776,8.55972,46.46415), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.58722,46.98776,8.55972,46.46415), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((10.40083,47.0011,8.55972,46.64443), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((10.40083,47.0011,8.55972,46.64443), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((10.40083,47.0011,8.55972,46.64443), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((10.40083,47.0011,8.55972,46.64443), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.87555,47.02248,9.03167,46.9586), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.87555,47.02248,9.03167,46.9586), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.87555,47.02248,9.03167,46.9586), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.87555,47.02248,9.03167,46.9586), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.70278,47.0386,9.03167,47.06832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.70278,47.0386,9.03167,47.06832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.70278,47.0386,9.03167,47.06832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.70278,47.0386,9.03167,47.06832), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.60183,47.04964,9.03167,47.35999), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.60183,47.04964,9.03167,47.35999), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.60183,47.04964,9.03167,47.35999), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.60183,47.04964,9.03167,47.35999), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.47583,47.05804,9.03167,47.19221), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.47583,47.05804,9.03167,47.19221), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.47583,47.05804,9.03167,47.19221), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.47583,47.05804,9.03167,47.19221), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.69444,47.06832,9.03167,47.0386), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.69444,47.06832,9.03167,47.0386), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.69444,47.06832,9.03167,47.0386), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.69444,47.06832,9.03167,47.0386), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.51167,47.09609,8.55972,46.33193), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.51167,47.09609,8.55972,46.33193), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.51167,47.09609,8.55972,46.33193), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.51167,47.09609,8.55972,46.33193), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.48416,47.19221,9.03167,47.05804), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.48416,47.19221,9.03167,47.05804), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.48416,47.19221,9.03167,47.05804), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.48416,47.19221,9.03167,47.05804), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.54041,47.26698,9.03167,47.50407), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.94833,47.29193,8.55972,46.06526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.94833,47.29193,8.55972,46.06526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.94833,47.29193,8.55972,46.06526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.94833,47.29193,8.55972,46.06526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.01083,47.30526,9.03167,47.45499), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.01083,47.30526,9.03167,47.45499), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.01083,47.30526,9.03167,47.45499), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.01083,47.30526,9.03167,47.45499), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.06167,47.34554,8.55972,45.93172), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.06167,47.34554,8.55972,45.93172), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.06167,47.34554,8.55972,45.93172), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.06167,47.34554,8.55972,45.93172), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.88111,47.35693,8.55972,46.12609), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.88111,47.35693,8.55972,46.12609), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.88111,47.35693,8.55972,46.12609), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.88111,47.35693,8.55972,46.12609), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.60528,47.35999,9.03167,47.04964), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.60528,47.35999,9.03167,47.04964), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.60528,47.35999,9.03167,47.04964), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.60528,47.35999,9.03167,47.04964), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.02306,47.37165,9.03167,47.30526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.02306,47.37165,9.03167,47.30526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.02306,47.37165,9.03167,47.30526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.02306,47.37165,9.03167,47.30526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.665,47.38137,9.03167,47.45554), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.665,47.38137,9.03167,47.45554), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.665,47.38137,9.03167,47.45554), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.665,47.38137,9.03167,47.45554), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.39561,47.43967,8.55972,45.91609), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.39561,47.43967,8.55972,45.91609), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.39561,47.43967,8.55972,45.91609), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.39561,47.43967,8.55972,45.91609), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.97833,47.44415,9.03167,47.49721), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.97833,47.44415,9.03167,47.49721), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.97833,47.44415,9.03167,47.49721), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.97833,47.44415,9.03167,47.49721), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.17833,47.44582,9.03167,47.49526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.17833,47.44582,9.03167,47.49526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.17833,47.44582,9.03167,47.49526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.17833,47.44582,9.03167,47.49526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.00778,47.45499,9.03167,47.30526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.00778,47.45499,9.03167,47.30526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.00778,47.45499,9.03167,47.30526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.00778,47.45499,9.03167,47.30526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.65361,47.45554,9.03167,47.38137), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.65361,47.45554,9.03167,47.38137), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.65361,47.45554,9.03167,47.38137), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.65361,47.45554,9.03167,47.38137), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.45278,47.46998,9.03167,47.43967), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.45278,47.46998,9.03167,47.43967), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.45278,47.46998,9.03167,47.43967), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.45278,47.46998,9.03167,47.43967), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.19805,47.49526,9.03167,47.44582), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.19805,47.49526,9.03167,47.44582), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.19805,47.49526,9.03167,47.44582), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.19805,47.49526,9.03167,47.44582), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((6.99055,47.49721,9.03167,47.44415), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((6.99055,47.49721,9.03167,47.44415), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((6.99055,47.49721,9.03167,47.44415), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((6.99055,47.49721,9.03167,47.44415), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.55825,47.50407,9.03167,47.54392), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.55825,47.50407,9.03167,47.54392), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.55825,47.50407,9.03167,47.54392), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.55825,47.50407,9.03167,47.54392), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.52139,47.52582,8.55972,45.98332), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.52139,47.52582,8.55972,45.98332), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.52139,47.52582,8.55972,45.98332), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.52139,47.52582,8.55972,45.98332), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.69722,47.54332,8.55972,45.96124), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.69722,47.54332,8.55972,45.96124), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.69722,47.54332,8.55972,45.96124), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.69722,47.54332,8.55972,45.96124), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.56761,47.54392,9.03167,47.50407), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.56761,47.54392,9.03167,47.50407), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.56761,47.54392,9.03167,47.50407), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.56761,47.54392,9.03167,47.50407), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.94333,47.5536,8.55972,45.9911), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.94333,47.5536,8.55972,45.9911), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.94333,47.5536,8.55972,45.9911), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.94333,47.5536,8.55972,45.9911), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.61833,47.5611,9.03167,47.58456), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.61833,47.5611,9.03167,47.58456), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.61833,47.5611,9.03167,47.58456), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.61833,47.5611,9.03167,47.58456), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.34055,47.57416,8.55972,46.45609), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.34055,47.57416,8.55972,46.45609), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.34055,47.57416,8.55972,46.45609), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.34055,47.57416,8.55972,46.45609), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.5888,47.58456,8.55972,45.98332), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.5888,47.58456,8.55972,45.98332), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.5888,47.58456,8.55972,45.98332), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.5888,47.58456,8.55972,45.98332), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.49303,47.58456,9.03167,47.62832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.49303,47.58456,9.03167,47.62832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.49303,47.58456,9.03167,47.62832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.49303,47.58456,9.03167,47.62832), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.81944,47.58832,8.55972,45.91998), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.81944,47.58832,8.55972,45.91998), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.81944,47.58832,8.55972,45.91998), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.81944,47.58832,8.55972,45.91998), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((7.67444,47.60638,8.55972,45.96124), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((7.67444,47.60638,8.55972,45.96124), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((7.67444,47.60638,8.55972,45.96124), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((7.67444,47.60638,8.55972,45.96124), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.20555,47.62165,8.55972,46.17665), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.20555,47.62165,8.55972,46.17665), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.20555,47.62165,8.55972,46.17665), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.20555,47.62165,8.55972,46.17665), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.50833,47.62832,9.03167,47.58456), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.50833,47.62832,9.03167,47.58456), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.50833,47.62832,9.03167,47.58456), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.50833,47.62832,9.03167,47.58456), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.61889,47.63971,9.03167,47.66026), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.61889,47.63971,9.03167,47.66026), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.61889,47.63971,9.03167,47.66026), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.61889,47.63971,9.03167,47.66026), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.87861,47.65582,8.55972,45.95582), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.87861,47.65582,8.55972,45.95582), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.87861,47.65582,8.55972,45.95582), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.87861,47.65582,8.55972,45.95582), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.62166,47.66026,9.03167,47.63971), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.62166,47.66026,9.03167,47.63971), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.62166,47.66026,9.03167,47.63971), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.62166,47.66026,9.03167,47.63971), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((9.26167,47.66304,8.55972,46.48415), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((9.26167,47.66304,8.55972,46.48415), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((9.26167,47.66304,8.55972,46.48415), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((9.26167,47.66304,8.55972,46.48415), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.41333,47.6711,9.03167,47.70387), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.41333,47.6711,9.03167,47.70387), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.41333,47.6711,9.03167,47.70387), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.41333,47.6711,9.03167,47.70387), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.7975,47.68304,9.03167,47.73526), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.7975,47.68304,9.03167,47.73526), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.7975,47.68304,9.03167,47.73526), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.7975,47.68304,9.03167,47.73526), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.725,47.69776,9.03167,47.76499), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.725,47.69776,9.03167,47.76499), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.725,47.69776,9.03167,47.76499), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.725,47.69776,9.03167,47.76499), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.40667,47.70387,8.55972,46.45609), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.40667,47.70387,8.55972,46.45609), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.40667,47.70387,8.55972,46.45609), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.40667,47.70387,8.55972,46.45609), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.8,47.73526,9.03167,47.68304), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.8,47.73526,9.03167,47.68304), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.8,47.73526,9.03167,47.68304), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.8,47.73526,9.03167,47.68304), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.72694,47.76499,9.03167,47.69776), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.72694,47.76499,9.03167,47.69776), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.72694,47.76499,9.03167,47.69776), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.72694,47.76499,9.03167,47.69776), mapfile, tile_dir, 17, 17, "ch-switzerland")
	render_tiles((8.55972,47.80637,9.03167,47.62832), mapfile, tile_dir, 0, 11, "ch-switzerland")
	render_tiles((8.55972,47.80637,9.03167,47.62832), mapfile, tile_dir, 13, 13, "ch-switzerland")
	render_tiles((8.55972,47.80637,9.03167,47.62832), mapfile, tile_dir, 15, 15, "ch-switzerland")
	render_tiles((8.55972,47.80637,9.03167,47.62832), mapfile, tile_dir, 17, 17, "ch-switzerland")