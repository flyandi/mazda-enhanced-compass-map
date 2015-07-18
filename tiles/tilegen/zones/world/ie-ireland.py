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
    # Region: IE
    # Region Name: Ireland

	render_tiles((-9.80334,51.44582,-9.23,51.48221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.23,51.48221,-9.81639,51.48666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.81639,51.48666,-9.38,51.48721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.38,51.48721,-9.81639,51.48666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.58778,51.50471,-9.38,51.48721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.82972,51.53999,-9.40306,51.54582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.40306,51.54582,-9.82972,51.53999), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.12028,51.55221,-9.81139,51.55804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.81139,51.55804,-9.12028,51.55221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.69778,51.58054,-10.10222,51.59721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.10222,51.59721,-9.61917,51.59915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.61917,51.59915,-10.10222,51.59721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.53417,51.60749,-10.14944,51.61499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.14944,51.61499,-8.53417,51.60749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.06556,51.62444,-10.14944,51.61499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.56639,51.63721,-8.75417,51.64221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.75417,51.64221,-8.56639,51.63721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.10361,51.66277,-8.515,51.67749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.515,51.67749,-9.98278,51.67915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.98278,51.67915,-8.515,51.67749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.60195,51.68832,-9.98278,51.67915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.38611,51.7061,-9.98611,51.70971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.98611,51.70971,-9.485,51.71138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.485,51.71138,-9.98611,51.70971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.54945,51.75526,-10.12806,51.75555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.12806,51.75555,-9.54945,51.75526), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.90583,51.75777,-10.12806,51.75555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.29583,51.76443,-9.90583,51.75777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.22389,51.77499,-10.33792,51.78365), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.33792,51.78365,-9.80361,51.78555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.80361,51.78555,-10.33792,51.78365), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.21917,51.79582,-10.17611,51.80444), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.17611,51.80444,-8.21917,51.79582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.01028,51.82526,-8.22889,51.82804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.22889,51.82804,-8.01028,51.82526), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.75917,51.84138,-10.34167,51.84221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.34167,51.84221,-9.75917,51.84138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.20056,51.84583,-10.34167,51.84221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.005,51.85915,-8.33861,51.86971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.33861,51.86971,-9.57944,51.86999), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.57944,51.86999,-8.33861,51.86971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.39306,51.87554,-10.39806,51.87666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.39806,51.87666,-7.88417,51.87777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.88417,51.87777,-10.39806,51.87666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.40944,51.88582,-8.18111,51.8861), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.18111,51.8861,-8.40944,51.88582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.24889,51.90193,-8.18111,51.8861), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.81222,51.94166,-7.8475,51.97804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.8475,51.97804,-10.26611,51.98471), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.26611,51.98471,-7.8475,51.97804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.58833,51.99165,-10.26611,51.98471), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.54833,52.05638,-7.63417,52.06832), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.63417,52.06832,-7.54833,52.05638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.55195,52.08249,-7.63417,52.06832), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.59111,52.10138,-10.47278,52.10249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.47278,52.10249,-7.59111,52.10138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.91111,52.10416,-10.47278,52.10249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.23778,52.11555,-6.93639,52.12054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.93639,52.12054,-10.23778,52.11555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.08361,52.13138,-6.93639,52.12054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.95472,52.14249,-9.76639,52.14416), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.76639,52.14416,-9.95472,52.14249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.91611,52.15137,-9.76639,52.14416), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.8725,52.15137,-9.76639,52.14416), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.075,52.16055,-6.91611,52.15137), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.58583,52.1711,-6.82917,52.17194), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.82917,52.17194,-6.58583,52.1711), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.46695,52.17666,-6.36139,52.17749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.36139,52.17749,-10.46695,52.17666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.95389,52.17832,-6.36139,52.17749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.91333,52.20304,-6.79528,52.20499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.79528,52.20499,-6.47417,52.20666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.47417,52.20666,-6.79528,52.20499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.71889,52.21416,-6.47417,52.20666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.8775,52.23055,-10.16778,52.23166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.16778,52.23166,-9.8775,52.23055), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.83167,52.23444,-10.16778,52.23166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.97056,52.23804,-6.32167,52.24026), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.32167,52.24026,-9.97056,52.23804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.74084,52.24471,-6.32167,52.24026), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.05583,52.25499,-9.74084,52.24471), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.995,52.28277,-6.41194,52.28582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.41194,52.28582,-10.03333,52.28721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.03333,52.28721,-10.16972,52.28749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.16972,52.28749,-10.03333,52.28721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.835,52.31055,-10.16972,52.28749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.40303,52.35365,-6.4925,52.36137), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.4925,52.36137,-6.40303,52.35365), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.83778,52.37638,-6.4925,52.36137), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.94556,52.40249,-9.92834,52.42249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.92834,52.42249,-9.94556,52.40249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.63528,52.46944,-9.92834,52.42249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.67583,52.54527,-9.88611,52.55027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.88611,52.55027,-9.67583,52.54527), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.20056,52.55527,-9.88611,52.55027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.295,52.56554,-9.61695,52.57082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.61695,52.57082,-9.91139,52.57249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.91139,52.57249,-9.61695,52.57082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.28167,52.58833,-9.33195,52.59583), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.33195,52.59583,-9.28167,52.58833), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.40722,52.60499,-9.62333,52.6111), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.62333,52.6111,-9.62778,52.61166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.62778,52.61166,-9.62333,52.6111), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.27528,52.63249,-6.22167,52.64804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.22167,52.64804,-9.11584,52.64971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.11584,52.64971,-6.22167,52.64804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.81056,52.6611,-9.57111,52.66305), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.57111,52.66305,-8.81056,52.6611), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.74222,52.67082,-9.66445,52.67776), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.66445,52.67776,-8.95167,52.67971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.95167,52.67971,-9.66445,52.67776), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.60333,52.73721,-9.49028,52.77332), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.49028,52.77332,-8.94556,52.77388), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.94556,52.77388,-9.49028,52.77332), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.46806,52.92749,-5.99472,52.9611), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.35195,52.92749,-5.99472,52.9611), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.99472,52.9611,-9.46806,52.92749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.35083,53.0711,-9.07556,53.11638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.07556,53.11638,-8.93833,53.14054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.93833,53.14054,-9.25306,53.14915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.25306,53.14915,-8.93833,53.14054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.08556,53.16082,-9.25306,53.14915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.94889,53.18555,-6.07722,53.1936), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.07722,53.1936,-8.94889,53.18555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.98445,53.21555,-9.49,53.2236), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.49,53.2236,-9.54278,53.22777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.54278,53.22777,-9.49,53.2236), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.60028,53.23221,-9.54278,53.22777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.98333,53.24832,-9.60028,53.23221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.61167,53.2686,-9.03306,53.27165), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.03306,53.27165,-9.61167,53.2686), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.55278,53.28971,-9.77528,53.29694), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.77528,53.29694,-9.55278,53.28971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.62,53.31944,-9.64972,53.32665), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.64972,53.32665,-9.61111,53.33166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.61111,53.33166,-9.55778,53.33554), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.55778,53.33554,-9.61111,53.33166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.89722,53.35027,-6.22472,53.35387), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.22472,53.35387,-9.89722,53.35027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.7,53.36137,-6.06667,53.36166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.06667,53.36166,-9.7,53.36137), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.59945,53.36443,-6.06667,53.36166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.94806,53.3786,-9.63833,53.38277), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.63833,53.38277,-6.05333,53.38443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.05333,53.38443,-9.63833,53.38277), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.14361,53.38666,-6.05333,53.38443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.17611,53.40777,-9.80056,53.41026), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.80056,53.41026,-10.17611,53.40777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.14778,53.44388,-10.05445,53.45082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.05445,53.45082,-10.14778,53.44388), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.1925,53.46027,-10.05445,53.45082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.01139,53.47582,-6.10333,53.48832), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.10333,53.48832,-10.01139,53.47582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.13611,53.51971,-6.07778,53.52583), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.07778,53.52583,-10.13611,53.51971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.20195,53.54082,-6.07778,53.52583), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.05028,53.56332,-10.20195,53.54082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.69945,53.59583,-10.00972,53.60194), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.00972,53.60194,-9.69945,53.59583), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.18056,53.61443,-10.00972,53.60194), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.91333,53.64749,-6.18056,53.61443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.90639,53.7586,-6.25611,53.80721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.25611,53.80721,-9.60722,53.82054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.60722,53.82054,-6.25611,53.80721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.90278,53.85777,-6.24472,53.8586), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.24472,53.8586,-9.90278,53.85777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.94278,53.86999,-6.33722,53.87193), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.33722,53.87193,-9.94278,53.86999), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.56695,53.88638,-9.76472,53.89443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.76472,53.89443,-9.56695,53.88638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.38111,53.91721,-9.81222,53.93971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.81222,53.93971,-9.91278,53.94943), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.91278,53.94943,-9.81222,53.93971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.22833,53.98777,-6.10528,53.99221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.10528,53.99221,-6.22833,53.98777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.11389,54.01166,-6.34445,54.01443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.34445,54.01443,-9.90417,54.01638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.90417,54.01638,-6.34445,54.01443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.06778,54.02776,-9.90417,54.01638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.93611,54.06082,-9.89167,54.08527), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.89167,54.08527,-10.06722,54.0886), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.06722,54.0886,-9.89167,54.08527), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.98445,54.09193,-10.06722,54.0886), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.19667,54.09527,-10.12444,54.09637), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.12444,54.09637,-6.27014,54.09687), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.27014,54.09687,-10.12444,54.09637), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.89444,54.10304,-6.27014,54.09687), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.09056,54.11943,-5.89444,54.10304), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.14222,54.13915,-9.9325,54.14555), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.9325,54.14555,-9.14222,54.13915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.09278,54.19582,-9.92028,54.20693), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.92028,54.20693,-9.21833,54.21165), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.21833,54.21165,-9.99861,54.21443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.99861,54.21443,-9.21833,54.21165), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.92417,54.22137,-5.86389,54.22388), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.86389,54.22388,-9.92417,54.22137), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.64806,54.22721,-5.86389,54.22388), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.56583,54.2336,-9.19972,54.23971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.19972,54.23971,-5.79667,54.24249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.79667,54.24249,-9.19972,54.23971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.62,54.25721,-9.88611,54.26082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.08167,54.25721,-9.88611,54.26082), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.88611,54.26082,-8.62,54.25721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.06361,54.2761,-8.53917,54.27999), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.53917,54.27999,-9.76139,54.28138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.76139,54.28138,-8.53917,54.27999), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.93694,54.2836,-9.76139,54.28138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-10.00917,54.30082,-9.84222,54.30499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.84222,54.30499,-9.305,54.30721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.305,54.30721,-9.84222,54.30499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.53417,54.31332,-8.50917,54.31832), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.50917,54.31832,-5.53417,54.31332), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-9.84361,54.32555,-5.49917,54.33193), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.49917,54.33193,-8.66639,54.33777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.66639,54.33777,-5.49917,54.33193), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.71056,54.34693,-8.66639,54.33777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.55778,54.36971,-5.5675,54.38749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.5675,54.38749,-8.58389,54.3911), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.58389,54.3911,-5.5675,54.38749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.64,54.4036,-8.58389,54.3911), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.47889,54.42527,-5.64,54.4036), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.43556,54.46054,-8.39583,54.46193), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.39583,54.46193,-5.43556,54.46054), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.64528,54.49249,-8.21056,54.49805), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.21056,54.49805,-5.64528,54.49249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.26722,54.51582,-5.47472,54.53027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.55972,54.51582,-5.47472,54.53027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.47472,54.53027,-5.71278,54.53387), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.71278,54.53387,-5.47472,54.53027), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.68056,54.57304,-5.90472,54.60221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.90472,54.60221,-8.38194,54.61638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.38194,54.61638,-5.90472,54.60221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.14611,54.6336,-5.92417,54.63388), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.92417,54.63388,-8.14611,54.6336), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.77444,54.65665,-5.67028,54.6711), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.67028,54.6711,-5.57722,54.67805), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.57722,54.67805,-5.67028,54.6711), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.87139,54.68748,-8.80111,54.69166), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.80111,54.69166,-5.87139,54.68748), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.69667,54.74888,-8.4125,54.7536), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.4125,54.7536,-5.69667,54.74888), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.62389,54.76943,-5.68722,54.77749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.68722,54.77749,-8.62389,54.76943), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.52528,54.81138,-8.33195,54.82915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.33195,54.82915,-5.78528,54.83305), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.78528,54.83305,-8.33195,54.82915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.71833,54.83804,-8.50694,54.84165), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.50694,54.84165,-5.71833,54.83804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.78722,54.85332,-8.38417,54.8536), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.38417,54.8536,-5.78722,54.85332), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.31583,54.86971,-8.37,54.87444), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.37,54.87444,-8.31583,54.86971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.42306,54.8911,-5.84806,54.8986), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.84806,54.8986,-8.33945,54.9036), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.33945,54.9036,-5.84806,54.8986), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.46,54.93832,-8.36722,54.94138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.36722,54.94138,-8.46,54.93832), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.665,54.95249,-8.36722,54.94138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.99333,54.98305,-8.45472,54.99165), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.45472,54.99165,-5.99333,54.98305), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.59722,55.01249,-5.96611,55.02415), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.96611,55.02415,-7.59722,55.01249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.32361,55.02415,-7.59722,55.01249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.07361,55.03777,-7.64,55.04443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.64,55.04443,-7.25111,55.0461), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.25111,55.0461,-7.4525,55.04749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.4525,55.04749,-7.56695,55.04804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.56695,55.04804,-7.4525,55.04749), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-5.97861,55.05221,-6.05667,55.05554), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.05667,55.05554,-8.35389,55.05693), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.35389,55.05693,-6.05667,55.05554), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.25306,55.07054,-8.35389,55.05693), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.25306,55.07054,-8.35389,55.05693), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.70417,55.09415,-7.53222,55.09915), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.53222,55.09915,-7.70417,55.09415), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.45472,55.13221,-6.97472,55.13666), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.97472,55.13666,-7.45472,55.13221), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.66806,55.14193,-7.15944,55.14582), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.15944,55.14582,-7.66806,55.14193), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.2925,55.15249,-6.03361,55.15499), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.03361,55.15499,-8.2925,55.15249), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-8.08778,55.15943,-7.57361,55.16138), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.57361,55.16138,-8.08778,55.15943), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.86389,55.16444,-7.72778,55.16721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.72778,55.16721,-7.86389,55.16444), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.75445,55.16721,-7.86389,55.16444), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.81111,55.17721,-7.96472,55.18304), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.96472,55.18304,-7.735,55.18777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.735,55.18777,-6.96806,55.18971), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.96806,55.18971,-7.735,55.18777), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.5475,55.19721,-6.07694,55.19804), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.07694,55.19804,-7.5475,55.19721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.79056,55.20332,-7.87417,55.20638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.87417,55.20638,-6.96445,55.20888), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.96445,55.20888,-7.87417,55.20638), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.69861,55.21416,-7.72056,55.21665), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.72056,55.21665,-7.69861,55.21416), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.93861,55.2411,-7.61694,55.24277), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.61694,55.24277,-6.93861,55.2411), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-6.49694,55.24527,-7.61694,55.24277), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.79889,55.24944,-6.49694,55.24527), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.76111,55.25443,-7.79889,55.24944), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.65778,55.27443,-7.26278,55.27471), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.26278,55.27471,-7.65778,55.27443), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.52111,55.2836,-7.26278,55.27471), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.26765,55.35506,-7.39917,55.37721), mapfile, tile_dir, 0, 11, "ie-ireland")
	render_tiles((-7.39917,55.37721,-7.26765,55.35506), mapfile, tile_dir, 0, 11, "ie-ireland")