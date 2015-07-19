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
    # Region: LY
    # Region Name: Libya

	render_tiles((23.99956,19.49776,11.52714,20.00194), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.99956,19.49776,11.52714,20.00194), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.99956,19.49776,11.52714,20.00194), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.99956,19.49776,11.52714,20.00194), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.37277,19.99582,23.99956,31.99027), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.37277,19.99582,23.99956,31.99027), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.37277,19.99582,23.99956,31.99027), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.37277,19.99582,23.99956,31.99027), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.00083,19.99916,11.52714,22.47582), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.00083,19.99916,11.52714,22.47582), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.00083,19.99916,11.52714,22.47582), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.00083,19.99916,11.52714,22.47582), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.00083,20.00194,11.52714,19.49776), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.00083,20.00194,11.52714,19.49776), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.00083,20.00194,11.52714,19.49776), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.00083,20.00194,11.52714,19.49776), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.00222,20.64416,11.52714,22.47582), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.00222,20.64416,11.52714,22.47582), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.00222,20.64416,11.52714,22.47582), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.00222,20.64416,11.52714,22.47582), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((21.39471,20.85221,23.99956,32.77999), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((21.39471,20.85221,23.99956,32.77999), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((21.39471,20.85221,23.99956,32.77999), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((21.39471,20.85221,23.99956,32.77999), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.00355,22.0013,11.52714,20.64416), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.00355,22.0013,11.52714,20.64416), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.00355,22.0013,11.52714,20.64416), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.00355,22.0013,11.52714,20.64416), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((18.36361,22.34444,23.99956,30.56055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((18.36361,22.34444,23.99956,30.56055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((18.36361,22.34444,23.99956,30.56055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((18.36361,22.34444,23.99956,30.56055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.00111,22.47582,11.52714,19.99916), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.00111,22.47582,11.52714,19.99916), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.00111,22.47582,11.52714,19.99916), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.00111,22.47582,11.52714,19.99916), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((14.235,22.61416,23.99956,32.70388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((14.235,22.61416,23.99956,32.70388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((14.235,22.61416,23.99956,32.70388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((14.235,22.61416,23.99956,32.70388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.00063,23.00037,23.99956,32.40443), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.00063,23.00037,23.99956,32.40443), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.00063,23.00037,23.99956,32.40443), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.00063,23.00037,23.99956,32.40443), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.59555,23.13943,23.99956,32.78777), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.59555,23.13943,23.99956,32.78777), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.59555,23.13943,23.99956,32.78777), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.59555,23.13943,23.99956,32.78777), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.41722,23.21471,23.99956,32.89971), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.41722,23.21471,23.99956,32.89971), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.41722,23.21471,23.99956,32.89971), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.41722,23.21471,23.99956,32.89971), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((16.00083,23.45055,23.99956,31.2761), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((16.00083,23.45055,23.99956,31.2761), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((16.00083,23.45055,23.99956,31.2761), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((16.00083,23.45055,23.99956,31.2761), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.98645,23.52232,23.99956,33.08415), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.98645,23.52232,23.99956,33.08415), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.98645,23.52232,23.99956,33.08415), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.98645,23.52232,23.99956,33.08415), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.99944,23.82972,11.52714,19.99916), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.99944,23.82972,11.52714,19.99916), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.99944,23.82972,11.52714,19.99916), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.99944,23.82972,11.52714,19.99916), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.55889,24.30249,23.99956,32.44221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.55889,24.30249,23.99956,32.44221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.55889,24.30249,23.99956,32.44221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.55889,24.30249,23.99956,32.44221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.42222,24.47805,23.99956,31.73055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.42222,24.47805,23.99956,31.73055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.42222,24.47805,23.99956,31.73055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.42222,24.47805,23.99956,31.73055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.71527,24.56721,23.99956,32.00888), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.71527,24.56721,23.99956,32.00888), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.71527,24.56721,23.99956,32.00888), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.71527,24.56721,23.99956,32.00888), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.25222,24.60583,11.52714,24.75111), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.25222,24.60583,11.52714,24.75111), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.25222,24.60583,11.52714,24.75111), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.25222,24.60583,11.52714,24.75111), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.21833,24.75111,23.99956,30.7361), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.21833,24.75111,23.99956,30.7361), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.21833,24.75111,23.99956,30.7361), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.21833,24.75111,23.99956,30.7361), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.05444,24.83805,11.52714,25.33749), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.05444,24.83805,11.52714,25.33749), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.05444,24.83805,11.52714,25.33749), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.05444,24.83805,11.52714,25.33749), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.99777,25.18,11.52714,23.82972), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.99777,25.18,11.52714,23.82972), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.99777,25.18,11.52714,23.82972), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.99777,25.18,11.52714,23.82972), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.02666,25.33749,11.52714,24.83805), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.02666,25.33749,11.52714,24.83805), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.02666,25.33749,11.52714,24.83805), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.02666,25.33749,11.52714,24.83805), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.39833,26.15332,11.52714,26.18277), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.39833,26.15332,11.52714,26.18277), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.39833,26.15332,11.52714,26.18277), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.39833,26.15332,11.52714,26.18277), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.39305,26.18277,11.52714,26.15332), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.39305,26.18277,11.52714,26.15332), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.39305,26.18277,11.52714,26.15332), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.39305,26.18277,11.52714,26.15332), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.43,26.23305,11.52714,26.15332), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.43,26.23305,11.52714,26.15332), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.43,26.23305,11.52714,26.15332), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.43,26.23305,11.52714,26.15332), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.49944,26.35749,23.99956,30.23606), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.49944,26.35749,23.99956,30.23606), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.49944,26.35749,23.99956,30.23606), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.49944,26.35749,23.99956,30.23606), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.63861,26.41555,23.99956,29.80694), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.63861,26.41555,23.99956,29.80694), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.63861,26.41555,23.99956,29.80694), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.63861,26.41555,23.99956,29.80694), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.87638,26.52888,23.99956,28.85916), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.87638,26.52888,23.99956,28.85916), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.87638,26.52888,23.99956,28.85916), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.87638,26.52888,23.99956,28.85916), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.99944,26.53944,11.52714,19.99916), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.99944,26.53944,11.52714,19.99916), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.99944,26.53944,11.52714,19.99916), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.99944,26.53944,11.52714,19.99916), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.93055,26.85971,23.99956,27.8686), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.93055,26.85971,23.99956,27.8686), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.93055,26.85971,23.99956,27.8686), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.93055,26.85971,23.99956,27.8686), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.84833,26.9086,23.99956,27.50805), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.84833,26.9086,23.99956,27.50805), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.84833,26.9086,23.99956,27.50805), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.84833,26.9086,23.99956,27.50805), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.73499,27.32388,23.99956,29.43333), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.73499,27.32388,23.99956,29.43333), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.73499,27.32388,23.99956,29.43333), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.73499,27.32388,23.99956,29.43333), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.83916,27.50805,23.99956,26.9086), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.83916,27.50805,23.99956,26.9086), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.83916,27.50805,23.99956,26.9086), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.83916,27.50805,23.99956,26.9086), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.81388,27.57638,23.99956,28.27055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.81388,27.57638,23.99956,28.27055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.81388,27.57638,23.99956,28.27055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.81388,27.57638,23.99956,28.27055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.88111,27.62111,23.99956,30.34749), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.88111,27.62111,23.99956,30.34749), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.88111,27.62111,23.99956,30.34749), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.88111,27.62111,23.99956,30.34749), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.95417,27.8686,23.99956,26.85971), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.95417,27.8686,23.99956,26.85971), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.95417,27.8686,23.99956,26.85971), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.95417,27.8686,23.99956,26.85971), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.00222,27.89888,11.52714,22.47582), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.00222,27.89888,11.52714,22.47582), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.00222,27.89888,11.52714,22.47582), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.00222,27.89888,11.52714,22.47582), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.79027,28.27055,23.99956,27.57638), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.79027,28.27055,23.99956,27.57638), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.79027,28.27055,23.99956,27.57638), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.79027,28.27055,23.99956,27.57638), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.87333,28.85916,23.99956,26.52888), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.87333,28.85916,23.99956,26.52888), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.87333,28.85916,23.99956,26.52888), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.87333,28.85916,23.99956,26.52888), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.99777,29.24888,11.52714,23.82972), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.99777,29.24888,11.52714,23.82972), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.99777,29.24888,11.52714,23.82972), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.99777,29.24888,11.52714,23.82972), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.76472,29.43333,23.99956,28.27055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.76472,29.43333,23.99956,28.27055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.76472,29.43333,23.99956,28.27055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.76472,29.43333,23.99956,28.27055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.8761,29.51194,23.99956,31.38388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.8761,29.51194,23.99956,31.38388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.8761,29.51194,23.99956,31.38388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.8761,29.51194,23.99956,31.38388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.88694,29.66388,23.99956,29.51194), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.88694,29.66388,23.99956,29.51194), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.88694,29.66388,23.99956,29.51194), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.88694,29.66388,23.99956,29.51194), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.81721,29.77416,23.99956,29.88666), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.81721,29.77416,23.99956,29.88666), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.81721,29.77416,23.99956,29.88666), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.81721,29.77416,23.99956,29.88666), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.56666,29.80694,23.99956,30.23606), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.56666,29.80694,23.99956,30.23606), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.56666,29.80694,23.99956,30.23606), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.56666,29.80694,23.99956,30.23606), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.83138,29.88666,23.99956,29.77416), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.83138,29.88666,23.99956,29.77416), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.83138,29.88666,23.99956,29.77416), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.83138,29.88666,23.99956,29.77416), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.30389,30.12249,11.52714,26.18277), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.30389,30.12249,11.52714,26.18277), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.30389,30.12249,11.52714,26.18277), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.30389,30.12249,11.52714,26.18277), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.70638,30.1561,23.99956,30.23388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.70638,30.1561,23.99956,30.23388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.70638,30.1561,23.99956,30.23388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.70638,30.1561,23.99956,30.23388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.72694,30.23388,23.99956,30.1561), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.72694,30.23388,23.99956,30.1561), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.72694,30.23388,23.99956,30.1561), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.72694,30.23388,23.99956,30.1561), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.53202,30.23606,23.99956,26.35749), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.04666,30.26527,23.99956,30.28638), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.04666,30.26527,23.99956,30.28638), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.04666,30.26527,23.99956,30.28638), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.04666,30.26527,23.99956,30.28638), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((18.93194,30.28638,23.99956,30.26527), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((18.93194,30.28638,23.99956,30.26527), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((18.93194,30.28638,23.99956,30.26527), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((18.93194,30.28638,23.99956,30.26527), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.35,30.29666,23.99956,30.26527), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.35,30.29666,23.99956,30.26527), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.35,30.29666,23.99956,30.26527), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.35,30.29666,23.99956,30.26527), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((9.88222,30.34749,23.99956,27.62111), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((9.88222,30.34749,23.99956,27.62111), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((9.88222,30.34749,23.99956,27.62111), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((9.88222,30.34749,23.99956,27.62111), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.70194,30.48166,23.99956,31.7575), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.70194,30.48166,23.99956,31.7575), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.70194,30.48166,23.99956,31.7575), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.70194,30.48166,23.99956,31.7575), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.9286,30.5111,23.99956,31.42888), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.9286,30.5111,23.99956,31.42888), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.9286,30.5111,23.99956,31.42888), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.9286,30.5111,23.99956,31.42888), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((18.45888,30.56055,11.52714,22.34444), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((18.45888,30.56055,11.52714,22.34444), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((18.45888,30.56055,11.52714,22.34444), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((18.45888,30.56055,11.52714,22.34444), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.21527,30.7361,11.52714,24.75111), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.21527,30.7361,11.52714,24.75111), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.21527,30.7361,11.52714,24.75111), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.21527,30.7361,11.52714,24.75111), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.0186,30.79305,23.99956,31.85138), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.0186,30.79305,23.99956,31.85138), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.0186,30.79305,23.99956,31.85138), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.0186,30.79305,23.99956,31.85138), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((18.04194,30.82277,23.99956,30.86388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((18.04194,30.82277,23.99956,30.86388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((18.04194,30.82277,23.99956,30.86388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((18.04194,30.82277,23.99956,30.86388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.06055,30.85527,23.99956,32.19138), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.06055,30.85527,23.99956,32.19138), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.06055,30.85527,23.99956,32.19138), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.06055,30.85527,23.99956,32.19138), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((17.89639,30.86388,23.99956,30.92249), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((17.89639,30.86388,23.99956,30.92249), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((17.89639,30.86388,23.99956,30.92249), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((17.89639,30.86388,23.99956,30.92249), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.29055,30.90666,23.99956,31.71305), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.29055,30.90666,23.99956,31.71305), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.29055,30.90666,23.99956,31.71305), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.29055,30.90666,23.99956,31.71305), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((17.85472,30.92249,23.99956,30.86388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((17.85472,30.92249,23.99956,30.86388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((17.85472,30.92249,23.99956,30.86388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((17.85472,30.92249,23.99956,30.86388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((17.46083,31.02638,23.99956,31.08166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((17.46083,31.02638,23.99956,31.08166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((17.46083,31.02638,23.99956,31.08166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((17.46083,31.02638,23.99956,31.08166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((17.37083,31.08166,23.99956,31.02638), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((17.37083,31.08166,23.99956,31.02638), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((17.37083,31.08166,23.99956,31.02638), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((17.37083,31.08166,23.99956,31.02638), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.15499,31.08666,23.99956,31.21027), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.15499,31.08666,23.99956,31.21027), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.15499,31.08666,23.99956,31.21027), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.15499,31.08666,23.99956,31.21027), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((17.06777,31.13805,23.99956,31.08166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((17.06777,31.13805,23.99956,31.08166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((17.06777,31.13805,23.99956,31.08166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((17.06777,31.13805,23.99956,31.08166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.86888,31.16194,23.99956,31.38388), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.86888,31.16194,23.99956,31.38388), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.86888,31.16194,23.99956,31.38388), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.86888,31.16194,23.99956,31.38388), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.14333,31.21027,23.99956,31.08666), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.14333,31.21027,23.99956,31.08666), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.14333,31.21027,23.99956,31.08666), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.14333,31.21027,23.99956,31.08666), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((16.70583,31.22805,23.99956,31.13805), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((16.70583,31.22805,23.99956,31.13805), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((16.70583,31.22805,23.99956,31.13805), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((16.70583,31.22805,23.99956,31.13805), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((16.26277,31.23027,23.99956,31.2761), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((16.26277,31.23027,23.99956,31.2761), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((16.26277,31.23027,23.99956,31.2761), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((16.26277,31.23027,23.99956,31.2761), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((16.02972,31.2761,11.52714,23.45055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((16.02972,31.2761,11.52714,23.45055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((16.02972,31.2761,11.52714,23.45055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((16.02972,31.2761,11.52714,23.45055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.87471,31.38388,23.99956,29.51194), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.87471,31.38388,23.99956,29.51194), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.87471,31.38388,23.99956,29.51194), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.87471,31.38388,23.99956,29.51194), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.70333,31.42055,23.99956,31.65138), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.70333,31.42055,23.99956,31.65138), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.70333,31.42055,23.99956,31.65138), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.70333,31.42055,23.99956,31.65138), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.12333,31.42249,23.99956,31.50999), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.12333,31.42249,23.99956,31.50999), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.12333,31.42249,23.99956,31.50999), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.12333,31.42249,23.99956,31.50999), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.9486,31.42888,23.99956,30.5111), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.9486,31.42888,23.99956,30.5111), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.9486,31.42888,23.99956,30.5111), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.9486,31.42888,23.99956,30.5111), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.98083,31.4875,23.99956,31.98499), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.98083,31.4875,23.99956,31.98499), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.98083,31.4875,23.99956,31.98499), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.98083,31.4875,23.99956,31.98499), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.13944,31.50999,23.99956,31.42249), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.13944,31.50999,23.99956,31.42249), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.13944,31.50999,23.99956,31.42249), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.13944,31.50999,23.99956,31.42249), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.06972,31.58166,23.99956,31.81166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.06972,31.58166,23.99956,31.81166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.06972,31.58166,23.99956,31.81166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.06972,31.58166,23.99956,31.81166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.13423,31.63933,23.99956,31.58166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.13423,31.63933,23.99956,31.58166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.13423,31.63933,23.99956,31.58166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.13423,31.63933,23.99956,31.58166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.49389,31.65138,23.99956,32.15665), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.49389,31.65138,23.99956,32.15665), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.49389,31.65138,23.99956,32.15665), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.49389,31.65138,23.99956,32.15665), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.30639,31.71305,23.99956,30.90666), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.30639,31.71305,23.99956,30.90666), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.30639,31.71305,23.99956,30.90666), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.30639,31.71305,23.99956,30.90666), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.46667,31.72027,11.52714,24.47805), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.46667,31.72027,11.52714,24.47805), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.46667,31.72027,11.52714,24.47805), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.46667,31.72027,11.52714,24.47805), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.38,31.73055,11.52714,24.47805), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.38,31.73055,11.52714,24.47805), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.38,31.73055,11.52714,24.47805), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.38,31.73055,11.52714,24.47805), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.91944,31.7575,23.99956,31.98499), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.91944,31.7575,23.99956,31.98499), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.91944,31.7575,23.99956,31.98499), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.91944,31.7575,23.99956,31.98499), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.05472,31.81166,23.99956,31.58166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.05472,31.81166,23.99956,31.58166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.05472,31.81166,23.99956,31.58166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.05472,31.81166,23.99956,31.58166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.02222,31.85138,23.99956,30.79305), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.02222,31.85138,23.99956,30.79305), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.02222,31.85138,23.99956,30.79305), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.02222,31.85138,23.99956,30.79305), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.61361,31.85638,23.99956,31.97055), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.61361,31.85638,23.99956,31.97055), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.61361,31.85638,23.99956,31.97055), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.61361,31.85638,23.99956,31.97055), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((25.03611,31.92444,23.99956,31.85138), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((25.03611,31.92444,23.99956,31.85138), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((25.03611,31.92444,23.99956,31.85138), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((25.03611,31.92444,23.99956,31.85138), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.98,31.96777,11.52714,25.18), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.98,31.96777,11.52714,25.18), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.98,31.96777,11.52714,25.18), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.98,31.96777,11.52714,25.18), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.35777,31.96833,23.99956,32.15665), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.35777,31.96833,23.99956,32.15665), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.35777,31.96833,23.99956,32.15665), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.35777,31.96833,23.99956,32.15665), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.62472,31.97055,23.99956,31.85638), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.62472,31.97055,23.99956,31.85638), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.62472,31.97055,23.99956,31.85638), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.62472,31.97055,23.99956,31.85638), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((19.9525,31.98499,23.99956,31.4875), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((19.9525,31.98499,23.99956,31.4875), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((19.9525,31.98499,23.99956,31.4875), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((19.9525,31.98499,23.99956,31.4875), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.48277,31.99027,11.52714,19.99582), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.48277,31.99027,11.52714,19.99582), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.48277,31.99027,11.52714,19.99582), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.48277,31.99027,11.52714,19.99582), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.78722,32.00888,11.52714,24.56721), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.78722,32.00888,11.52714,24.56721), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.78722,32.00888,11.52714,24.56721), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.78722,32.00888,11.52714,24.56721), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((24.66305,32.02693,23.99956,30.1561), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((24.66305,32.02693,23.99956,30.1561), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((24.66305,32.02693,23.99956,30.1561), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((24.66305,32.02693,23.99956,30.1561), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.99305,32.04722,23.99956,32.09249), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.99305,32.04722,23.99956,32.09249), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.99305,32.04722,23.99956,32.09249), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.99305,32.04722,23.99956,32.09249), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.99611,32.09249,23.99956,32.04722), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.99611,32.09249,23.99956,32.04722), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.99611,32.09249,23.99956,32.04722), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.99611,32.09249,23.99956,32.04722), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((10.8825,32.13915,23.99956,32.00888), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((10.8825,32.13915,23.99956,32.00888), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((10.8825,32.13915,23.99956,32.00888), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((10.8825,32.13915,23.99956,32.00888), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.37,32.15665,23.99956,31.96833), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.37,32.15665,23.99956,31.96833), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.37,32.15665,23.99956,31.96833), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.37,32.15665,23.99956,31.96833), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.73333,32.17304,23.99956,32.04722), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.73333,32.17304,23.99956,32.04722), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.73333,32.17304,23.99956,32.04722), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.73333,32.17304,23.99956,32.04722), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.09249,32.19138,23.99956,30.85527), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.09249,32.19138,23.99956,30.85527), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.09249,32.19138,23.99956,30.85527), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.09249,32.19138,23.99956,30.85527), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.25833,32.21027,23.99956,32.2186), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.25833,32.21027,23.99956,32.2186), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.25833,32.21027,23.99956,32.2186), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.25833,32.21027,23.99956,32.2186), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.30777,32.2186,23.99956,32.21027), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.30777,32.2186,23.99956,32.21027), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.30777,32.2186,23.99956,32.21027), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.30777,32.2186,23.99956,32.21027), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.08388,32.32832,23.99956,32.56332), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.08388,32.32832,23.99956,32.56332), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.08388,32.32832,23.99956,32.56332), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.08388,32.32832,23.99956,32.56332), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.21222,32.36888,23.99956,32.40443), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.21222,32.36888,23.99956,32.40443), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.21222,32.36888,23.99956,32.40443), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.21222,32.36888,23.99956,32.40443), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((15.1475,32.40443,23.99956,32.36888), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((15.1475,32.40443,23.99956,32.36888), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((15.1475,32.40443,23.99956,32.36888), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((15.1475,32.40443,23.99956,32.36888), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.11055,32.40749,23.99956,32.56332), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.11055,32.40749,23.99956,32.56332), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.11055,32.40749,23.99956,32.56332), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.11055,32.40749,23.99956,32.56332), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.51667,32.40942,23.99956,33.16578), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.51667,32.40942,23.99956,33.16578), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.51667,32.40942,23.99956,33.16578), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.51667,32.40942,23.99956,33.16578), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.5675,32.44221,11.52714,24.30249), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.5675,32.44221,11.52714,24.30249), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.5675,32.44221,11.52714,24.30249), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.5675,32.44221,11.52714,24.30249), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.14444,32.46027,23.99956,32.62221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.14444,32.46027,23.99956,32.62221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.14444,32.46027,23.99956,32.62221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.14444,32.46027,23.99956,32.62221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.58333,32.49082,23.99956,32.44221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.58333,32.49082,23.99956,32.44221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.58333,32.49082,23.99956,32.44221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.58333,32.49082,23.99956,32.44221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.49749,32.51166,23.99956,31.08666), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.49749,32.51166,23.99956,31.08666), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.49749,32.51166,23.99956,31.08666), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.49749,32.51166,23.99956,31.08666), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((14.46833,32.51638,11.52714,22.61416), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((14.46833,32.51638,11.52714,22.61416), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((14.46833,32.51638,11.52714,22.61416), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((14.46833,32.51638,11.52714,22.61416), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.09638,32.56332,23.99956,32.32832), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.09638,32.56332,23.99956,32.32832), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.09638,32.56332,23.99956,32.32832), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.09638,32.56332,23.99956,32.32832), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((23.12472,32.62221,23.99956,32.40749), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((23.12472,32.62221,23.99956,32.40749), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((23.12472,32.62221,23.99956,32.40749), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((23.12472,32.62221,23.99956,32.40749), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.47055,32.62776,23.99956,33.03499), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.47055,32.62776,23.99956,33.03499), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.47055,32.62776,23.99956,33.03499), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.47055,32.62776,23.99956,33.03499), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((14.20028,32.70388,11.52714,22.61416), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((14.20028,32.70388,11.52714,22.61416), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((14.20028,32.70388,11.52714,22.61416), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((14.20028,32.70388,11.52714,22.61416), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((20.99944,32.74638,23.99956,32.77999), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((20.99944,32.74638,23.99956,32.77999), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((20.99944,32.74638,23.99956,32.77999), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((20.99944,32.74638,23.99956,32.77999), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((21.37888,32.77999,11.52714,20.85221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((21.37888,32.77999,11.52714,20.85221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((21.37888,32.77999,11.52714,20.85221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((21.37888,32.77999,11.52714,20.85221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((22.57999,32.78277,23.99956,32.80221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((22.57999,32.78277,23.99956,32.80221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((22.57999,32.78277,23.99956,32.80221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((22.57999,32.78277,23.99956,32.80221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.62,32.78777,11.52714,23.13943), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.62,32.78777,11.52714,23.13943), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.62,32.78777,11.52714,23.13943), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.62,32.78777,11.52714,23.13943), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((12.61444,32.79832,23.99956,32.8336), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((12.61444,32.79832,23.99956,32.8336), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((12.61444,32.79832,23.99956,32.8336), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((12.61444,32.79832,23.99956,32.8336), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.79083,32.7986,23.99956,32.78777), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.79083,32.7986,23.99956,32.78777), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.79083,32.7986,23.99956,32.78777), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.79083,32.7986,23.99956,32.78777), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((22.48472,32.80221,23.99956,32.78277), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((22.48472,32.80221,23.99956,32.78277), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((22.48472,32.80221,23.99956,32.78277), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((22.48472,32.80221,23.99956,32.78277), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((12.31694,32.8336,23.99956,32.79832), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((12.31694,32.8336,23.99956,32.79832), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((12.31694,32.8336,23.99956,32.79832), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((12.31694,32.8336,23.99956,32.79832), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((22.36166,32.87444,23.99956,32.80221), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((22.36166,32.87444,23.99956,32.80221), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((22.36166,32.87444,23.99956,32.80221), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((22.36166,32.87444,23.99956,32.80221), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((22.20944,32.88026,23.99956,32.94027), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((22.20944,32.88026,23.99956,32.94027), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((22.20944,32.88026,23.99956,32.94027), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((22.20944,32.88026,23.99956,32.94027), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.35416,32.89971,11.52714,23.21471), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.35416,32.89971,11.52714,23.21471), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.35416,32.89971,11.52714,23.21471), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.35416,32.89971,11.52714,23.21471), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((21.87749,32.90166,23.99956,32.93555), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((21.87749,32.90166,23.99956,32.93555), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((21.87749,32.90166,23.99956,32.93555), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((21.87749,32.90166,23.99956,32.93555), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((13.24305,32.91916,23.99956,32.89971), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((13.24305,32.91916,23.99956,32.89971), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((13.24305,32.91916,23.99956,32.89971), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((13.24305,32.91916,23.99956,32.89971), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((21.64138,32.93555,23.99956,32.90166), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((21.64138,32.93555,23.99956,32.90166), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((21.64138,32.93555,23.99956,32.90166), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((21.64138,32.93555,23.99956,32.90166), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((22.16055,32.94027,23.99956,32.88026), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((22.16055,32.94027,23.99956,32.88026), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((22.16055,32.94027,23.99956,32.88026), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((22.16055,32.94027,23.99956,32.88026), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.49166,33.03499,23.99956,32.62776), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.49166,33.03499,23.99956,32.62776), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.49166,33.03499,23.99956,32.62776), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.49166,33.03499,23.99956,32.62776), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.80917,33.08415,23.99956,33.09943), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.80917,33.08415,23.99956,33.09943), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.80917,33.08415,23.99956,33.09943), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.80917,33.08415,23.99956,33.09943), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.64361,33.09943,23.99956,32.49082), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.64361,33.09943,23.99956,32.49082), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.64361,33.09943,23.99956,32.49082), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.64361,33.09943,23.99956,32.49082), mapfile, tile_dir, 17, 17, "ly-libya")
	render_tiles((11.52714,33.16578,23.99956,32.40942), mapfile, tile_dir, 0, 11, "ly-libya")
	render_tiles((11.52714,33.16578,23.99956,32.40942), mapfile, tile_dir, 13, 13, "ly-libya")
	render_tiles((11.52714,33.16578,23.99956,32.40942), mapfile, tile_dir, 15, 15, "ly-libya")
	render_tiles((11.52714,33.16578,23.99956,32.40942), mapfile, tile_dir, 17, 17, "ly-libya")