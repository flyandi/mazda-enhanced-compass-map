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
    # Region: GY
    # Region Name: Guyana

	render_tiles((-58.80722,1.18555,-58.86584,1.20194), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.86584,1.20194,-58.80722,1.18555), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.72639,1.22861,-58.86584,1.20194), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.51556,1.26778,-58.69527,1.28694), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.69527,1.28694,-58.92834,1.30278), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.92834,1.30278,-58.69527,1.28694), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.47111,1.32055,-59.00054,1.33118), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.00054,1.33118,-58.47111,1.32055), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.23277,1.37917,-59.00054,1.33118), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.5039,1.45055,-58.01389,1.51222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.01389,1.51222,-58.3475,1.54944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.3475,1.54944,-58.25278,1.56583), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.25278,1.56583,-58.3475,1.54944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.97472,1.60139,-58.31194,1.60333), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.31194,1.60333,-57.97472,1.60139), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.98083,1.65694,-57.82528,1.68222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.82528,1.68222,-57.56445,1.69806), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.56445,1.69806,-57.82528,1.68222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.53139,1.71889,-57.75362,1.72055), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.75362,1.72055,-59.53139,1.71889), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.64278,1.73111,-57.50751,1.73639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.50751,1.73639,-59.64278,1.73111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.67805,1.77139,-57.50751,1.73639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.63111,1.84639,-59.75195,1.86528), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.75195,1.86528,-59.63111,1.84639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.55472,1.90333,-56.67084,1.91361), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.67084,1.91361,-56.97916,1.91639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.97916,1.91639,-56.67084,1.91361), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.62833,1.93972,-56.47091,1.94446), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.47091,1.94446,-57.23695,1.94722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.23695,1.94722,-56.47091,1.94446), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.05305,1.95278,-57.35306,1.95555), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.35306,1.95555,-57.05305,1.95278), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.30305,1.98139,-57.35306,1.95555), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.0675,2.01083,-56.55778,2.02194), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.55778,2.02194,-57.10056,2.02389), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.10056,2.02389,-56.55778,2.02194), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-56.69189,2.02697,-57.10056,2.02389), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.73473,2.28722,-59.84973,2.33055), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.84973,2.33055,-59.73473,2.28722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.02306,2.62472,-59.96528,2.62667), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.96528,2.62667,-57.02306,2.62472), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.09361,2.71583,-57.12778,2.79167), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.12778,2.79167,-57.20222,2.82083), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.20222,2.82083,-57.12778,2.79167), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.97694,2.91139,-57.20222,2.82083), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.21056,3.03528,-59.97694,2.91139), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.29333,3.26389,-59.85973,3.28694), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.85973,3.28694,-57.29333,3.26389), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.64223,3.35583,-57.29945,3.37083), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.29945,3.37083,-57.64223,3.35583), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.66695,3.39556,-57.29945,3.37083), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.63695,3.46111,-59.82139,3.46805), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.82139,3.46805,-57.63695,3.46111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.85834,3.56389,-57.735,3.61028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.735,3.61028,-59.76056,3.62833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.76056,3.62833,-57.735,3.61028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.83583,3.665,-59.76056,3.62833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.92917,3.88667,-59.51889,3.9525), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.51889,3.9525,-58.04056,3.99472), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.04056,3.99472,-59.59862,4.01639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.59862,4.01639,-58.04056,3.99472), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.63583,4.14639,-58.07167,4.15222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.07167,4.15222,-59.63583,4.14639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.70528,4.16722,-58.07167,4.15222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.73277,4.2275,-59.70528,4.16722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.95417,4.28778,-59.71278,4.30944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.71278,4.30944,-57.95417,4.28778), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.67612,4.38889,-59.75472,4.43028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.75472,4.43028,-59.67612,4.38889), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.14779,4.5175,-57.8825,4.55916), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.8825,4.55916,-60.15222,4.57333), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.15222,4.57333,-57.8825,4.55916), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.83861,4.65472,-60.02583,4.70722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.02583,4.70722,-57.83861,4.65472), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.92639,4.79861,-60.02583,4.70722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.85362,4.91278,-57.37583,5.00611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.37583,5.00611,-57.68833,5.01028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.68833,5.01028,-57.37583,5.00611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.28917,5.01666,-57.68833,5.01028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.97472,5.09305,-60.05194,5.13444), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.05194,5.13444,-57.22888,5.14083), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.22888,5.14083,-60.05194,5.13444), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.29723,5.16028,-57.19055,5.16417), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.19055,5.16417,-57.29723,5.16028), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.44055,5.17639,-57.26611,5.17777), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.26611,5.17777,-60.44055,5.17639), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.73293,5.20528,-60.66695,5.21666), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.66695,5.21666,-57.26112,5.22055), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.26112,5.22055,-60.66695,5.21666), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.11142,5.22591,-57.28667,5.22944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.28667,5.22944,-57.21278,5.23222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.21278,5.23222,-57.28667,5.22944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.2525,5.26917,-60.20055,5.27361), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.20055,5.27361,-57.2525,5.26917), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.33556,5.31333,-60.20055,5.27361), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.24814,5.48581,-57.18472,5.5925), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.18472,5.5925,-57.24814,5.48581), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.39,5.94,-57.15968,5.99724), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.15968,5.99724,-61.39,5.94), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.27914,6.06676,-61.21916,6.12889), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.21916,6.12889,-57.21001,6.15833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.21001,6.15833,-61.11306,6.18722), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.11306,6.18722,-57.21001,6.15833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.51945,6.27083,-61.11389,6.28222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.11389,6.28222,-57.3639,6.29), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.3639,6.29,-61.11389,6.28222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.15666,6.32889,-57.49751,6.34111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.49751,6.34111,-61.15666,6.32889), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.68333,6.38,-58.5964,6.41333), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.5964,6.41333,-58.68333,6.38), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.63473,6.44972,-57.63667,6.48222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.63667,6.48222,-61.15361,6.49222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.15361,6.49222,-57.63667,6.48222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.61834,6.54916,-61.20472,6.57444), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.20472,6.57444,-58.61834,6.54916), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-61.13805,6.70194,-60.71306,6.75805), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.71306,6.75805,-60.89611,6.75972), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.89611,6.75972,-60.71306,6.75805), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.48668,6.78055,-57.99584,6.79528), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-57.99584,6.79528,-60.90944,6.80916), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.90944,6.80916,-57.99584,6.79528), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.42612,6.86611,-58.32751,6.89528), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.32751,6.89528,-58.42612,6.86611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.36806,6.93028,-58.55334,6.94222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.55334,6.94222,-60.40833,6.9475), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.40833,6.9475,-58.55334,6.94222), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.47889,7.0125,-60.33139,7.02417), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.33139,7.02417,-58.47889,7.0125), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.27834,7.1125,-60.53556,7.12389), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.53556,7.12389,-60.27834,7.1125), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.35778,7.17555,-60.49528,7.18111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.49528,7.18111,-60.35778,7.17555), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.61777,7.19444,-60.49528,7.18111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.63445,7.25805,-58.46751,7.28166), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.46751,7.28166,-60.63445,7.25805), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.58805,7.31639,-58.46751,7.28166), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.64584,7.43667,-60.69111,7.45583), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.69111,7.45583,-60.64584,7.43667), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.71973,7.52889,-60.69361,7.56611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.69361,7.56611,-58.65389,7.59833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.65389,7.59833,-60.63277,7.60111), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.63277,7.60111,-58.65389,7.59833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.53528,7.80222,-60.34094,7.84604), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.34094,7.84604,-58.95361,7.8625), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-58.95361,7.8625,-60.34094,7.84604), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.17805,7.98139,-60.02333,8.04139), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-60.02333,8.04139,-60.17805,7.98139), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.43528,8.12833,-59.32751,8.16), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.32751,8.16,-59.43528,8.12833), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.94445,8.21027,-59.52251,8.22611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.94445,8.21027,-59.52251,8.22611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.52251,8.22611,-59.83195,8.22888), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.83195,8.22888,-59.52251,8.22611), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.80139,8.275,-59.83195,8.22888), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.75278,8.3475,-59.85583,8.37944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.85583,8.37944,-59.78195,8.38666), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.78195,8.38666,-59.85583,8.37944), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.76501,8.41,-59.78195,8.38666), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.95251,8.50916,-59.98905,8.5336), mapfile, tile_dir, 0, 11, "gy-guyana")
	render_tiles((-59.98905,8.5336,-59.95251,8.50916), mapfile, tile_dir, 0, 11, "gy-guyana")