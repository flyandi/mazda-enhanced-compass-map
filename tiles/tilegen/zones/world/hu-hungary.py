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
    # Region: HU
    # Region Name: Hungary

	render_tiles((18.27897,45.76035,18.08249,45.76665), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.08249,45.76665,18.27897,45.76035), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.18916,45.78443,18.55944,45.80165), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.55944,45.80165,17.79944,45.80888), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.79944,45.80888,18.55944,45.80165), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.62916,45.87609,18.80305,45.88665), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.80305,45.88665,18.8281,45.89668), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.8281,45.89668,18.80305,45.88665), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.69916,45.9211,17.57666,45.94054), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.57666,45.94054,17.35861,45.94999), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.35861,45.94999,17.4261,45.9547), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.4261,45.9547,17.35861,45.94999), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.03819,45.9676,17.32055,45.97443), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.32055,45.97443,19.03819,45.9676), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.15527,45.98721,19.24971,45.99332), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.24971,45.99332,19.15527,45.98721), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.08389,46.01943,19.12388,46.0236), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.12388,46.0236,19.08389,46.01943), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.26114,46.11533,17.20972,46.11832), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.20972,46.11832,20.26114,46.11533), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.80722,46.12804,17.20972,46.11832), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.33555,46.15804,20.11832,46.16693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.11832,46.16693,19.57055,46.1736), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.57055,46.1736,19.71082,46.17499), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.71082,46.17499,19.57055,46.1736), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.72166,46.18443,19.71082,46.17499), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.76305,46.19915,17.06416,46.20665), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.06416,46.20665,20.76305,46.19915), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.05361,46.23888,16.93916,46.24609), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.75333,46.23888,16.93916,46.24609), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.93916,46.24609,21.05361,46.23888), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.82972,46.27721,16.93916,46.24609), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.87638,46.3186,21.18722,46.32999), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.18722,46.32999,16.87638,46.3186), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.19721,46.39137,21.2875,46.41443), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.2875,46.41443,21.19721,46.39137), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.2961,46.44748,16.60924,46.47517), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.60924,46.47517,21.26166,46.48693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.26166,46.48693,16.60924,46.47517), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.32666,46.61998,16.39166,46.63638), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.39166,46.63638,21.43832,46.6486), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.43832,46.6486,16.39166,46.63638), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.42666,46.68776,21.43832,46.6486), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.31833,46.77971,16.35055,46.84109), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.35055,46.84109,16.1067,46.85139), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.1067,46.85139,16.35055,46.84109), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.28167,46.87248,16.1067,46.85139), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.60916,46.89499,16.28167,46.87248), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.69582,47.00082,16.34694,47.00999), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.34694,47.00999,21.69582,47.00082), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.65166,47.02554,16.45916,47.02943), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.45916,47.02943,21.65166,47.02554), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.51694,47.06081,16.45916,47.02943), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.46805,47.09526,16.51694,47.06081), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.52777,47.13443,16.4561,47.14693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.4561,47.14693,16.52055,47.15526), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.52055,47.15526,16.4561,47.14693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.43861,47.25277,21.86888,47.26221), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.86888,47.26221,16.43861,47.25277), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.49166,47.27998,21.86888,47.26221), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.9225,47.35443,22.00471,47.37498), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.00471,47.37498,21.9225,47.35443), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.45055,47.4072,22.00471,47.37498), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.64555,47.45277,16.45055,47.4072), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.01028,47.5086,16.71249,47.53999), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.71249,47.53999,22.01028,47.5086), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.64888,47.62971,16.43388,47.66443), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.43388,47.66443,16.76194,47.68526), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.76194,47.68526,16.7973,47.688), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.7973,47.688,16.76194,47.68526), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.45055,47.69804,16.7973,47.688), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.08361,47.71027,16.53249,47.71471), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.53249,47.71471,17.08361,47.71027), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.69645,47.73624,18.3375,47.74082), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.3375,47.74082,22.42138,47.74387), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.42138,47.74387,22.31888,47.74582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.68666,47.74387,22.31888,47.74582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.31888,47.74582,17.78416,47.74693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.78416,47.74693,22.31888,47.74582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((16.55583,47.7561,17.78416,47.74693), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.61221,47.76859,16.55583,47.7561), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.44166,47.7911,22.61221,47.76859), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.8125,47.81666,18.84941,47.81844), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.84941,47.81844,18.8125,47.81666), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.60333,47.82804,22.77444,47.83637), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.77444,47.83637,17.0575,47.84443), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.0575,47.84443,22.77444,47.83637), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.01583,47.86804,18.76694,47.87776), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.76694,47.87776,22.77083,47.87943), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.77083,47.87943,18.76694,47.87776), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.09055,47.88776,22.77083,47.87943), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.89486,47.9533,18.77028,47.95609), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.77028,47.95609,22.89486,47.9533), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.1036,47.9772,18.80833,47.99387), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.80833,47.99387,17.1799,48.00182), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.1799,48.00182,18.80833,47.99387), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.86527,48.01054,17.1799,48.00182), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((17.25166,48.02499,18.8275,48.03582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((18.8275,48.03582,22.88694,48.03665), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.88694,48.03665,18.8275,48.03582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.00125,48.06878,22.84095,48.08677), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.84095,48.08677,19.47638,48.08915), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.47638,48.08915,22.84095,48.08677), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.62046,48.10181,19.47638,48.08915), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.74416,48.11471,22.62046,48.10181), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.9186,48.12998,22.74416,48.11471), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.06555,48.18027,19.535,48.2122), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.535,48.2122,19.66221,48.23193), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((19.66221,48.23193,22.38333,48.24415), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.38333,48.24415,22.50777,48.24526), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.50777,48.24526,22.38333,48.24415), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.29194,48.25471,20.15027,48.26027), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.15027,48.26027,20.29194,48.25471), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.36472,48.30582,22.31583,48.3211), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.31583,48.3211,20.36472,48.30582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.73804,48.35082,22.26027,48.36082), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.26027,48.36082,22.31944,48.36166), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.31944,48.36166,22.26027,48.36082), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.86277,48.36554,22.31944,48.36166), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.10166,48.3772,21.86277,48.36554), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.27138,48.40415,22.15145,48.41206), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.15145,48.41206,22.27138,48.40415), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((22.15145,48.41206,22.27138,48.40415), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.62805,48.44942,22.15145,48.41206), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.12555,48.49249,21.61416,48.49832), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.61416,48.49832,21.12555,48.49249), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.50999,48.53777,21.44277,48.57526), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((21.44277,48.57526,20.82471,48.57582), mapfile, tile_dir, 0, 11, "hu-hungary")
	render_tiles((20.82471,48.57582,21.44277,48.57526), mapfile, tile_dir, 0, 11, "hu-hungary")