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
    # Region: FR
    # Region Name: France

	render_tiles((9.21917,41.36638,9.0975,41.39277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.21917,41.36638,9.0975,41.39277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.0975,41.39277,9.25083,41.41193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.25083,41.41193,9.0975,41.39277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.08055,41.47471,8.88222,41.50665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.88222,41.50665,9.08055,41.47471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.78,41.58887,9.35528,41.59388), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.35528,41.59388,8.78,41.58887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.28278,41.60777,9.34889,41.61888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.34889,41.61888,9.28278,41.60777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.79472,41.63249,9.34889,41.61888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.91389,41.67638,8.87889,41.69804), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.87889,41.69804,8.91389,41.67638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.68027,41.75166,8.72472,41.77693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.72472,41.77693,8.68027,41.75166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.80222,41.89443,8.61472,41.89777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.61472,41.89777,8.80222,41.89443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.40694,41.90665,8.61472,41.89777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.75416,41.93166,9.40694,41.90665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.58944,41.9661,8.65861,41.97804), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.65861,41.97804,8.58944,41.9661), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.72639,42.04082,9.50805,42.06554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.50805,42.06554,8.72639,42.04082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.69666,42.10944,8.56194,42.14888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.56194,42.14888,8.69666,42.10944), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.57278,42.21416,8.64111,42.25499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.64111,42.25499,8.68806,42.27026), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.68806,42.27026,8.64111,42.25499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.61222,42.34999,8.55583,42.37666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.55583,42.37666,8.61222,42.34999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.54305,42.42999,8.67222,42.46193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.67222,42.46193,9.54305,42.42999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.66333,42.50777,8.67222,42.46193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.50286,42.56636,9.49722,42.60082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.49722,42.60082,9.46,42.6036), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.46,42.6036,9.49722,42.60082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.9375,42.63638,9.05305,42.6586), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.05305,42.6586,9.29083,42.67332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.29083,42.67332,9.05305,42.6586), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.08389,42.71082,9.21055,42.73082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.21055,42.73082,9.34111,42.73415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.34111,42.73415,9.21055,42.73082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.48944,42.81443,9.34111,42.73415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.32083,42.89499,9.46583,42.92721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.46583,42.92721,9.34639,42.95888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.34639,42.95888,9.46583,42.92721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.34472,43.00082,9.43028,43.00582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((9.43028,43.00582,9.34472,43.00082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.67111,42.33832,2.47667,42.35165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.47667,42.35165,2.08694,42.36332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.08694,42.36332,1.97667,42.3747), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.97667,42.3747,2.08694,42.36332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.67833,42.40165,1.97667,42.3747), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.15083,42.43332,2.25417,42.43471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.25417,42.43471,3.15083,42.43332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.95306,42.4372,3.17696,42.43761), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.17696,42.43761,1.95306,42.4372), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.71097,42.4735,3.03833,42.47498), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.03833,42.47498,1.71097,42.4735), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.7875,42.49026,3.03833,42.47498), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.04944,42.55388,1.78167,42.58166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.78167,42.58166,1.43525,42.59715), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.43525,42.59715,1.78167,42.58166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.54111,42.65387,0.29111,42.67582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.29111,42.67582,0.67611,42.68915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.67611,42.68915,1.38361,42.6897), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.38361,42.6897,0.67611,42.68915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.0575,42.69415,0.4125,42.69526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.4125,42.69526,-0.0575,42.69415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.26194,42.71748,0.4125,42.69526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.66806,42.74804,-0.12389,42.75749), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.12389,42.75749,0.66806,42.74804), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.18778,42.78582,-0.51917,42.79082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.51917,42.79082,-0.18778,42.78582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.39222,42.79638,3.01222,42.79804), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.01222,42.79804,-0.39222,42.79638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.97528,42.80943,3.01222,42.79804), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.50083,42.82221,2.97528,42.80943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.66778,42.83915,0.81583,42.84109), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.81583,42.84109,0.66778,42.83915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.96389,42.84832,-0.30667,42.84915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.30667,42.84915,2.96389,42.84832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.65611,42.8636,-0.30667,42.84915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.0525,42.8786,-0.65611,42.8636), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.03694,42.89721,3.0525,42.8786), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.03778,42.94027,-0.81889,42.94609), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.81889,42.94609,3.03778,42.94027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.74694,42.96554,-0.81889,42.94609), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.36028,43.03165,-1.44056,43.04832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.44056,43.04832,3.08528,43.05027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.08528,43.05027,-1.44056,43.04832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.08861,43.05249,3.08528,43.05027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.18111,43.05638,5.86222,43.05749), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.86222,43.05749,6.18111,43.05638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.03028,43.06915,-1.30056,43.07166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.30056,43.07166,3.03028,43.06915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.12694,43.07555,-1.30056,43.07166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.78111,43.08249,3.10222,43.08415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.10222,43.08415,5.78111,43.08249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.47278,43.09109,6.36805,43.09277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.36805,43.09277,-1.47278,43.09109), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.1175,43.09805,5.91111,43.09999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.91111,43.09999,6.1175,43.09805), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.28833,43.10609,3.04333,43.10693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.04333,43.10693,-1.28833,43.10609), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.16667,43.11555,3.04333,43.10693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.20194,43.12999,5.93472,43.1336), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.93472,43.1336,6.20194,43.12999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.78055,43.13915,5.93472,43.1336), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.37528,43.14971,5.78055,43.13915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.615,43.16943,6.37528,43.14971), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.38195,43.19665,6.59389,43.19749), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.59389,43.19749,-1.38195,43.19665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.43194,43.22083,5.36361,43.22193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.36361,43.22193,5.43194,43.22083), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.68139,43.22443,5.36361,43.22193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.62,43.25638,3.34111,43.2711), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.34111,43.2711,-1.40917,43.27304), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.40917,43.27304,3.34111,43.2711), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.47139,43.2786,-1.40917,43.27304), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.58944,43.29221,3.47139,43.2786), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.65583,43.30943,6.66972,43.31554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.66972,43.31554,-1.65583,43.30943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.36167,43.33527,5.06361,43.34082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.06361,43.34082,5.36167,43.33527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.82167,43.35999,4.60694,43.36193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.60694,43.36193,4.82167,43.35999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.32222,43.36665,4.60694,43.36193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.87361,43.37332,5.01278,43.3736), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.01278,43.3736,4.87361,43.37332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.57333,43.37999,-1.81049,43.38589), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.81639,43.37999,-1.81049,43.38589), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.81049,43.38589,4.57333,43.37999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.65222,43.39943,5.01833,43.40083), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.01833,43.40083,-1.65222,43.39943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.74167,43.41749,4.75694,43.41943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.75694,43.41943,4.74167,43.41749), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.87611,43.42387,4.60167,43.42499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.60167,43.42499,4.87611,43.42387), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.95778,43.42499,4.87611,43.42387), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.85444,43.42721,4.60167,43.42499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.06111,43.43054,6.85444,43.42721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.74805,43.43471,5.06111,43.43054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.53784,43.45141,4.39944,43.45582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.39944,43.45582,4.32111,43.45832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.32111,43.45832,4.39944,43.45582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.19528,43.46138,5.23056,43.46416), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.23056,43.46416,5.06083,43.46555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.06083,43.46555,5.23056,43.46416), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.24055,43.47166,5.16222,43.47694), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.16222,43.47694,4.24055,43.47166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.86055,43.48471,4.24722,43.49194), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.24722,43.49194,5.22722,43.4961), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.22722,43.4961,5.01611,43.49666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.01611,43.49666,5.22722,43.4961), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.12472,43.49832,5.01611,43.49666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.3125,43.50971,4.12472,43.49832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.13306,43.53665,5.03139,43.55666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.03139,43.55666,7.00528,43.55832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.00528,43.55832,5.03139,43.55666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.49333,43.56138,4.06222,43.56332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.06222,43.56332,7.13389,43.56416), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.13389,43.56416,4.06222,43.56332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.15861,43.66305,7.34583,43.72054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.34583,43.72054,7.34954,43.72182), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.34954,43.72182,7.34583,43.72054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.39119,43.72803,7.3908,43.7322), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3908,43.7322,7.39,43.7358), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.39,43.7358,7.3883,43.7389), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3883,43.7389,7.3864,43.7419), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3864,43.7419,7.3883,43.7389), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3864,43.7456,7.3878,43.7486), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3878,43.7486,7.3906,43.7514), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3906,43.7514,7.3939,43.7539), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3939,43.7539,7.3972,43.7561), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.3972,43.7561,7.4407,43.75782), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4407,43.75782,7.4003,43.7586), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4003,43.7586,7.45739,43.75865), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.45739,43.75865,7.4003,43.7586), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.42445,43.75888,7.45739,43.75865), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4383,43.7603,7.4031,43.7614), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4031,43.7614,7.4383,43.7603), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4358,43.7631,7.4064,43.7636), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4064,43.7636,7.4358,43.7631), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4097,43.7658,7.4342,43.7661), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4342,43.7661,7.4097,43.7658), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4128,43.7683,7.4325,43.7692), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4325,43.7692,7.4128,43.7683), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4161,43.7706,7.4294,43.7714), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4294,43.7714,7.4161,43.7706), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.42,43.7725,7.4247,43.7731), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.4247,43.7731,7.42,43.7725), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.53476,43.78345,7.4247,43.7731), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.49222,43.86943,7.53476,43.78345), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.70417,44.07221,7.27944,44.15665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.27944,44.15665,7.67278,44.18276), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.67278,44.18276,7.27944,44.15665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.29417,44.25971,6.93583,44.32332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.93583,44.32332,-1.29417,44.25971), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.88611,44.41582,6.935,44.44304), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.935,44.44304,6.88611,44.41582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.85722,44.50777,6.85278,44.54082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.85278,44.54082,6.85722,44.50777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.20861,44.62582,6.96028,44.62887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.96028,44.62887,-1.24889,44.63082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.24889,44.63082,6.96028,44.62887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.05445,44.65999,-1.19222,44.66193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.19222,44.66193,-1.05445,44.65999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.95083,44.6647,-1.19222,44.66193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.03722,44.68304,7.06889,44.68915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.06889,44.68915,-1.25917,44.69193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.25917,44.69193,7.06889,44.68915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.00083,44.69915,-1.25917,44.69193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.13444,44.7636,-1.16889,44.7761), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.16889,44.7761,-1.13444,44.7636), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.01,44.84859,-1.16889,44.7761), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.74333,44.94776,-0.56056,44.98666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.56056,44.98666,-0.495,44.99888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.495,44.99888,-0.56056,44.98666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.74667,45.02026,-0.58917,45.02165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.58917,45.02165,6.74667,45.02026), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.51944,45.03082,-0.58917,45.02165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.66167,45.0536,-0.62,45.06026), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.62,45.06026,-0.66167,45.0536), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.62017,45.11066,-1.19472,45.1236), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.19472,45.1236,-0.66833,45.12415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.66833,45.12415,-1.19472,45.1236), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.71472,45.13055,6.85028,45.1361), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.85028,45.1361,-0.71472,45.13055), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.76083,45.16832,6.85028,45.1361), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.73583,45.24749,7.12278,45.30193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.12278,45.30193,7.13305,45.35555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.13305,45.35555,-0.80833,45.36721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.80833,45.36721,7.13305,45.35555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.73778,45.40027,7.18139,45.41165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.18139,45.41165,-0.73778,45.40027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.99944,45.51832,-0.85,45.51916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.85,45.51916,6.99944,45.51832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.05778,45.52082,-0.85,45.51916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.09528,45.55276,-1.05833,45.57138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.05833,45.57138,-1.09528,45.55276), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.99083,45.63998,-1.24533,45.70779), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.24533,45.70779,6.82417,45.71499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.82417,45.71499,-0.98528,45.71526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.98528,45.71526,6.82417,45.71499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.79917,45.78082,-1.23083,45.78805), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.23083,45.78805,6.79917,45.78082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.14611,45.80332,-1.23083,45.78805), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.80889,45.83138,-1.13806,45.83999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.13806,45.83999,6.80889,45.83138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.03875,45.93172,-1.07194,45.95888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.07194,45.95888,7.03875,45.93172), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.11139,46.00665,-1.05556,46.0086), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.05556,46.0086,-1.11139,46.00665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.87139,46.05193,6.92917,46.06526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.92917,46.06526,6.87139,46.05193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.10389,46.10027,6.88444,46.12609), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.88444,46.12609,5.96583,46.14027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.96583,46.14027,6.78805,46.14221), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.78805,46.14221,5.96583,46.14027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.15222,46.15359,-1.15222,46.15527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.15222,46.15527,6.15222,46.15359), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.2025,46.15527,6.15222,46.15359), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6,46.17416,-1.15222,46.15527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.96611,46.20526,-1.19389,46.21526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.19389,46.21526,6.79528,46.21776), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.79528,46.21776,-1.19389,46.21526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.06083,46.25054,6.30555,46.25249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.30555,46.25249,6.06083,46.25054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.11444,46.25777,-1.10583,46.2586), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.10583,46.2586,6.11444,46.25777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.84444,46.27137,-1.21444,46.27332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.21444,46.27332,6.84444,46.27137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.24222,46.29777,-1.11222,46.3136), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.11222,46.3136,-1.195,46.31915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.195,46.31915,6.24393,46.32239), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.24393,46.32239,-1.195,46.31915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.44861,46.33804,-1.40556,46.34666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.40556,46.34666,6.7675,46.35249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.7675,46.35249,6.24639,46.35777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.24639,46.35777,6.7675,46.35249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.47222,46.39582,6.12444,46.40192), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.12444,46.40192,6.80494,46.4055), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.80494,46.4055,6.32833,46.40693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.32833,46.40693,6.80494,46.4055), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.80889,46.41109,6.32833,46.40693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.51195,46.41916,6.80889,46.41109), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.79611,46.43249,-1.51195,46.41916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.67389,46.45193,6.07805,46.46082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.07805,46.46082,6.63361,46.46415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.63361,46.46415,6.07805,46.46082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.79945,46.48693,6.63361,46.46415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.13,46.59332,-1.91417,46.6886), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.91417,46.6886,6.32502,46.70448), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.32502,46.70448,-1.91417,46.6886), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.45278,46.77443,-2.10806,46.8136), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.10806,46.8136,6.45278,46.77443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.13389,46.88721,6.43472,46.92693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.43472,46.92693,-2.13389,46.88721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.58722,46.98776,-2.01105,47.02003), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.01105,47.02003,-1.98444,47.03443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.98444,47.03443,6.70278,47.0386), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.70278,47.0386,-1.98444,47.03443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.69444,47.06832,-2.05167,47.09721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.05167,47.09721,6.69444,47.06832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.23972,47.13582,-2.16361,47.16527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.16361,47.16527,-2.23972,47.13582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.81583,47.23721,-2.27333,47.24165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.27333,47.24165,-1.81583,47.23721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.16556,47.26499,-2.27333,47.24165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.4425,47.2911,-2.49583,47.29137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.49583,47.29137,-2.4425,47.2911), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.94833,47.29193,-2.49583,47.29137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.025,47.29694,6.94833,47.29193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.01083,47.30526,-2.15945,47.30999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.15945,47.30999,7.01083,47.30526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.99889,47.31749,-2.44861,47.32249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.44861,47.32249,-1.99889,47.31749), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.06167,47.34554,6.88111,47.35693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.88111,47.35693,7.06167,47.34554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.02306,47.37165,-2.55361,47.37693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.55361,47.37693,7.02306,47.37165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.39611,47.40527,-2.43417,47.4211), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.43417,47.4211,-2.39611,47.40527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.39561,47.43967,6.97833,47.44415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.97833,47.44415,7.17833,47.44582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.17833,47.44582,6.97833,47.44415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.00778,47.45499,7.17833,47.44582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.45278,47.46998,-2.48889,47.48138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.48889,47.48138,-2.80972,47.49165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.80972,47.49165,7.19805,47.49526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.19805,47.49526,6.99055,47.49721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.99055,47.49721,7.19805,47.49526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.12028,47.50054,6.99055,47.49721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.73611,47.50416,-3.12028,47.50054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.36139,47.50416,-3.12028,47.50054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.14917,47.52138,7.52139,47.52582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.52139,47.52582,-2.66222,47.5261), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.66222,47.5261,7.52139,47.52582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.73694,47.54388,-2.91222,47.55166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.91222,47.55166,-2.73694,47.54388), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.96056,47.56443,-2.87722,47.56471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.87722,47.56471,-2.96056,47.56443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.13222,47.57665,7.5888,47.58456), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.5888,47.58456,-3.13222,47.57665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.93667,47.59721,-3.12528,47.59943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.12528,47.59943,-2.93667,47.59721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.88,47.6061,-3.12528,47.59943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.68056,47.61304,-2.88,47.6061), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.76861,47.62054,-2.68056,47.61304), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.21278,47.65249,-3.20056,47.65943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.20056,47.65943,-3.21278,47.65249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.51333,47.68693,-3.44139,47.70138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.44139,47.70138,-3.35444,47.70666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.35444,47.70666,-3.44139,47.70138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.14806,47.74138,-3.35444,47.70666), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.28139,47.78249,-3.84778,47.7936), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.84778,47.7936,-4.20972,47.79943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.20972,47.79943,-4.36195,47.80276), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.36195,47.80276,-4.20972,47.79943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.71278,47.81194,-4.36195,47.80276), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.15556,47.83193,-3.71278,47.81194), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.34389,47.8536,-4.08556,47.86832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.08556,47.86832,-4.18278,47.88138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.18278,47.88138,-4.08556,47.86832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.95111,47.89888,-4.18278,47.88138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.41972,47.96082,7.61583,48.00277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.61583,48.00277,-4.61306,48.02082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.61306,48.02082,-4.52945,48.02249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.52945,48.02249,-4.61306,48.02082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.57167,48.03721,-4.72561,48.04099), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.72561,48.04099,7.57167,48.03721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.66222,48.0736,-4.295,48.09888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.295,48.09888,-4.66222,48.0736), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.605,48.15693,-4.53722,48.1761), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.53722,48.1761,-4.2975,48.17832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.2975,48.17832,-4.53722,48.1761), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.56361,48.2336,-4.49306,48.2361), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.49306,48.2361,-4.56361,48.2336), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.43139,48.24055,-4.49306,48.2361), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.62389,48.28194,-4.51306,48.30027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.51306,48.30027,-4.235,48.30388), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.235,48.30388,-4.51306,48.30027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.32361,48.31999,-4.44861,48.33054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.44861,48.33054,-4.76389,48.33527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.76389,48.33527,7.75083,48.33665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.75083,48.33665,-4.76389,48.33527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.31833,48.35777,7.75083,48.33665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.40556,48.38194,-4.31833,48.35777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.79056,48.42138,-4.29333,48.42721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.29333,48.42721,-4.79056,48.42138), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.77167,48.49165,7.8075,48.51332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.8075,48.51332,-1.9825,48.51415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.9825,48.51415,7.8075,48.51332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.65333,48.53054,-4.74417,48.54582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.74417,48.54582,-1.95694,48.55082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.95694,48.55082,-4.74417,48.54582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.76556,48.57221,-2.17639,48.57943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.17639,48.57943,-4.63195,48.57971), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.63195,48.57971,-2.17639,48.57943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.0025,48.58305,-4.63195,48.57971), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.80194,48.59248,-2.0025,48.58305), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.22639,48.61137,-1.84056,48.61555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.84056,48.61555,-1.63806,48.61665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.63806,48.61665,-1.84056,48.61555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.32917,48.62471,-3.85417,48.62916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.85417,48.62916,-2.46472,48.62999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.46472,48.62999,-3.85417,48.62916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.10583,48.6461,-4.22278,48.64832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.22278,48.64832,-1.87,48.64999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.87,48.64999,-4.42833,48.65054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.42833,48.65054,-2.03,48.65083), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.03,48.65083,-4.42833,48.65054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.95333,48.65221,-2.03,48.65083), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.82472,48.65499,-3.95333,48.65221), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.43389,48.6636,-2.28445,48.66916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.28445,48.66916,-4.31417,48.67082), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.31417,48.67082,-2.28445,48.66916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.59611,48.67582,-3.90028,48.67638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.90028,48.67638,-3.59611,48.67582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.88361,48.68082,-3.90028,48.67638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-4.17667,48.6861,7.92151,48.69003), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.57111,48.6861,7.92151,48.69003), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.92151,48.69003,-2.31434,48.6926), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.31434,48.6926,7.92151,48.69003), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.84917,48.70915,-2.31434,48.6926), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.81083,48.7261,-3.58278,48.72665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.58278,48.72665,-3.81083,48.7261), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.96806,48.73193,-3.58278,48.72665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.53222,48.73916,-1.56083,48.74471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.56083,48.74471,-3.53222,48.73916), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.58083,48.76693,-2.94861,48.76888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-2.94861,48.76888,-3.58083,48.76693), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.22417,48.79388,-2.94861,48.76888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.07917,48.82693,-3.51278,48.83721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.51278,48.83721,-3.26306,48.8386), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.26306,48.8386,-3.51278,48.83721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.60583,48.8411,-3.26306,48.8386), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.22083,48.87054,-3.10167,48.87221), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-3.10167,48.87221,-3.22083,48.87054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.13333,48.88554,-3.10167,48.87221), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.54917,48.92277,8.13333,48.88554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.22739,48.96371,8.19222,48.96887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((8.19222,48.96887,8.22739,48.96371), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.58528,49.00888,-1.55667,49.01582), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.55667,49.01582,-1.58528,49.00888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.74111,49.04166,7.93861,49.04887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.93861,49.04887,7.74111,49.04166), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.54,49.08887,-1.61028,49.09776), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.61028,49.09776,7.54,49.08887), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.03833,49.11832,7.08833,49.12526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.08833,49.12526,7.03833,49.11832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.36139,49.14777,6.83889,49.15498), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.83889,49.15498,7.36139,49.14777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.78722,49.16248,7.48694,49.16415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.48694,49.16415,6.78722,49.16248), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.37528,49.17193,7.48694,49.16415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((7.02667,49.18887,7.37528,49.17193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.8475,49.21526,-1.62944,49.21555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.62944,49.21555,6.8475,49.21526), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.54945,49.21971,-1.62944,49.21555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.21833,49.27471,6.58972,49.32027), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.58972,49.32027,-0.43639,49.3411), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-0.43639,49.3411,-1.14222,49.34388), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.14222,49.34388,-0.43639,49.3411), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.59472,49.36304,-1.8125,49.37777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.8125,49.37777,-1.77417,49.38165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.77417,49.38165,-1.8125,49.37777), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.01389,49.39471,-1.77417,49.38165), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.21833,49.42666,6.49389,49.4472), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.49389,49.4472,5.98139,49.44832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.98139,49.44832,6.49389,49.4472), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.39306,49.45888,6.36222,49.45998), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.36222,49.45998,0.39306,49.45888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.14361,49.47694,5.96333,49.48832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.96333,49.48832,0.14361,49.47694), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.84583,49.49971,6.16528,49.50471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((6.16528,49.50471,5.47305,49.5061), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.47305,49.5061,6.16528,49.50471), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.07806,49.51166,5.47305,49.5061), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.78304,49.52727,-1.88444,49.52915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.88444,49.52915,5.78304,49.52727), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.70521,49.53523,-1.88444,49.52915), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.30167,49.54388,5.70521,49.53523), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.84,49.57832,-1.29945,49.57888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.29945,49.57888,-1.84,49.57832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.25972,49.5861,-1.29945,49.57888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.42333,49.60915,5.30722,49.63081), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.30722,49.63081,-1.85194,49.64277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.85194,49.64277,5.30722,49.63081), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.32972,49.65998,-1.48278,49.67554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.48278,49.67554,5.21694,49.69054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.94583,49.67554,5.21694,49.69054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.21694,49.69054,-1.25833,49.69444), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.25833,49.69444,5.21694,49.69054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.27611,49.69859,-1.25833,49.69444), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.18444,49.70277,-1.41083,49.70499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.41083,49.70499,0.18444,49.70277), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((-1.94194,49.72388,-1.41083,49.70499), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((5.09778,49.76859,4.85805,49.79638), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.85805,49.79638,5.09778,49.76859), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((0.61194,49.85749,4.88111,49.9147), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.88111,49.9147,4.45833,49.93859), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.45833,49.93859,1.13111,49.94888), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.13111,49.94888,4.45833,49.93859), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.80083,49.97776,4.16833,49.98137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.16833,49.98137,4.80083,49.97776), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.15,49.98637,4.16833,49.98137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.67361,49.99638,4.15,49.98637), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.85194,50.07943,4.22639,50.0811), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.22639,50.0811,4.85194,50.07943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.43555,50.09832,4.22639,50.0811), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.7575,50.12943,4.13778,50.13776), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.13778,50.13776,4.7575,50.12943), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.87639,50.15498,4.13778,50.13776), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.65694,50.18416,1.50556,50.20193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.50556,50.20193,1.645,50.21721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.645,50.21721,1.555,50.21944), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.555,50.21944,1.645,50.21721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.21611,50.26527,1.54806,50.26665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.54806,50.26665,4.21611,50.26527), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.53417,50.2886,1.54806,50.26665), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.72528,50.3136,4.09083,50.31443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((4.09083,50.31443,3.72528,50.3136), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.67194,50.34637,3.76361,50.35193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.76361,50.35193,3.67194,50.34637), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.55278,50.35999,3.76361,50.35193), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.61028,50.36832,1.55278,50.35999), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.55889,50.40249,1.61028,50.36832), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.66889,50.44415,1.55889,50.40249), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.36722,50.49554,3.60222,50.49721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.60222,50.49721,3.36722,50.49554), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.28,50.53832,3.60222,50.49721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.28139,50.59415,1.57694,50.61221), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.57694,50.61221,3.28139,50.59415), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.90128,50.69705,3.19333,50.74137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.90128,50.69705,3.19333,50.74137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.19333,50.74137,2.78194,50.75555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.78194,50.75555,3.19333,50.74137), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.03528,50.77387,2.78194,50.75555), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((3.11305,50.79332,3.03528,50.77387), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.62944,50.82471,3.11305,50.79332), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.57611,50.86443,2.6125,50.88721), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.6125,50.88721,1.57611,50.86443), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((1.94333,50.99527,2.47278,51.07054), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.47278,51.07054,2.54167,51.0911), mapfile, tile_dir, 0, 11, "fr-france")
	render_tiles((2.54167,51.0911,2.47278,51.07054), mapfile, tile_dir, 0, 11, "fr-france")