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
        mapfile = "../../../tilestyles/mazda/mazda.xml"
    except KeyError:
        print("[MapFile] Not found")
        sys.exit(1)
    try:
        # ./tilegen/zones/[zone]/[region]
        tile_dir = "../../../../output/"
    except KeyError:
        print("[OutputDir] No output directory found")
        sys.exit(1)

    if not tile_dir.endswith('/'):
        tile_dir = tile_dir + '/'


    print ("Starting")

    # ------------------------------------------------------------------------
    # Tile Render Data
    # Zone: world
    # Region: CR
    # Region Name: Costa Rica

    render_tiles((-82.89667,8.02671,-82.89667,9.79694), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.89667,8.02671,-82.89667,9.79694), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.89667,8.02671,-82.89667,9.79694), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.89667,8.02671,-82.89667,9.79694), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.985,8.24194,-85.61444,8.27472), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.985,8.24194,-85.61444,8.27472), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.985,8.24194,-85.61444,8.27472), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.985,8.24194,-85.61444,8.27472), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.93083,8.25472,-85.61444,9.06207), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.93083,8.25472,-85.61444,9.06207), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.93083,8.25472,-85.61444,9.06207), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.93083,8.25472,-85.61444,9.06207), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.96666,8.27472,-85.61444,8.24194), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.96666,8.27472,-85.61444,8.24194), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.96666,8.27472,-85.61444,8.24194), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.96666,8.27472,-85.61444,8.24194), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.03168,8.33694,-82.89667,10.00111), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.03168,8.33694,-82.89667,10.00111), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.03168,8.33694,-82.89667,10.00111), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.03168,8.33694,-82.89667,10.00111), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.13945,8.35,-85.61444,8.385), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.13945,8.35,-85.61444,8.385), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.13945,8.35,-85.61444,8.385), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.13945,8.35,-85.61444,8.385), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.30751,8.37139,-85.61444,8.53639), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.30751,8.37139,-85.61444,8.53639), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.30751,8.37139,-85.61444,8.53639), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.30751,8.37139,-85.61444,8.53639), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.27724,8.38416,-82.89667,10.205), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.27724,8.38416,-82.89667,10.205), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.27724,8.38416,-82.89667,10.205), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.27724,8.38416,-82.89667,10.205), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.14029,8.385,-85.61444,8.35), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.14029,8.385,-85.61444,8.35), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.14029,8.385,-85.61444,8.35), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.14029,8.385,-85.61444,8.35), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.09529,8.43916,-82.89667,10.01611), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.09529,8.43916,-82.89667,10.01611), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.09529,8.43916,-82.89667,10.01611), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.09529,8.43916,-82.89667,10.01611), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.48167,8.4425,-85.61444,8.69305), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.48167,8.4425,-85.61444,8.69305), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.48167,8.4425,-85.61444,8.69305), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.48167,8.4425,-85.61444,8.69305), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.57945,8.44666,-85.61444,8.83528), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.57945,8.44666,-85.61444,8.83528), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.57945,8.44666,-85.61444,8.83528), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.57945,8.44666,-85.61444,8.83528), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.83057,8.47388,-85.61444,9.49819), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.83057,8.47388,-85.61444,9.49819), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.83057,8.47388,-85.61444,9.49819), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.83057,8.47388,-85.61444,9.49819), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.30139,8.53639,-85.61444,8.37139), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.30139,8.53639,-85.61444,8.37139), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.30139,8.53639,-85.61444,8.37139), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.30139,8.53639,-85.61444,8.37139), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.82695,8.57583,-85.61444,8.47388), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.82695,8.57583,-85.61444,8.47388), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.82695,8.57583,-85.61444,8.47388), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.82695,8.57583,-85.61444,8.47388), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.13084,8.58389,-85.61444,8.35), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.13084,8.58389,-85.61444,8.35), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.13084,8.58389,-85.61444,8.35), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.13084,8.58389,-85.61444,8.35), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.18056,8.59666,-85.61444,8.62611), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.18056,8.59666,-85.61444,8.62611), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.18056,8.59666,-85.61444,8.62611), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.18056,8.59666,-85.61444,8.62611), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.73584,8.6125,-85.61444,9.11666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.73584,8.6125,-85.61444,9.11666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.73584,8.6125,-85.61444,9.11666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.73584,8.6125,-85.61444,9.11666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.16446,8.62611,-85.61444,8.59666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.16446,8.62611,-85.61444,8.59666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.16446,8.62611,-85.61444,8.59666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.16446,8.62611,-85.61444,8.59666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.32251,8.66805,-85.61444,8.37139), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.32251,8.66805,-85.61444,8.37139), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.32251,8.66805,-85.61444,8.37139), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.32251,8.66805,-85.61444,8.37139), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.47557,8.69305,-85.61444,8.4425), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.47557,8.69305,-85.61444,8.4425), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.47557,8.69305,-85.61444,8.4425), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.47557,8.69305,-85.61444,8.4425), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.37695,8.73611,-82.89667,10.39361), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.37695,8.73611,-82.89667,10.39361), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.37695,8.73611,-82.89667,10.39361), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.37695,8.73611,-82.89667,10.39361), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.91528,8.74527,-85.61444,9.06207), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.91528,8.74527,-85.61444,9.06207), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.91528,8.74527,-85.61444,9.06207), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.91528,8.74527,-85.61444,9.06207), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.61057,8.77972,-85.61444,8.96476), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.61057,8.77972,-85.61444,8.96476), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.61057,8.77972,-85.61444,8.96476), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.61057,8.77972,-85.61444,8.96476), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.88501,8.78472,-82.89667,9.79694), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.88501,8.78472,-82.89667,9.79694), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.88501,8.78472,-82.89667,9.79694), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.88501,8.78472,-82.89667,9.79694), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.65001,8.78583,-82.89667,10.92426), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.65001,8.78583,-82.89667,10.92426), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.65001,8.78583,-82.89667,10.92426), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.65001,8.78583,-82.89667,10.92426), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.58278,8.83528,-85.61444,8.44666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.58278,8.83528,-85.61444,8.44666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.58278,8.83528,-85.61444,8.44666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.58278,8.83528,-85.61444,8.44666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.74861,8.88722,-82.89667,9.64944), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.74861,8.88722,-82.89667,9.64944), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.74861,8.88722,-82.89667,9.64944), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.74861,8.88722,-82.89667,9.64944), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.71916,8.94027,-85.61444,9.55889), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.71916,8.94027,-85.61444,9.55889), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.71916,8.94027,-85.61444,9.55889), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.71916,8.94027,-85.61444,9.55889), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.60345,8.96476,-85.61444,8.77972), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.60345,8.96476,-85.61444,8.77972), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.60345,8.96476,-85.61444,8.77972), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.60345,8.96476,-85.61444,8.77972), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.85583,9.04583,-85.61444,9.60833), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.85583,9.04583,-85.61444,9.60833), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.85583,9.04583,-85.61444,9.60833), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.85583,9.04583,-85.61444,9.60833), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.92947,9.06207,-85.61444,8.25472), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.92947,9.06207,-85.61444,8.25472), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.92947,9.06207,-85.61444,8.25472), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.92947,9.06207,-85.61444,8.25472), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.70334,9.11666,-82.89667,10.78889), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.70334,9.11666,-82.89667,10.78889), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.70334,9.11666,-82.89667,10.78889), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.70334,9.11666,-82.89667,10.78889), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.87946,9.26,-82.89667,10.70972), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.87946,9.26,-82.89667,10.70972), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.87946,9.26,-82.89667,10.70972), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.87946,9.26,-82.89667,10.70972), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.10583,9.37194,-85.61444,9.41861), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.10583,9.37194,-85.61444,9.41861), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.10583,9.37194,-85.61444,9.41861), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.10583,9.37194,-85.61444,9.41861), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.17529,9.39361,-85.61444,9.41861), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.17529,9.39361,-85.61444,9.41861), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.17529,9.39361,-85.61444,9.41861), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.17529,9.39361,-85.61444,9.41861), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.93416,9.39833,-85.61444,9.47166), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.93416,9.39833,-85.61444,9.47166), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.93416,9.39833,-85.61444,9.47166), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.93416,9.39833,-85.61444,9.47166), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.16724,9.41861,-85.61444,9.39361), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.16724,9.41861,-85.61444,9.39361), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.16724,9.41861,-85.61444,9.39361), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.16724,9.41861,-85.61444,9.39361), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.24918,9.47,-82.89667,10.87805), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.24918,9.47,-82.89667,10.87805), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.24918,9.47,-82.89667,10.87805), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.24918,9.47,-82.89667,10.87805), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.93472,9.47166,-85.61444,9.39833), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.93472,9.47166,-85.61444,9.39833), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.93472,9.47166,-85.61444,9.39833), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.93472,9.47166,-85.61444,9.39833), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.61555,9.48972,-85.61444,9.56979), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.61555,9.48972,-85.61444,9.56979), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.61555,9.48972,-85.61444,9.56979), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.61555,9.48972,-85.61444,9.56979), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.83321,9.49819,-85.61444,8.47388), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.83321,9.49819,-85.61444,8.47388), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.83321,9.49819,-85.61444,8.47388), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.83321,9.49819,-85.61444,8.47388), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.52306,9.51889,-82.89667,10.95944), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.52306,9.51889,-82.89667,10.95944), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.52306,9.51889,-82.89667,10.95944), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.52306,9.51889,-82.89667,10.95944), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.11362,9.55305,-82.89667,9.97889), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.11362,9.55305,-82.89667,9.97889), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.11362,9.55305,-82.89667,9.97889), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.11362,9.55305,-82.89667,9.97889), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.87532,9.55633,-85.61444,8.78472), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.87532,9.55633,-85.61444,8.78472), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.87532,9.55633,-85.61444,8.78472), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.87532,9.55633,-85.61444,8.78472), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.72749,9.55889,-85.61444,8.94027), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.72749,9.55889,-85.61444,8.94027), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.72749,9.55889,-85.61444,8.94027), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.72749,9.55889,-85.61444,8.94027), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.56519,9.56979,-85.61444,9.48972), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.56519,9.56979,-85.61444,9.48972), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.56519,9.56979,-85.61444,9.48972), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.56519,9.56979,-85.61444,9.48972), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.09167,9.58639,-82.89667,10.16666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.09167,9.58639,-82.89667,10.16666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.09167,9.58639,-82.89667,10.16666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.09167,9.58639,-82.89667,10.16666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.84334,9.60833,-85.61444,9.49819), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.84334,9.60833,-85.61444,9.49819), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.84334,9.60833,-85.61444,9.49819), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.84334,9.60833,-85.61444,9.49819), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.67029,9.64722,-82.89667,11.07805), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.67029,9.64722,-82.89667,11.07805), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.67029,9.64722,-82.89667,11.07805), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.67029,9.64722,-82.89667,11.07805), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.74306,9.64944,-85.61444,8.88722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.74306,9.64944,-85.61444,8.88722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.74306,9.64944,-85.61444,8.88722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.74306,9.64944,-85.61444,8.88722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.99306,9.70194,-82.89667,10.95527), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.99306,9.70194,-82.89667,10.95527), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.99306,9.70194,-82.89667,10.95527), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.99306,9.70194,-82.89667,10.95527), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.25473,9.74778,-82.89667,10.12277), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.25473,9.74778,-82.89667,10.12277), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.25473,9.74778,-82.89667,10.12277), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.25473,9.74778,-82.89667,10.12277), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.62918,9.76444,-82.89667,9.64722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.62918,9.76444,-82.89667,9.64722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.62918,9.76444,-82.89667,9.64722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.62918,9.76444,-82.89667,9.64722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-82.89418,9.79694,-85.61444,8.02671), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-82.89418,9.79694,-85.61444,8.02671), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-82.89418,9.79694,-85.61444,8.02671), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-82.89418,9.79694,-85.61444,8.02671), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.86528,9.82444,-82.89667,10.0025), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.86528,9.82444,-82.89667,10.0025), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.86528,9.82444,-82.89667,10.0025), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.86528,9.82444,-82.89667,10.0025), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.92834,9.83111,-82.89667,9.92722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.92834,9.83111,-82.89667,9.92722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.92834,9.83111,-82.89667,9.92722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.92834,9.83111,-82.89667,9.92722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.3439,9.83166,-82.89667,10.27861), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.3439,9.83166,-82.89667,10.27861), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.3439,9.83166,-82.89667,10.27861), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.3439,9.83166,-82.89667,10.27861), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.5914,9.88305,-82.89667,11.21361), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.5914,9.88305,-82.89667,11.21361), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.5914,9.88305,-82.89667,11.21361), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.5914,9.88305,-82.89667,11.21361), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.66446,9.89916,-82.89667,10.7625), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.66446,9.89916,-82.89667,10.7625), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.66446,9.89916,-82.89667,10.7625), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.66446,9.89916,-82.89667,10.7625), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.92279,9.92722,-82.89667,9.83111), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.92279,9.92722,-82.89667,9.83111), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.92279,9.92722,-82.89667,9.83111), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.92279,9.92722,-82.89667,9.83111), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.73668,9.9525,-82.89667,11.07805), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.73668,9.9525,-82.89667,11.07805), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.73668,9.9525,-82.89667,11.07805), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.73668,9.9525,-82.89667,11.07805), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.10695,9.97889,-85.61444,9.55305), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.10695,9.97889,-85.61444,9.55305), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.10695,9.97889,-85.61444,9.55305), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.10695,9.97889,-85.61444,9.55305), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.71529,10.00028,-82.89667,11.08428), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.71529,10.00028,-82.89667,11.08428), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.71529,10.00028,-82.89667,11.08428), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.71529,10.00028,-82.89667,11.08428), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.02306,10.00111,-85.61444,8.33694), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.02306,10.00111,-85.61444,8.33694), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.02306,10.00111,-85.61444,8.33694), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.02306,10.00111,-85.61444,8.33694), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.87946,10.0025,-82.89667,9.82444), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.87946,10.0025,-82.89667,9.82444), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.87946,10.0025,-82.89667,9.82444), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.87946,10.0025,-82.89667,9.82444), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.11057,10.01611,-85.61444,8.43916), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.11057,10.01611,-85.61444,8.43916), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.11057,10.01611,-85.61444,8.43916), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.11057,10.01611,-85.61444,8.43916), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.17223,10.045,-82.89667,10.16833), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.17223,10.045,-82.89667,10.16833), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.17223,10.045,-82.89667,10.16833), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.17223,10.045,-82.89667,10.16833), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.2289,10.12277,-82.89667,9.74778), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.2289,10.12277,-82.89667,9.74778), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.2289,10.12277,-82.89667,9.74778), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.2289,10.12277,-82.89667,9.74778), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.06973,10.13083,-82.89667,10.16666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.06973,10.13083,-82.89667,10.16666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.06973,10.13083,-82.89667,10.16666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.06973,10.13083,-82.89667,10.16666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.08862,10.16666,-85.61444,9.58639), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.08862,10.16666,-85.61444,9.58639), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.08862,10.16666,-85.61444,9.58639), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.08862,10.16666,-85.61444,9.58639), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.1925,10.16833,-82.89667,10.045), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.1925,10.16833,-82.89667,10.045), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.1925,10.16833,-82.89667,10.045), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.1925,10.16833,-82.89667,10.045), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.26112,10.205,-85.61444,8.38416), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.26112,10.205,-85.61444,8.38416), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.26112,10.205,-85.61444,8.38416), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.26112,10.205,-85.61444,8.38416), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.83778,10.21416,-82.89667,10.50722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.83778,10.21416,-82.89667,10.50722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.83778,10.21416,-82.89667,10.50722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.83778,10.21416,-82.89667,10.50722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.28528,10.27861,-82.89667,11.09139), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.28528,10.27861,-82.89667,11.09139), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.28528,10.27861,-82.89667,11.09139), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.28528,10.27861,-82.89667,11.09139), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.87361,10.34944,-82.89667,10.94389), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.87361,10.34944,-82.89667,10.94389), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.87361,10.34944,-82.89667,10.94389), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.87361,10.34944,-82.89667,10.94389), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.40889,10.39361,-85.61444,8.73611), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.40889,10.39361,-85.61444,8.73611), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.40889,10.39361,-85.61444,8.73611), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.40889,10.39361,-85.61444,8.73611), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.77556,10.445,-82.89667,10.50722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.77556,10.445,-82.89667,10.50722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.77556,10.445,-82.89667,10.50722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.77556,10.445,-82.89667,10.50722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.8114,10.50722,-82.89667,10.21416), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.8114,10.50722,-82.89667,10.21416), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.8114,10.50722,-82.89667,10.21416), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.8114,10.50722,-82.89667,10.21416), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.63445,10.615,-82.89667,11.15166), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.63445,10.615,-82.89667,11.15166), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.63445,10.615,-82.89667,11.15166), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.63445,10.615,-82.89667,11.15166), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.52501,10.61639,-85.61444,8.4425), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.52501,10.61639,-85.61444,8.4425), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.52501,10.61639,-85.61444,8.4425), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.52501,10.61639,-85.61444,8.4425), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.65417,10.63861,-82.89667,11.15166), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.65417,10.63861,-82.89667,11.15166), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.65417,10.63861,-82.89667,11.15166), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.65417,10.63861,-82.89667,11.15166), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.92444,10.70972,-85.61444,9.26), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.92444,10.70972,-85.61444,9.26), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.92444,10.70972,-85.61444,9.26), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.92444,10.70972,-85.61444,9.26), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.59113,10.72444,-85.61444,8.83528), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.59113,10.72444,-85.61444,8.83528), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.59113,10.72444,-85.61444,8.83528), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.59113,10.72444,-85.61444,8.83528), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.98222,10.75611,-82.89667,10.70972), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.98222,10.75611,-82.89667,10.70972), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.98222,10.75611,-82.89667,10.70972), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.98222,10.75611,-82.89667,10.70972), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.66139,10.7625,-82.89667,9.89916), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.66139,10.7625,-82.89667,9.89916), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.66139,10.7625,-82.89667,9.89916), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.66139,10.7625,-82.89667,9.89916), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.19777,10.78583,-85.61444,9.39361), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.19777,10.78583,-85.61444,9.39361), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.19777,10.78583,-85.61444,9.39361), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.19777,10.78583,-85.61444,9.39361), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.67722,10.78889,-82.89667,10.87944), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.67722,10.78889,-82.89667,10.87944), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.67722,10.78889,-82.89667,10.87944), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.67722,10.78889,-82.89667,10.87944), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.70056,10.80583,-82.89667,10.95389), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.70056,10.80583,-82.89667,10.95389), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.70056,10.80583,-82.89667,10.95389), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.70056,10.80583,-82.89667,10.95389), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.6239,10.85222,-85.61444,8.77972), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.6239,10.85222,-85.61444,8.77972), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.6239,10.85222,-85.61444,8.77972), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.6239,10.85222,-85.61444,8.77972), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.22472,10.87805,-85.61444,9.47), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.22472,10.87805,-85.61444,9.47), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.22472,10.87805,-85.61444,9.47), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.22472,10.87805,-85.61444,9.47), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.66167,10.87944,-85.61444,8.78583), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.66167,10.87944,-85.61444,8.78583), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.66167,10.87944,-85.61444,8.78583), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.66167,10.87944,-85.61444,8.78583), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.89862,10.88305,-82.89667,10.94389), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.89862,10.88305,-82.89667,10.94389), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.89862,10.88305,-82.89667,10.94389), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.89862,10.88305,-82.89667,10.94389), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-83.64534,10.92426,-85.61444,8.78583), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-83.64534,10.92426,-85.61444,8.78583), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-83.64534,10.92426,-85.61444,8.78583), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-83.64534,10.92426,-85.61444,8.78583), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.32167,10.92666,-82.89667,10.99333), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.32167,10.92666,-82.89667,10.99333), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.32167,10.92666,-82.89667,10.99333), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.32167,10.92666,-82.89667,10.99333), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.90279,10.94083,-82.89667,9.92722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.90279,10.94083,-82.89667,9.92722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.90279,10.94083,-82.89667,9.92722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.90279,10.94083,-82.89667,9.92722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.8775,10.94389,-82.89667,10.34944), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.8775,10.94389,-82.89667,10.34944), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.8775,10.94389,-82.89667,10.34944), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.8775,10.94389,-82.89667,10.34944), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.70695,10.95389,-82.89667,11.08428), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.70695,10.95389,-82.89667,11.08428), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.70695,10.95389,-82.89667,11.08428), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.70695,10.95389,-82.89667,11.08428), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.94055,10.95527,-82.89667,9.83111), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.94055,10.95527,-82.89667,9.83111), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.94055,10.95527,-82.89667,9.83111), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.94055,10.95527,-82.89667,9.83111), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.44444,10.95944,-85.61444,9.51889), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.44444,10.95944,-85.61444,9.51889), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.44444,10.95944,-85.61444,9.51889), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.44444,10.95944,-85.61444,9.51889), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.35556,10.99333,-82.89667,10.92666), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.35556,10.99333,-82.89667,10.92666), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.35556,10.99333,-82.89667,10.92666), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.35556,10.99333,-82.89667,10.92666), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.675,11.03139,-82.89667,9.89916), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.675,11.03139,-82.89667,9.89916), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.675,11.03139,-82.89667,9.89916), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.675,11.03139,-82.89667,9.89916), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.73279,11.03611,-82.89667,10.00028), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.73279,11.03611,-82.89667,10.00028), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.73279,11.03611,-82.89667,10.00028), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.73279,11.03611,-82.89667,10.00028), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.69157,11.07511,-82.89667,10.80583), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.69157,11.07511,-82.89667,10.80583), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.69157,11.07511,-82.89667,10.80583), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.69157,11.07511,-82.89667,10.80583), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-84.67444,11.07805,-82.89667,9.64722), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-84.67444,11.07805,-82.89667,9.64722), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-84.67444,11.07805,-82.89667,9.64722), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-84.67444,11.07805,-82.89667,9.64722), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.71135,11.08428,-82.89667,10.00028), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.28416,11.09139,-82.89667,10.27861), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.28416,11.09139,-82.89667,10.27861), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.28416,11.09139,-82.89667,10.27861), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.28416,11.09139,-82.89667,10.27861), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.65028,11.15166,-82.89667,10.63861), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.65028,11.15166,-82.89667,10.63861), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.65028,11.15166,-82.89667,10.63861), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.65028,11.15166,-82.89667,10.63861), mapfile, tile_dir, 17, 17, "cr-costa-rica")
    render_tiles((-85.61444,11.21361,-82.89667,10.615), mapfile, tile_dir, 0, 11, "cr-costa-rica")
    render_tiles((-85.61444,11.21361,-82.89667,10.615), mapfile, tile_dir, 13, 13, "cr-costa-rica")
    render_tiles((-85.61444,11.21361,-82.89667,10.615), mapfile, tile_dir, 15, 15, "cr-costa-rica")
    render_tiles((-85.61444,11.21361,-82.89667,10.615), mapfile, tile_dir, 17, 17, "cr-costa-rica")