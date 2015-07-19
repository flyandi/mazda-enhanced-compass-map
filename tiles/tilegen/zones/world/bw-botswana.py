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
    # Region: BW
    # Region Name: Botswana

	render_tiles((20.69234,-26.89468,25.26575,-26.38612), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.69234,-26.89468,25.26575,-26.38612), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.69234,-26.89468,25.26575,-26.38612), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.69234,-26.89468,25.26575,-26.38612), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.14416,-26.86667,20.69234,-18.31741), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.14416,-26.86667,20.69234,-18.31741), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.14416,-26.86667,20.69234,-18.31741), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.14416,-26.86667,20.69234,-18.31741), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.52361,-26.85334,20.69234,-18.30445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.52361,-26.85334,20.69234,-18.30445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.52361,-26.85334,20.69234,-18.30445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.52361,-26.85334,20.69234,-18.30445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.69305,-26.85305,25.26575,-26.69195), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.69305,-26.85305,25.26575,-26.69195), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.69305,-26.85305,25.26575,-26.69195), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.69305,-26.85305,25.26575,-26.69195), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.3486,-26.8275,20.69234,-18.30445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.3486,-26.8275,20.69234,-18.30445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.3486,-26.8275,20.69234,-18.30445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.3486,-26.8275,20.69234,-18.30445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.63694,-26.81278,25.26575,-25.51889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.63694,-26.81278,25.26575,-25.51889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.63694,-26.81278,25.26575,-25.51889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.63694,-26.81278,25.26575,-25.51889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.89721,-26.79473,25.26575,-26.13361), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.89721,-26.79473,25.26575,-26.13361), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.89721,-26.79473,25.26575,-26.13361), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.89721,-26.79473,25.26575,-26.13361), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.77861,-26.77084,25.26575,-26.67167), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.77861,-26.77084,25.26575,-26.67167), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.77861,-26.77084,25.26575,-26.67167), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.77861,-26.77084,25.26575,-26.67167), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.75999,-26.69195,25.26575,-26.77084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.75999,-26.69195,25.26575,-26.77084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.75999,-26.69195,25.26575,-26.77084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.75999,-26.69195,25.26575,-26.77084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.79138,-26.67167,25.26575,-26.77084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.79138,-26.67167,25.26575,-26.77084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.79138,-26.67167,25.26575,-26.77084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.79138,-26.67167,25.26575,-26.77084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.89888,-26.66833,25.26575,-26.67167), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.89888,-26.66833,25.26575,-26.67167), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.89888,-26.66833,25.26575,-26.67167), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.89888,-26.66833,25.26575,-26.67167), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.06721,-26.61473,25.26575,-26.42778), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.06721,-26.61473,25.26575,-26.42778), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.06721,-26.61473,25.26575,-26.42778), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.06721,-26.61473,25.26575,-26.42778), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.61138,-26.44917,25.26575,-25.51889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.61138,-26.44917,25.26575,-25.51889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.61138,-26.44917,25.26575,-25.51889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.61138,-26.44917,25.26575,-25.51889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.17944,-26.42778,25.26575,-26.61473), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.17944,-26.42778,25.26575,-26.61473), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.17944,-26.42778,25.26575,-26.61473), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.17944,-26.42778,25.26575,-26.61473), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.69333,-26.38612,25.26575,-26.89468), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.69333,-26.38612,25.26575,-26.89468), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.69333,-26.38612,25.26575,-26.89468), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.69333,-26.38612,25.26575,-26.89468), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.46194,-26.215,25.26575,-25.99916), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.46194,-26.215,25.26575,-25.99916), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.46194,-26.215,25.26575,-25.99916), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.46194,-26.215,25.26575,-25.99916), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.86082,-26.13361,25.26575,-26.79473), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.86082,-26.13361,25.26575,-26.79473), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.86082,-26.13361,25.26575,-26.79473), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.86082,-26.13361,25.26575,-26.79473), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.71277,-25.99916,25.26575,-25.93723), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.71277,-25.99916,25.26575,-25.93723), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.71277,-25.99916,25.26575,-25.93723), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.71277,-25.99916,25.26575,-25.93723), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.72249,-25.93723,25.26575,-25.78889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.72249,-25.93723,25.26575,-25.78889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.72249,-25.93723,25.26575,-25.78889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.72249,-25.93723,25.26575,-25.78889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.80777,-25.83084,20.69234,-17.84056), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.80777,-25.83084,20.69234,-17.84056), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.80777,-25.83084,20.69234,-17.84056), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.80777,-25.83084,20.69234,-17.84056), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.76916,-25.82306,25.26575,-25.78889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.76916,-25.82306,25.26575,-25.78889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.76916,-25.82306,25.26575,-25.78889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.76916,-25.82306,25.26575,-25.78889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.73027,-25.78889,25.26575,-25.93723), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.73027,-25.78889,25.26575,-25.93723), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.73027,-25.78889,25.26575,-25.93723), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.73027,-25.78889,25.26575,-25.93723), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.75666,-25.78195,25.26575,-26.38612), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.75666,-25.78195,25.26575,-26.38612), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.75666,-25.78195,25.26575,-26.38612), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.75666,-25.78195,25.26575,-26.38612), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.50444,-25.76306,20.69234,-18.05945), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.50444,-25.76306,20.69234,-18.05945), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.50444,-25.76306,20.69234,-18.05945), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.50444,-25.76306,20.69234,-18.05945), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.39333,-25.76278,20.69234,-17.94667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.39333,-25.76278,20.69234,-17.94667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.39333,-25.76278,20.69234,-17.94667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.39333,-25.76278,20.69234,-17.94667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.15221,-25.76222,20.69234,-17.89445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.15221,-25.76222,20.69234,-17.89445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.15221,-25.76222,20.69234,-17.89445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.15221,-25.76222,20.69234,-17.89445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.38638,-25.74639,20.69234,-18.12251), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.38638,-25.74639,20.69234,-18.12251), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.38638,-25.74639,20.69234,-18.12251), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.38638,-25.74639,20.69234,-18.12251), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.4461,-25.73556,20.69234,-18.00806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.4461,-25.73556,20.69234,-18.00806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.4461,-25.73556,20.69234,-18.00806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.4461,-25.73556,20.69234,-18.00806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.03694,-25.72861,25.26575,-25.76222), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.03694,-25.72861,25.26575,-25.76222), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.03694,-25.72861,25.26575,-25.76222), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.03694,-25.72861,25.26575,-25.76222), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.26611,-25.69667,20.69234,-17.94667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.26611,-25.69667,20.69234,-17.94667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.26611,-25.69667,20.69234,-17.94667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.26611,-25.69667,20.69234,-17.94667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.67971,-25.67945,25.26575,-25.45916), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.67971,-25.67945,25.26575,-25.45916), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.67971,-25.67945,25.26575,-25.45916), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.67971,-25.67945,25.26575,-25.45916), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.81888,-25.66806,25.26575,-25.56389), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.81888,-25.66806,25.26575,-25.56389), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.81888,-25.66806,25.26575,-25.56389), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.81888,-25.66806,25.26575,-25.56389), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.00027,-25.65889,25.26575,-25.62251), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.00027,-25.65889,25.26575,-25.62251), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.00027,-25.65889,25.26575,-25.62251), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.00027,-25.65889,25.26575,-25.62251), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.00722,-25.62251,25.26575,-25.65889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.00722,-25.62251,25.26575,-25.65889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.00722,-25.62251,25.26575,-25.65889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.00722,-25.62251,25.26575,-25.65889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.15027,-25.62167,25.26575,-25.69667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.15027,-25.62167,25.26575,-25.69667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.15027,-25.62167,25.26575,-25.69667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.15027,-25.62167,25.26575,-25.69667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.58709,-25.62104,20.69234,-18.53112), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.58709,-25.62104,20.69234,-18.53112), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.58709,-25.62104,20.69234,-18.53112), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.58709,-25.62104,20.69234,-18.53112), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.68777,-25.57889,25.26575,-26.89468), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.68777,-25.57889,25.26575,-26.89468), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.68777,-25.57889,25.26575,-26.89468), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.68777,-25.57889,25.26575,-26.89468), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.84416,-25.57417,20.69234,-18.32251), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.84416,-25.57417,20.69234,-18.32251), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.84416,-25.57417,20.69234,-18.32251), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.84416,-25.57417,20.69234,-18.32251), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.81305,-25.56389,25.26575,-25.66806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.81305,-25.56389,25.26575,-25.66806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.81305,-25.56389,25.26575,-25.66806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.81305,-25.56389,25.26575,-25.66806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.63194,-25.51889,25.26575,-26.81278), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.63194,-25.51889,25.26575,-26.81278), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.63194,-25.51889,25.26575,-26.81278), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.63194,-25.51889,25.26575,-26.81278), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.73444,-25.46222,20.69234,-18.32251), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.73444,-25.46222,20.69234,-18.32251), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.73444,-25.46222,20.69234,-18.32251), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.73444,-25.46222,20.69234,-18.32251), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.67332,-25.45916,25.26575,-25.67945), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.67332,-25.45916,25.26575,-25.67945), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.67332,-25.45916,25.26575,-25.67945), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.67332,-25.45916,25.26575,-25.67945), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((22.92638,-25.38389,25.26575,-25.66806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((22.92638,-25.38389,25.26575,-25.66806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((22.92638,-25.38389,25.26575,-25.66806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((22.92638,-25.38389,25.26575,-25.66806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.55555,-25.38,25.26575,-26.44917), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.55555,-25.38,25.26575,-26.44917), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.55555,-25.38,25.26575,-26.44917), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.55555,-25.38,25.26575,-26.44917), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.49638,-25.33389,20.69234,-18.22639), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.49638,-25.33389,20.69234,-18.22639), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.49638,-25.33389,20.69234,-18.22639), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.49638,-25.33389,20.69234,-18.22639), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.10471,-25.30028,25.26575,-25.38389), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.10471,-25.30028,25.26575,-25.38389), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.10471,-25.30028,25.26575,-25.38389), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.10471,-25.30028,25.26575,-25.38389), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.45832,-25.27834,20.69234,-18.22639), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.45832,-25.27834,20.69234,-18.22639), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.45832,-25.27834,20.69234,-18.22639), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.45832,-25.27834,20.69234,-18.22639), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.38999,-25.03695,25.26575,-25.38), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.38999,-25.03695,25.26575,-25.38), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.38999,-25.03695,25.26575,-25.38), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.38999,-25.03695,25.26575,-25.38), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.83221,-25.02806,20.69234,-18.77695), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.83221,-25.02806,20.69234,-18.77695), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.83221,-25.02806,20.69234,-18.77695), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.83221,-25.02806,20.69234,-18.77695), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.17499,-24.89223,25.26575,-24.76363), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.17499,-24.89223,25.26575,-24.76363), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.17499,-24.89223,25.26575,-24.76363), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.17499,-24.89223,25.26575,-24.76363), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.00141,-24.76363,25.26575,-23.42667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.00141,-24.76363,25.26575,-23.42667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.00141,-24.76363,25.26575,-23.42667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.00141,-24.76363,25.26575,-23.42667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.86595,-24.73866,25.26575,-25.02806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.86595,-24.73866,25.26575,-25.02806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.86595,-24.73866,25.26575,-25.02806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.86595,-24.73866,25.26575,-25.02806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.40288,-24.63044,20.69234,-19.67583), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.40288,-24.63044,20.69234,-19.67583), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.40288,-24.63044,20.69234,-19.67583), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.40288,-24.63044,20.69234,-19.67583), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.35999,-24.61889,20.69234,-19.59723), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.35999,-24.61889,20.69234,-19.59723), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.35999,-24.61889,20.69234,-19.59723), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.35999,-24.61889,20.69234,-19.59723), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.46471,-24.58056,20.69234,-19.73111), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.46471,-24.58056,20.69234,-19.73111), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.46471,-24.58056,20.69234,-19.73111), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.46471,-24.58056,20.69234,-19.73111), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.55499,-24.43694,20.69234,-19.79917), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.55499,-24.43694,20.69234,-19.79917), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.55499,-24.43694,20.69234,-19.79917), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.55499,-24.43694,20.69234,-19.79917), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.84527,-24.26445,25.26575,-23.75362), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.84527,-24.26445,25.26575,-23.75362), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.84527,-24.26445,25.26575,-23.75362), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.84527,-24.26445,25.26575,-23.75362), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.95943,-23.75362,20.69234,-20.00973), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.95943,-23.75362,20.69234,-20.00973), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.95943,-23.75362,20.69234,-20.00973), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.95943,-23.75362,20.69234,-20.00973), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.06721,-23.66083,25.26575,-23.65472), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.06721,-23.66083,25.26575,-23.65472), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.06721,-23.66083,25.26575,-23.65472), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.06721,-23.66083,25.26575,-23.65472), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.03249,-23.65472,20.69234,-20.00028), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.03249,-23.65472,20.69234,-20.00028), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.03249,-23.65472,20.69234,-20.00028), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.03249,-23.65472,20.69234,-20.00028), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.99916,-23.64861,20.69234,-20.00028), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.99916,-23.64861,20.69234,-20.00028), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.99916,-23.64861,20.69234,-20.00028), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.99916,-23.64861,20.69234,-20.00028), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.10277,-23.57667,25.26575,-23.66083), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.10277,-23.57667,25.26575,-23.66083), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.10277,-23.57667,25.26575,-23.66083), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.10277,-23.57667,25.26575,-23.66083), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((19.99888,-23.42667,20.69234,-22.07389), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((19.99888,-23.42667,20.69234,-22.07389), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((19.99888,-23.42667,20.69234,-22.07389), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((19.99888,-23.42667,20.69234,-22.07389), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.39582,-23.39084,20.69234,-20.465), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.39582,-23.39084,20.69234,-20.465), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.39582,-23.39084,20.69234,-20.465), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.39582,-23.39084,20.69234,-20.465), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.52055,-23.38361,25.26575,-23.28945), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.52055,-23.38361,25.26575,-23.28945), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.52055,-23.38361,25.26575,-23.28945), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.52055,-23.38361,25.26575,-23.28945), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.57277,-23.28945,25.26575,-23.22028), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.57277,-23.28945,25.26575,-23.22028), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.57277,-23.28945,25.26575,-23.22028), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.57277,-23.28945,25.26575,-23.22028), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.73693,-23.23,20.69234,-21.14556), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.73693,-23.23,20.69234,-21.14556), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.73693,-23.23,20.69234,-21.14556), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.73693,-23.23,20.69234,-21.14556), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.60138,-23.22028,25.26575,-23.28945), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.60138,-23.22028,25.26575,-23.28945), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.60138,-23.22028,25.26575,-23.28945), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.60138,-23.22028,25.26575,-23.28945), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.78611,-23.16528,25.26575,-23.10917), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.78611,-23.16528,25.26575,-23.10917), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.78611,-23.16528,25.26575,-23.10917), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.78611,-23.16528,25.26575,-23.10917), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.81749,-23.10917,25.26575,-23.16528), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.81749,-23.10917,25.26575,-23.16528), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.81749,-23.10917,25.26575,-23.16528), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.81749,-23.10917,25.26575,-23.16528), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.92833,-23.05806,25.26575,-22.95556), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.92833,-23.05806,25.26575,-22.95556), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.92833,-23.05806,25.26575,-22.95556), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.92833,-23.05806,25.26575,-22.95556), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.9386,-22.95556,25.26575,-23.05806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.9386,-22.95556,25.26575,-23.05806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.9386,-22.95556,25.26575,-23.05806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.9386,-22.95556,25.26575,-23.05806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.02444,-22.92445,20.69234,-21.56195), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.02444,-22.92445,20.69234,-21.56195), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.02444,-22.92445,20.69234,-21.56195), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.02444,-22.92445,20.69234,-21.56195), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.05222,-22.88139,25.26575,-22.84111), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.05222,-22.88139,25.26575,-22.84111), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.05222,-22.88139,25.26575,-22.84111), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.05222,-22.88139,25.26575,-22.84111), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.04277,-22.84111,25.26575,-22.88139), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.04277,-22.84111,25.26575,-22.88139), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.04277,-22.84111,25.26575,-22.88139), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.04277,-22.84111,25.26575,-22.88139), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.14916,-22.78,25.26575,-22.68473), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.14916,-22.78,25.26575,-22.68473), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.14916,-22.78,25.26575,-22.68473), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.14916,-22.78,25.26575,-22.68473), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.18416,-22.68473,20.69234,-21.59667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.18416,-22.68473,20.69234,-21.59667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.18416,-22.68473,20.69234,-21.59667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.18416,-22.68473,20.69234,-21.59667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.43971,-22.57362,20.69234,-21.66083), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.43971,-22.57362,20.69234,-21.66083), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.43971,-22.57362,20.69234,-21.66083), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.43971,-22.57362,20.69234,-21.66083), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.63444,-22.56334,20.69234,-21.63111), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.63444,-22.56334,20.69234,-21.63111), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.63444,-22.56334,20.69234,-21.63111), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.63444,-22.56334,20.69234,-21.63111), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.91777,-22.45834,20.69234,-22.31334), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.91777,-22.45834,20.69234,-22.31334), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.91777,-22.45834,20.69234,-22.31334), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.91777,-22.45834,20.69234,-22.31334), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.96582,-22.38639,20.69234,-22.31334), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.96582,-22.38639,20.69234,-22.31334), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.96582,-22.38639,20.69234,-22.31334), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.96582,-22.38639,20.69234,-22.31334), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.96055,-22.31334,25.26575,-22.38639), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.96055,-22.31334,25.26575,-22.38639), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.96055,-22.31334,25.26575,-22.38639), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.96055,-22.31334,25.26575,-22.38639), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.08749,-22.22417,20.69234,-21.82512), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.08749,-22.22417,20.69234,-21.82512), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.08749,-22.22417,20.69234,-21.82512), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.08749,-22.22417,20.69234,-21.82512), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.37053,-22.19138,20.69234,-22.06652), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.37053,-22.19138,20.69234,-22.06652), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.37053,-22.19138,20.69234,-22.06652), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.37053,-22.19138,20.69234,-22.06652), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.15666,-22.07695,20.69234,-22.22417), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.15666,-22.07695,20.69234,-22.22417), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.15666,-22.07695,20.69234,-22.22417), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.15666,-22.07695,20.69234,-22.22417), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((19.99666,-22.07389,20.69234,-22.0014), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((19.99666,-22.07389,20.69234,-22.0014), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((19.99666,-22.07389,20.69234,-22.0014), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((19.99666,-22.07389,20.69234,-22.0014), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.25815,-22.06652,20.69234,-22.07695), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.25815,-22.06652,20.69234,-22.07695), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.25815,-22.06652,20.69234,-22.07695), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.25815,-22.06652,20.69234,-22.07695), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.07388,-22.03778,20.69234,-21.82084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.07388,-22.03778,20.69234,-21.82084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.07388,-22.03778,20.69234,-21.82084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.07388,-22.03778,20.69234,-21.82084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((19.99582,-22.0014,20.69234,-22.07389), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((19.99582,-22.0014,20.69234,-22.07389), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((19.99582,-22.0014,20.69234,-22.07389), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((19.99582,-22.0014,20.69234,-22.07389), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.99194,-21.99695,20.69234,-20.64722), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.99194,-21.99695,20.69234,-20.64722), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.99194,-21.99695,20.69234,-20.64722), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.99194,-21.99695,20.69234,-20.64722), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.03722,-21.97417,20.69234,-21.89473), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.03722,-21.97417,20.69234,-21.89473), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.03722,-21.97417,20.69234,-21.89473), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.03722,-21.97417,20.69234,-21.89473), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.03777,-21.89473,20.69234,-21.97417), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.03777,-21.89473,20.69234,-21.97417), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.03777,-21.89473,20.69234,-21.97417), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.03777,-21.89473,20.69234,-21.97417), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.08334,-21.82512,20.69234,-21.82084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.08334,-21.82512,20.69234,-21.82084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.08334,-21.82512,20.69234,-21.82084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.08334,-21.82512,20.69234,-21.82084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((29.08083,-21.82084,20.69234,-21.82512), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((29.08083,-21.82084,20.69234,-21.82512), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((29.08083,-21.82084,20.69234,-21.82512), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((29.08083,-21.82084,20.69234,-21.82512), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.49194,-21.66083,25.26575,-22.57362), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.49194,-21.66083,25.26575,-22.57362), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.49194,-21.66083,25.26575,-22.57362), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.49194,-21.66083,25.26575,-22.57362), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.56805,-21.63111,25.26575,-22.56334), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.56805,-21.63111,25.26575,-22.56334), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.56805,-21.63111,25.26575,-22.56334), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.56805,-21.63111,25.26575,-22.56334), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.35722,-21.60306,25.26575,-22.57362), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.35722,-21.60306,25.26575,-22.57362), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.35722,-21.60306,25.26575,-22.57362), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.35722,-21.60306,25.26575,-22.57362), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.20277,-21.59667,25.26575,-22.68473), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.20277,-21.59667,25.26575,-22.68473), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.20277,-21.59667,25.26575,-22.68473), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.20277,-21.59667,25.26575,-22.68473), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((28.01277,-21.56195,25.26575,-22.92445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((28.01277,-21.56195,25.26575,-22.92445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((28.01277,-21.56195,25.26575,-22.92445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((28.01277,-21.56195,25.26575,-22.92445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.9086,-21.31389,25.26575,-23.05806), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.9086,-21.31389,25.26575,-23.05806), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.9086,-21.31389,25.26575,-23.05806), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.9086,-21.31389,25.26575,-23.05806), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.73943,-21.14556,25.26575,-23.23), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.73943,-21.14556,25.26575,-23.23), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.73943,-21.14556,25.26575,-23.23), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.73943,-21.14556,25.26575,-23.23), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.68527,-21.06362,20.69234,-20.60667), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.68527,-21.06362,20.69234,-20.60667), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.68527,-21.06362,20.69234,-20.60667), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.68527,-21.06362,20.69234,-20.60667), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.71944,-20.81195,20.69234,-20.5675), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.71944,-20.81195,20.69234,-20.5675), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.71944,-20.81195,20.69234,-20.5675), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.71944,-20.81195,20.69234,-20.5675), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.99332,-20.64722,20.69234,-19.29972), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.99332,-20.64722,20.69234,-19.29972), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.99332,-20.64722,20.69234,-19.29972), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.99332,-20.64722,20.69234,-19.29972), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.70082,-20.60667,20.69234,-20.50986), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.70082,-20.60667,20.69234,-20.50986), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.70082,-20.60667,20.69234,-20.50986), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.70082,-20.60667,20.69234,-20.50986), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.72471,-20.5675,20.69234,-20.81195), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.72471,-20.5675,20.69234,-20.81195), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.72471,-20.5675,20.69234,-20.81195), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.72471,-20.5675,20.69234,-20.81195), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.71397,-20.50986,20.69234,-20.81195), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.71397,-20.50986,20.69234,-20.81195), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.71397,-20.50986,20.69234,-20.81195), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.71397,-20.50986,20.69234,-20.81195), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.28749,-20.49472,20.69234,-20.30251), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.28749,-20.49472,20.69234,-20.30251), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.28749,-20.49472,20.69234,-20.30251), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.28749,-20.49472,20.69234,-20.30251), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.35722,-20.465,25.26575,-23.39084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.35722,-20.465,25.26575,-23.39084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.35722,-20.465,25.26575,-23.39084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.35722,-20.465,25.26575,-23.39084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.29721,-20.30251,20.69234,-20.49472), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.29721,-20.30251,20.69234,-20.49472), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.29721,-20.30251,20.69234,-20.49472), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.29721,-20.30251,20.69234,-20.49472), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.21999,-20.09167,20.69234,-20.49472), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.21999,-20.09167,20.69234,-20.49472), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.21999,-20.09167,20.69234,-20.49472), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.21999,-20.09167,20.69234,-20.49472), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.96971,-20.00973,25.26575,-23.75362), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.96971,-20.00973,25.26575,-23.75362), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.96971,-20.00973,25.26575,-23.75362), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.96971,-20.00973,25.26575,-23.75362), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((27.02666,-20.00028,25.26575,-23.65472), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((27.02666,-20.00028,25.26575,-23.65472), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((27.02666,-20.00028,25.26575,-23.65472), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((27.02666,-20.00028,25.26575,-23.65472), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.61082,-19.85445,20.69234,-19.79917), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.61082,-19.85445,20.69234,-19.79917), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.61082,-19.85445,20.69234,-19.79917), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.61082,-19.85445,20.69234,-19.79917), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.5886,-19.79917,20.69234,-19.85445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.5886,-19.79917,20.69234,-19.85445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.5886,-19.79917,20.69234,-19.85445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.5886,-19.79917,20.69234,-19.85445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.4411,-19.73111,25.26575,-24.58056), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.4411,-19.73111,25.26575,-24.58056), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.4411,-19.73111,25.26575,-24.58056), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.4411,-19.73111,25.26575,-24.58056), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.40444,-19.67583,25.26575,-24.63044), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.40444,-19.67583,25.26575,-24.63044), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.40444,-19.67583,25.26575,-24.63044), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.40444,-19.67583,25.26575,-24.63044), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.32582,-19.65445,20.69234,-19.59723), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.32582,-19.65445,20.69234,-19.59723), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.32582,-19.65445,20.69234,-19.59723), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.32582,-19.65445,20.69234,-19.59723), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.34805,-19.59723,25.26575,-24.61889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.34805,-19.59723,25.26575,-24.61889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.34805,-19.59723,25.26575,-24.61889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.34805,-19.59723,25.26575,-24.61889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((26.19194,-19.54473,20.69234,-19.65445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((26.19194,-19.54473,20.69234,-19.65445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((26.19194,-19.54473,20.69234,-19.65445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((26.19194,-19.54473,20.69234,-19.65445), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.9936,-19.29972,20.69234,-20.64722), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.9936,-19.29972,20.69234,-20.64722), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.9936,-19.29972,20.69234,-20.64722), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.9936,-19.29972,20.69234,-20.64722), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.96194,-19.10028,20.69234,-18.94556), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.96194,-19.10028,20.69234,-18.94556), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.96194,-19.10028,20.69234,-18.94556), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.96194,-19.10028,20.69234,-18.94556), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.97332,-18.94556,20.69234,-19.10028), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.97332,-18.94556,20.69234,-19.10028), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.97332,-18.94556,20.69234,-19.10028), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.97332,-18.94556,20.69234,-19.10028), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.80833,-18.77695,20.69234,-18.68167), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.80833,-18.77695,20.69234,-18.68167), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.80833,-18.77695,20.69234,-18.68167), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.80833,-18.77695,20.69234,-18.68167), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.79749,-18.68167,20.69234,-18.77695), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.79749,-18.68167,20.69234,-18.77695), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.79749,-18.68167,20.69234,-18.77695), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.79749,-18.68167,20.69234,-18.77695), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.65944,-18.53112,25.26575,-25.62104), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.65944,-18.53112,25.26575,-25.62104), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.65944,-18.53112,25.26575,-25.62104), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.65944,-18.53112,25.26575,-25.62104), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.61555,-18.48528,20.69234,-18.46722), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.61555,-18.48528,20.69234,-18.46722), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.61555,-18.48528,20.69234,-18.46722), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.61555,-18.48528,20.69234,-18.46722), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.57083,-18.46722,20.69234,-18.31723), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.57083,-18.46722,20.69234,-18.31723), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.57083,-18.46722,20.69234,-18.31723), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.57083,-18.46722,20.69234,-18.31723), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.82333,-18.32251,25.26575,-25.57417), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.82333,-18.32251,25.26575,-25.57417), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.82333,-18.32251,25.26575,-25.57417), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.82333,-18.32251,25.26575,-25.57417), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((20.99544,-18.31741,20.69234,-19.29972), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.54888,-18.31723,20.69234,-18.46722), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.54888,-18.31723,20.69234,-18.46722), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.54888,-18.31723,20.69234,-18.46722), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.54888,-18.31723,20.69234,-18.46722), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((21.46249,-18.30445,25.26575,-26.85334), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((21.46249,-18.30445,25.26575,-26.85334), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((21.46249,-18.30445,25.26575,-26.85334), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((21.46249,-18.30445,25.26575,-26.85334), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.46194,-18.22639,25.26575,-25.27834), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.46194,-18.22639,25.26575,-25.27834), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.46194,-18.22639,25.26575,-25.27834), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.46194,-18.22639,25.26575,-25.27834), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.98888,-18.16445,25.26575,-25.65889), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.98888,-18.16445,25.26575,-25.65889), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.98888,-18.16445,25.26575,-25.65889), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.98888,-18.16445,25.26575,-25.65889), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.39333,-18.12251,25.26575,-25.74639), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.39333,-18.12251,25.26575,-25.74639), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.39333,-18.12251,25.26575,-25.74639), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.39333,-18.12251,25.26575,-25.74639), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.30916,-18.06584,20.69234,-17.79766), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.30916,-18.06584,20.69234,-17.79766), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.30916,-18.06584,20.69234,-17.79766), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.30916,-18.06584,20.69234,-17.79766), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.4961,-18.05945,25.26575,-25.76306), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.4961,-18.05945,25.26575,-25.76306), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.4961,-18.05945,25.26575,-25.76306), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.4961,-18.05945,25.26575,-25.76306), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.60194,-18.02056,25.26575,-25.76306), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.60194,-18.02056,25.26575,-25.76306), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.60194,-18.02056,25.26575,-25.76306), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.60194,-18.02056,25.26575,-25.76306), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.46416,-18.00806,25.26575,-25.73556), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.46416,-18.00806,25.26575,-25.73556), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.46416,-18.00806,25.26575,-25.73556), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.46416,-18.00806,25.26575,-25.73556), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((23.29145,-17.99815,25.26575,-25.27834), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((23.29145,-17.99815,25.26575,-25.27834), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((23.29145,-17.99815,25.26575,-25.27834), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((23.29145,-17.99815,25.26575,-25.27834), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.38194,-17.94667,25.26575,-25.76278), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.38194,-17.94667,25.26575,-25.76278), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.38194,-17.94667,25.26575,-25.76278), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.38194,-17.94667,25.26575,-25.76278), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.23666,-17.89445,20.69234,-17.79766), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.23666,-17.89445,20.69234,-17.79766), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.23666,-17.89445,20.69234,-17.79766), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.23666,-17.89445,20.69234,-17.79766), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((24.82333,-17.84056,25.26575,-25.83084), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((24.82333,-17.84056,25.26575,-25.83084), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((24.82333,-17.84056,25.26575,-25.83084), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((24.82333,-17.84056,25.26575,-25.83084), mapfile, tile_dir, 17, 17, "bw-botswana")
	render_tiles((25.26575,-17.79766,20.69234,-17.89445), mapfile, tile_dir, 0, 11, "bw-botswana")
	render_tiles((25.26575,-17.79766,20.69234,-17.89445), mapfile, tile_dir, 13, 13, "bw-botswana")
	render_tiles((25.26575,-17.79766,20.69234,-17.89445), mapfile, tile_dir, 15, 15, "bw-botswana")
	render_tiles((25.26575,-17.79766,20.69234,-17.89445), mapfile, tile_dir, 17, 17, "bw-botswana")