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
    # Region: GN
    # Region Name: Guinea

	render_tiles((8.65389,3.21361,8.48666,3.24639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.65389,3.21361,8.48666,3.24639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.48666,3.24639,8.65389,3.21361), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.425,3.33389,8.48666,3.24639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.44972,3.43528,8.57166,3.45722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.57166,3.45722,8.51611,3.46194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.51611,3.46194,8.57166,3.45722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.91639,3.58361,8.96139,3.69972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.96139,3.69972,8.91889,3.74778), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.91889,3.74778,8.68889,3.7525), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.68889,3.7525,8.75305,3.75389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((8.75305,3.75389,8.68889,3.7525), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.11091,7.19394,-9.09778,7.2325), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.09778,7.2325,-8.90417,7.25361), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.90417,7.25361,-9.09778,7.2325), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.92861,7.28805,-8.83944,7.30028), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.83944,7.30028,-9.19806,7.3125), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.19806,7.3125,-8.83944,7.30028), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.48442,7.36366,-9.20417,7.38139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.20417,7.38139,-9.39,7.38861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.39,7.38861,-9.20417,7.38139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.30506,7.41637,-9.4225,7.425), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.4225,7.425,-9.30506,7.41637), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.41333,7.48944,-8.70805,7.51833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.70805,7.51833,-8.21222,7.545), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.21222,7.545,-8.46896,7.55987), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.46896,7.55987,-8.72305,7.57222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.72305,7.57222,-8.46896,7.55987), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.18667,7.59139,-8.72305,7.57222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.55611,7.61861,-8.18667,7.59139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.39917,7.61861,-8.18667,7.59139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.55722,7.69167,-8.67055,7.69666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.67055,7.69666,-8.55722,7.69167), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.3552,7.74359,-8.67055,7.69666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.43611,7.93194,-7.94833,8.01861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.94833,8.01861,-8.05,8.03194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.05,8.03194,-9.41028,8.03277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.41028,8.03277,-8.05,8.03194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.42833,8.05833,-7.96917,8.06639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.96917,8.06639,-9.42833,8.05833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.46278,8.13389,-8.07278,8.16389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.07278,8.16389,-9.49139,8.17527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.49139,8.17527,-8.07278,8.16389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.98861,8.19166,-9.49139,8.17527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.51722,8.24277,-8.25111,8.25222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.25111,8.25222,-9.51722,8.24277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.70127,8.27575,-8.25111,8.25222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.54944,8.31027,-10.65222,8.33805), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.65222,8.33805,-9.48444,8.34638), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.48444,8.34638,-10.65222,8.33805), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.48583,8.355,-8.22111,8.35638), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.22111,8.35638,-10.48583,8.355), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.74861,8.37638,-7.64798,8.37976), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.64798,8.37976,-7.74861,8.37638), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.65611,8.38972,-7.64798,8.37976), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.58194,8.41389,-10.04528,8.42138), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.04528,8.42138,-7.765,8.42222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.765,8.42222,-10.04528,8.42138), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.88,8.42833,-9.70667,8.43277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.70667,8.43277,-10.06361,8.43472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.06361,8.43472,-10.65028,8.435), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.65028,8.435,-10.06361,8.43472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.83056,8.43583,-10.65028,8.435), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.24229,8.44266,-7.83056,8.43583), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.6275,8.45555,-8.24229,8.44266), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.95222,8.48222,-7.82472,8.48611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.82472,8.48611,-10.24889,8.48777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.24889,8.48777,-10.26652,8.4887), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.69417,8.48777,-10.26652,8.4887), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.26652,8.4887,-9.6625,8.48916), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.6625,8.48916,-10.26652,8.4887), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.83778,8.49194,-9.6625,8.48916), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.38611,8.495,-8.19833,8.49666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.19833,8.49666,-7.93528,8.49777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.93528,8.49777,-8.19833,8.49666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.05667,8.50472,-7.93528,8.49777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.16222,8.53222,-10.05667,8.50472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.77371,8.56968,-10.58972,8.57166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.58972,8.57166,-9.77371,8.56968), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.67472,8.62083,-10.50028,8.625), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.50028,8.625,-7.67472,8.62083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.46722,8.68416,-10.50028,8.625), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.53028,8.75139,-7.79472,8.75694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.79472,8.75694,-10.53028,8.75139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.91444,8.76722,-7.79472,8.75694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.95639,8.80111,-7.91444,8.76722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.59528,8.88944,-7.93667,8.93305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.93667,8.93305,-10.59528,8.88944), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.895,9.02166,-13.29843,9.03845), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.29843,9.03845,-13.09583,9.04583), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.09583,9.04583,-13.29843,9.03845), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.57125,9.057,-13.09583,9.04583), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.73694,9.07277,-13.32583,9.07333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.32583,9.07333,-7.73694,9.07277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.73389,9.08,-13.32583,9.07333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.1875,9.08694,-10.73389,9.08), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.74222,9.09972,-13.0075,9.10361), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.0075,9.10361,-7.74222,9.09972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.25056,9.15472,-13.28139,9.16472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.28139,9.16472,-13.25056,9.15472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.72694,9.18111,-7.90833,9.18277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.90833,9.18277,-13.23695,9.18416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.23695,9.18416,-13.32306,9.18472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.32306,9.18472,-13.23695,9.18416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.92083,9.21777,-12.96083,9.23444), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.96083,9.23444,-7.92083,9.21777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.90091,9.27047,-13.37694,9.27305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.37694,9.27305,-12.90091,9.27047), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.84417,9.28222,-12.93056,9.28889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.93056,9.28889,-10.65667,9.295), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.41278,9.28889,-10.65667,9.295), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.65667,9.295,-12.93056,9.28889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.78806,9.32722,-13.30972,9.34944), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.30972,9.34944,-12.78806,9.32722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.85972,9.37527,-7.97055,9.3875), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.97055,9.3875,-10.74833,9.38778), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.74833,9.38778,-7.97055,9.3875), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.05139,9.3975,-12.70472,9.39916), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.70472,9.39916,-8.05139,9.3975), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.485,9.41639,-7.86194,9.42389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.86194,9.42389,-13.485,9.41639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.82861,9.43333,-13.39834,9.43666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.39834,9.43666,-10.82861,9.43333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.13818,9.49788,-13.54528,9.49972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.54528,9.49972,-8.13818,9.49788), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.71639,9.50416,-13.54528,9.49972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.72639,9.51333,-13.71639,9.50416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.58722,9.54083,-13.72639,9.51333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.58667,9.56833,-13.58722,9.54083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.93639,9.65833,-12.58361,9.66222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.58361,9.66222,-10.93639,9.65833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.51945,9.71083,-13.6775,9.74333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.6775,9.74333,-13.73695,9.74889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.73695,9.74889,-13.6775,9.74333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.64806,9.77527,-13.55972,9.78111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.55972,9.78111,-13.75556,9.78611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.75556,9.78611,-13.55972,9.78111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.11583,9.80305,-13.75556,9.78611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.65833,9.83694,-13.78722,9.84194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.78722,9.84194,-13.65833,9.83694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.83861,9.8575,-12.50333,9.86222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.50333,9.86222,-13.83861,9.8575), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.10472,9.86722,-12.50333,9.86222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.13194,9.875,-8.10472,9.86722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.7025,9.9075,-11.89333,9.93139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.89333,9.93139,-12.23556,9.93888), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.23556,9.93888,-8.16056,9.94416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.16056,9.94416,-12.23556,9.93888), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.895,9.99611,-11.21472,9.9975), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.21472,9.9975,-11.895,9.99611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.13333,10.01055,-11.21472,9.9975), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.06695,10.03861,-14.13472,10.04694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.13472,10.04694,-14.06695,10.03861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.08667,10.0625,-14.04389,10.07083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.04389,10.07083,-13.99306,10.07694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.99306,10.07694,-14.18028,10.07833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.18028,10.07833,-13.99306,10.07694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.00917,10.09944,-14.06361,10.10111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.06361,10.10111,-14.22083,10.10166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.22083,10.10166,-14.06361,10.10111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.18333,10.10444,-14.22083,10.10166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.14639,10.1375,-14.05334,10.15111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.05334,10.15111,-14.27611,10.15333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.27611,10.15333,-14.05334,10.15111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.97768,10.16547,-14.13778,10.17694), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.13778,10.17694,-7.97768,10.16547), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.32139,10.20194,-14.42694,10.22611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.42694,10.22611,-14.46667,10.2425), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.46667,10.2425,-7.94056,10.24333), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.94056,10.24333,-14.46667,10.2425), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-7.985,10.33777,-8.09389,10.35638), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.09389,10.35638,-7.985,10.33777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.21556,10.42166,-14.55056,10.42444), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.55056,10.42444,-8.21556,10.42166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.12222,10.43722,-14.55056,10.42444), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.62833,10.47166,-14.6625,10.50555), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.6625,10.50555,-14.53528,10.50639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.53528,10.50639,-14.6625,10.50555), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.28222,10.54944,-14.53528,10.50639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.7025,10.63888,-14.755,10.69416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.755,10.69416,-14.71639,10.69889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.71639,10.69889,-14.755,10.69416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.60833,10.74389,-14.79528,10.75166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.79528,10.75166,-14.60833,10.74389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.9575,10.76861,-15.02361,10.78111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-15.02361,10.78111,-14.9575,10.76861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.66639,10.81111,-14.80889,10.82083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.80889,10.82083,-14.66639,10.81111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-15.07861,10.85194,-14.98056,10.86833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.98056,10.86833,-14.74,10.87166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.74,10.87166,-14.98056,10.86833), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.50472,10.88,-14.74,10.87166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-15.07361,10.89778,-14.91445,10.90805), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.91445,10.90805,-15.07361,10.89778), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-15.03056,10.925,-14.90833,10.93389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.90833,10.93389,-14.81444,10.93972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.81444,10.93972,-14.90833,10.93389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.77917,10.94666,-14.81444,10.93972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.67139,10.955,-14.77917,10.94666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-15.01974,10.96388,-8.60278,10.96416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.60278,10.96416,-15.01974,10.96388), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.87639,10.96972,-8.54417,10.97527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.54417,10.97527,-14.75528,10.97639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.75528,10.97639,-8.54417,10.97527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.82056,10.99055,-8.68278,10.99194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.68278,10.99194,-14.82056,10.99055), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.28972,11.00777,-14.68806,11.0225), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.68806,11.0225,-14.92083,11.02583), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.92083,11.02583,-14.68806,11.0225), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.88917,11.03472,-14.92083,11.02583), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.35111,11.05722,-8.48889,11.05889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.48889,11.05889,-8.35111,11.05722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.85528,11.19777,-8.515,11.22639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.515,11.22639,-14.85528,11.19777), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.37611,11.28194,-8.47472,11.29111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.47472,11.29111,-8.37611,11.28194), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.35472,11.32277,-8.41028,11.33527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.41028,11.33527,-8.35472,11.32277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.35965,11.37156,-14.78417,11.38527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.78417,11.38527,-8.35965,11.37156), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.51417,11.43027,-14.78417,11.38527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.60778,11.47583,-8.53417,11.49389), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.53417,11.49389,-14.69139,11.50667), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.69139,11.50667,-14.51528,11.51222), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.51528,11.51222,-14.69139,11.50667), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.00222,11.64083,-8.70305,11.65889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.70305,11.65889,-8.83167,11.66166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.83167,11.66166,-8.70305,11.65889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.87806,11.66916,-8.83167,11.66166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-14.27667,11.67889,-13.79167,11.68555), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.79167,11.68555,-14.27667,11.67889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.79944,11.71472,-13.70917,11.71527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.70917,11.71527,-13.79944,11.71472), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.86111,11.74416,-13.70917,11.71527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.65241,11.89351,-11.25389,11.99611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.25389,11.99611,-13.70667,12.00139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.70667,12.00139,-11.25389,11.99611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.79657,12.00806,-13.70667,12.00139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.76111,12.02805,-11.15417,12.04055), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.15417,12.04055,-8.90417,12.04444), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.90417,12.04444,-11.15417,12.04055), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.6725,12.06777,-8.90417,12.04444), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.51111,12.11611,-9.98694,12.12083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.98694,12.12083,-10.51111,12.11611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.6675,12.12778,-9.98694,12.12083), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.46278,12.14028,-13.89028,12.14361), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.89028,12.14361,-11.46278,12.14028), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.97062,12.15323,-13.89028,12.14361), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.89944,12.17805,-10.33333,12.18527), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.33333,12.18527,-8.89944,12.17805), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.89222,12.20111,-11.495,12.20611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.495,12.20611,-11.04528,12.20722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.04528,12.20722,-11.495,12.20611), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.27306,12.20944,-13.95667,12.20972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.95667,12.20972,-10.27306,12.20944), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.97994,12.22371,-10.32917,12.22416), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-10.32917,12.22416,-8.97994,12.22371), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.87861,12.24555,-9.46667,12.24861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.46667,12.24861,-9.34544,12.2498), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.34544,12.2498,-9.46667,12.24861), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.71472,12.2575,-9.34544,12.2498), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.43,12.28722,-13.69424,12.29309), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.69424,12.29309,-11.43,12.28722), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.34528,12.30139,-13.69424,12.29309), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.66361,12.35166,-8.94667,12.35305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-8.94667,12.35305,-13.66361,12.35166), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.29333,12.35555,-8.94667,12.35305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.57278,12.35916,-9.29333,12.35555), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.44111,12.36333,-12.57278,12.35916), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.43639,12.38277,-11.84167,12.38639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.84167,12.38639,-12.43639,12.38277), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.96889,12.39139,-11.84167,12.38639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.49694,12.39667,-11.96889,12.39139), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.37333,12.40464,-12.09889,12.41028), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.09889,12.41028,-11.37333,12.40464), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.90833,12.42889,-11.48639,12.43666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-11.48639,12.43666,-12.63333,12.4375), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.05778,12.43666,-12.63333,12.4375), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.63333,12.4375,-11.48639,12.43666), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.39889,12.44611,-12.63333,12.4375), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.64417,12.47138,-13.03778,12.47305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.03778,12.47305,-13.64417,12.47138), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.95528,12.47639,-13.03778,12.47305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.84139,12.48,-12.95528,12.47639), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.36222,12.4875,-12.84139,12.48), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-9.31131,12.50425,-9.36222,12.4875), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.06889,12.525,-12.93167,12.54111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.93167,12.54111,-12.89167,12.54305), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-12.89167,12.54305,-12.93167,12.54111), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.71472,12.56889,-13.04167,12.59), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.04167,12.59,-13.71472,12.56889), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.06139,12.63972,-13.71255,12.66603), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.71255,12.66603,-13.06139,12.63972), mapfile, tile_dir, 0, 11, "gn-guinea")
	render_tiles((-13.71255,12.66603,-13.06139,12.63972), mapfile, tile_dir, 0, 11, "gn-guinea")