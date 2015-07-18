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
    # Region: IR
    # Region Name: Iran

	render_tiles((55.30499,26.54027,55.27499,26.65083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.30499,26.54027,55.27499,26.65083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.27499,26.65083,55.45221,26.68), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.45221,26.68,55.95165,26.68916), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.95165,26.68916,55.45221,26.68), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.87832,26.73638,55.95165,26.68916), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.09026,26.78861,55.76999,26.79277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.76999,26.79277,56.09026,26.78861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.7686,26.8811,55.88332,26.90166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.88332,26.90166,56.1836,26.91833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.1836,26.91833,55.73637,26.92194), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.73637,26.92194,56.1836,26.91833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.28833,26.95006,55.75221,26.95194), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.75221,26.95194,56.28833,26.95006), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.27304,26.97583,56.15554,26.99861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.15554,26.99861,56.27304,26.97583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.4386,25.07527,61.42304,25.0975), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.42304,25.0975,61.4386,25.07527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.21526,25.12416,61.51054,25.14722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.51054,25.14722,61.21526,25.12416), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.51638,25.18111,61.61097,25.19332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.61097,25.19332,61.51638,25.18111), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.54971,25.25,60.4411,25.26833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.4411,25.26833,60.61582,25.27583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.61582,25.27583,60.4411,25.26833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.64693,25.30611,60.19554,25.32166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.19554,25.32166,60.44193,25.32722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.44193,25.32722,60.19554,25.32166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.40554,25.33388,60.10915,25.33833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.10915,25.33833,60.40554,25.33388), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.89388,25.34499,60.10915,25.33833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.18916,25.3586,60.3936,25.36944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.3936,25.36944,60.0861,25.37722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.0861,25.37722,60.27971,25.37861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.27971,25.37861,60.0036,25.37944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.0036,25.37944,60.27971,25.37861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.59915,25.39555,59.10221,25.39666), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.10221,25.39666,59.59915,25.39555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.59805,25.40944,59.83887,25.41083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.83887,25.41083,60.59805,25.40944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.42776,25.41583,59.83887,25.41083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.48804,25.44055,59.5161,25.46055), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.55471,25.44055,59.5161,25.46055), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.5161,25.46055,59.45055,25.47777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.45055,25.47777,59.5161,25.46055), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.89832,25.52417,58.13499,25.54138), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.13499,25.54138,58.89832,25.52417), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.04555,25.57694,58.54221,25.59333), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.54221,25.59333,58.04555,25.57694), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.76943,25.63527,58.54221,25.59333), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.97443,25.68722,57.78693,25.69833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.78693,25.69833,57.93332,25.70027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.93332,25.70027,57.78693,25.69833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.75388,25.74333,57.69554,25.74416), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.69554,25.74416,57.75388,25.74333), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.31055,25.77777,61.68637,25.79499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.68637,25.79499,61.76943,25.80972), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.76943,25.80972,61.68637,25.79499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.26388,25.93277,57.19804,25.995), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.19804,25.995,57.26388,25.93277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.16693,26.09583,57.21387,26.17249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.21387,26.17249,61.83276,26.17944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.83276,26.17944,57.21387,26.17249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.85804,26.23471,61.83276,26.17944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.1236,26.31833,62.27667,26.35406), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.27667,26.35406,62.13582,26.38083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.13582,26.38083,62.27667,26.35406), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.27332,26.42944,57.07416,26.44722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.07416,26.44722,62.27332,26.42944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.0911,26.49667,54.61749,26.50027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.61749,26.50027,57.0911,26.49667), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.84165,26.51638,54.61749,26.50027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.37137,26.54249,54.84165,26.51638), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.6136,26.57999,54.55721,26.58222), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.55721,26.58222,62.6136,26.57999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.39971,26.59666,54.55721,26.58222), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.1811,26.63388,63.14027,26.63527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.14027,26.63527,63.1811,26.63388), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.77248,26.64972,63.14027,26.63527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.71333,26.70166,57.06443,26.71611), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.15415,26.70166,57.06443,26.71611), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.06443,26.71611,54.29082,26.71833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.29082,26.71833,57.06443,26.71611), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.02527,26.74528,54.29082,26.71833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.24388,26.78166,55.58276,26.79222), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.58276,26.79222,55.24388,26.78166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.20638,26.84222,55.59332,26.84499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.59332,26.84499,63.20638,26.84222), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.47527,26.86861,63.28693,26.8811), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.28693,26.8811,53.47527,26.86861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.58721,26.90527,56.9611,26.91499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.9611,26.91499,55.58721,26.90527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.46526,26.94888,55.63721,26.97694), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.63721,26.97694,53.42387,26.98249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.42387,26.98249,56.92387,26.9825), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.92387,26.9825,53.42387,26.98249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.96971,27,56.92387,26.9825), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.25417,27.01864,56.8411,27.03444), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.8411,27.03444,63.25417,27.01864), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.18638,27.0525,56.8411,27.03444), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.88527,27.07083,56.8436,27.08), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.8436,27.08,56.88527,27.07083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.07638,27.1125,63.27859,27.12194), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.27859,27.12194,63.34193,27.12249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.34193,27.12249,63.27859,27.12194), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.8066,27.13896,52.98527,27.1436), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.98527,27.1436,56.8066,27.13896), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.13277,27.16027,63.31776,27.16944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.31776,27.16944,56.43832,27.17528), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.43832,27.17528,63.31776,27.16944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.91971,27.21499,56.43832,27.17528), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((63.19804,27.26777,62.76471,27.27194), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.76471,27.27194,63.19804,27.26777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.60304,27.35083,52.57777,27.39778), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.57777,27.39778,52.66805,27.41), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.66805,27.41,52.57777,27.39778), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.67082,27.44416,62.83971,27.47444), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.83971,27.47444,52.67082,27.44416), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.47971,27.61944,52.37221,27.64583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.37221,27.64583,52.47971,27.61944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.82193,27.76027,52.02026,27.83027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.02026,27.83027,51.58221,27.84916), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.58221,27.84916,52.02026,27.83027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.47694,27.93166,62.75777,28.00027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.75777,28.00027,51.30332,28.05527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.30332,28.05527,51.3161,28.10555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.3161,28.10555,51.26332,28.15409), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.26332,28.15409,51.3161,28.10555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.59248,28.2336,51.28333,28.23555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.28333,28.23555,62.59248,28.2336), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.78137,28.26694,51.28333,28.23555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.14221,28.39861,62.39471,28.42166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.39471,28.42166,51.14221,28.39861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((62.03971,28.50055,62.39471,28.42166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.05749,28.73444,61.65137,28.78527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.65137,28.78527,51.00416,28.8036), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.00416,28.8036,61.65137,28.78527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.88527,28.82583,51.00416,28.8036), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.8386,28.88083,50.88527,28.82583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.89555,28.94333,50.80054,28.9725), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.80054,28.9725,61.54758,28.98494), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.54758,28.98494,50.83027,28.99305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.83027,28.99305,61.54758,28.98494), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.92888,29.01333,50.83027,28.99305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.92221,29.06667,61.5111,29.0886), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.5111,29.0886,50.92221,29.06667), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.67471,29.11833,50.81749,29.13916), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.81749,29.13916,50.63499,29.15277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.63499,29.15277,61.41998,29.16444), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.41998,29.16444,50.63499,29.15277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.35526,29.38833,50.6686,29.4086), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.6686,29.4086,61.35526,29.38833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.60693,29.46749,50.62138,29.49583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.62138,29.49583,50.60693,29.46749), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.49221,29.59416,50.62138,29.49583), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.86687,29.86243,48.54518,29.94454), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.54518,29.94454,50.13277,29.95277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.13277,29.95277,48.62666,29.95555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.62666,29.95555,50.13277,29.95277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.5373,29.96383,48.62666,29.95555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.65999,29.98888,48.44776,29.99305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.44776,29.99305,48.65999,29.98888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.55138,30.0075,48.86137,30.01972), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.86137,30.01972,49.55138,30.0075), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.91721,30.03888,48.63666,30.04972), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.63666,30.04972,49.50138,30.05388), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.50138,30.05388,50.14304,30.05666), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.14304,30.05666,49.50138,30.05388), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.37943,30.12944,49.81693,30.14722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.81693,30.14722,49.48749,30.14833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.48749,30.14833,49.81693,30.14722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.23193,30.15777,49.45554,30.16278), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.45554,30.16278,49.23193,30.15777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.94332,30.16278,49.23193,30.15777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.06248,30.19833,48.40276,30.20277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.40276,30.20277,49.91527,30.20417), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.91527,30.20417,48.40276,30.20277), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.00638,30.21888,49.15471,30.22499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.15471,30.22499,50.00638,30.21888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.91805,30.2486,61.25304,30.25999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.25304,30.25999,49.21665,30.26472), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.21665,30.26472,61.25304,30.25999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.19804,30.31777,48.27415,30.32916), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.27415,30.32916,48.19804,30.31777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.86388,30.34722,49.2111,30.35527), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.2111,30.35527,48.86388,30.34722), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.92387,30.38083,48.95749,30.39805), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.95749,30.39805,48.91415,30.40805), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.91415,30.40805,49.00526,30.4086), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.00526,30.4086,48.91415,30.40805), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.15359,30.42166,49.00526,30.4086), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.26054,30.44305,49.0236,30.45499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.0236,30.45499,48.06959,30.46073), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.06959,30.46073,49.0236,30.45499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.94388,30.47666,48.06959,30.46073), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.95304,30.49972,49.10027,30.51611), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.10027,30.51611,48.95304,30.49972), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.03333,30.5536,49.10027,30.51611), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.81276,30.84361,61.80387,30.94582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.80387,30.94582,48.03693,30.99471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.03693,30.99471,47.69387,31.00111), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.69387,31.00111,48.03693,30.99471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.84721,31.04888,47.69387,31.00111), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.76582,31.24527,61.7711,31.31833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.7711,31.31833,61.7136,31.38333), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.7136,31.38333,61.61943,31.39582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.61943,31.39582,47.6972,31.40777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.6972,31.40777,61.61943,31.39582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.84387,31.49833,47.6972,31.40777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.86443,31.7986,60.81026,31.8736), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.81026,31.8736,47.86443,31.7986), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.5172,32.14832,60.85777,32.23472), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.85777,32.23472,47.50304,32.2536), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.50304,32.2536,60.85777,32.23472), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.41331,32.34109,47.44082,32.38332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.44082,32.38332,47.41331,32.34109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.1547,32.45776,47.36249,32.4747), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.36249,32.4747,47.1547,32.45776), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.74165,32.57887,47.36249,32.4747), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.78304,32.70833,46.63804,32.81999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.63804,32.81999,46.78304,32.70833), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.42387,32.93748,46.14804,32.95304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.14804,32.95304,46.2836,32.96666), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.2836,32.96666,46.10332,32.97109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.10332,32.97109,46.2836,32.96666), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.09748,33.00555,46.10332,32.97109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.14499,33.04804,46.14638,33.06944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.14638,33.06944,60.58193,33.07166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.58193,33.07166,46.14638,33.06944), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.04916,33.09082,60.58193,33.07166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.11137,33.11276,46.05554,33.12165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.05554,33.12165,46.11137,33.11276), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.59109,33.16304,46.19859,33.19109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.19859,33.19109,60.59109,33.16304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.1247,33.3086,60.85165,33.41805), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.85165,33.41805,45.8747,33.49165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.8747,33.49165,60.85832,33.49387), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.85832,33.49387,45.99693,33.49554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.99693,33.49554,60.85832,33.49387), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.9286,33.50443,45.99693,33.49554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.90165,33.55443,45.94859,33.55665), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.94859,33.55665,60.90165,33.55443), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.64915,33.57499,45.75193,33.58887), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.75193,33.58887,60.64915,33.57499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.90549,33.63319,60.52276,33.65304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.52276,33.65304,45.90549,33.63319), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.50526,33.73915,60.55415,33.81332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.55415,33.81332,60.50526,33.73915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.55026,33.8886,60.55415,33.81332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.40582,33.97109,60.52137,33.99915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.52137,33.99915,45.40582,33.97109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.5136,34.15082,45.56749,34.2197), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.56749,34.2197,60.5136,34.15082), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.58471,34.30248,60.67165,34.3136), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.67165,34.3136,60.91109,34.31638), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.91109,34.31638,60.67165,34.3136), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.48776,34.33582,45.55193,34.34415), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.55193,34.34415,60.89777,34.34582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.89777,34.34582,45.55193,34.34415), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.43554,34.44859,60.75665,34.48332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.75665,34.48332,45.52276,34.49776), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.52276,34.49776,60.75665,34.48332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.7336,34.54166,45.71471,34.55721), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.71471,34.55721,60.7336,34.54166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.8636,34.57639,45.51332,34.58166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.51332,34.58166,60.8636,34.57639), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.53193,34.60054,45.51332,34.58166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.70832,34.65915,45.53193,34.60054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.65109,34.72693,45.70832,34.65915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.06526,34.81276,45.69193,34.81915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.69193,34.81915,61.06526,34.81276), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.7661,34.84582,45.69193,34.81915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.87026,34.90971,45.77248,34.91443), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.77248,34.91443,45.87026,34.90971), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.03249,35.05776,45.92137,35.0786), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.92137,35.0786,45.94193,35.09554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.94193,35.09554,46.15526,35.09915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.15526,35.09915,45.94193,35.09554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.11388,35.20165,46.19304,35.2111), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.19304,35.2111,61.11388,35.20165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.11915,35.24443,46.19304,35.2111), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.10443,35.27915,61.1861,35.29694), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.1861,35.29694,46.14804,35.30165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.14804,35.30165,61.1861,35.29694), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.99221,35.48109,61.27776,35.52026), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.23915,35.48109,61.27776,35.52026), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.27776,35.52026,45.99221,35.48109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.02026,35.5761,45.97998,35.58471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.97998,35.58471,46.02026,35.5761), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.27872,35.60675,45.97998,35.58471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.23109,35.66749,46.01915,35.67554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.01915,35.67554,61.23109,35.66749), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.13443,35.6972,46.01915,35.67554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.3186,35.76971,46.34887,35.79999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.34887,35.79999,45.78526,35.81499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.78526,35.81499,46.34026,35.82555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.34026,35.82555,45.74332,35.82804), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.74332,35.82804,46.34026,35.82555), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.24499,35.89721,45.63499,35.96554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.63499,35.96554,61.12526,35.97082), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.12526,35.97082,45.63499,35.96554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.38471,35.98193,61.12526,35.97082), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.33498,36.00443,45.50555,36.02054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.50555,36.02054,45.33498,36.00443), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.36915,36.08027,61.22609,36.11526), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.22609,36.11526,45.36915,36.08027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.16998,36.30221,61.14249,36.39276), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.14249,36.39276,45.12137,36.41193), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.12137,36.41193,45.25082,36.42027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.25082,36.42027,45.12137,36.41193), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.01471,36.53526,61.1897,36.56137), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.1897,36.56137,51.97276,36.57999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.97276,36.57999,61.1897,36.56137), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.7986,36.60194,51.97276,36.57999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.36471,36.64554,61.1572,36.64999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((61.1572,36.64999,60.36471,36.64554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.06666,36.67387,61.1572,36.64999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((52.61555,36.70915,51.08749,36.73249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((51.08749,36.73249,45.01693,36.74999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.01693,36.74999,51.08749,36.73249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.8672,36.78526,53.89471,36.7936), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.89471,36.7936,44.8672,36.78526), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.01916,36.8186,44.84165,36.81971), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.84165,36.81971,54.01916,36.8186), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.6686,36.82082,44.84165,36.81971), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.24471,36.85027,53.63443,36.85332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.63443,36.85332,53.24471,36.85027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.90665,36.88832,50.74082,36.90083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.74082,36.90083,54.00221,36.90499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.00221,36.90499,50.74082,36.90083), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.94916,36.91471,54.00221,36.90499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.0086,36.94832,53.94916,36.91471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.03165,36.94832,53.94916,36.91471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.98832,36.98221,54.0086,36.94832), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.01582,37.02193,44.90971,37.02387), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.90971,37.02387,54.01582,37.02193), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((60.02943,37.03693,44.90971,37.02387), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.76804,37.10526,59.77443,37.13165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.77443,37.13165,59.62026,37.13193), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.62026,37.13193,59.77443,37.13165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.78667,37.14815,50.31527,37.15305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.31527,37.15305,44.78667,37.14815), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.79443,37.16387,50.31527,37.15305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.51971,37.19609,59.56693,37.20888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.56693,37.20888,59.51971,37.19609), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.77332,37.22748,59.56693,37.20888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.82193,37.2711,44.77332,37.22748), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.80165,37.32166,54.23165,37.32721), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.23165,37.32721,44.80165,37.32166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.38971,37.33305,54.23165,37.32721), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((53.90942,37.35152,59.38971,37.33305), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.20221,37.38388,50.07749,37.41165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((50.07749,37.41165,44.59276,37.43693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.59276,37.43693,54.55165,37.44609), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.55165,37.44609,44.59276,37.43693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.69165,37.46748,49.47887,37.4836), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.47887,37.4836,54.69165,37.46748), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.37109,37.50388,59.24999,37.51332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.24999,37.51332,59.37109,37.50388), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.32276,37.54137,49.27471,37.54582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.27471,37.54582,59.32276,37.54137), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.80582,37.56165,59.17165,37.56304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((59.17165,37.56304,54.80582,37.56165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.45165,37.64137,58.49804,37.64777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.49804,37.64777,44.55804,37.64804), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.55804,37.64804,58.49804,37.64777), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.77859,37.66193,58.92609,37.66998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.92609,37.66998,49.06971,37.67165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.06971,37.67165,58.92609,37.66998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.22387,37.68471,49.06971,37.67165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.54443,37.7036,44.61887,37.71609), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.61887,37.71609,58.54443,37.7036), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((54.83305,37.74638,49.0011,37.75943), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((49.0011,37.75943,54.83305,37.74638), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.53804,37.78054,58.19804,37.78638), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.19804,37.78638,44.53804,37.78054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((58.12443,37.79999,58.19804,37.78638), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.41915,37.81776,58.12443,37.79999), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.34499,37.88026,44.24554,37.88387), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.24554,37.88387,44.34499,37.88026), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.22221,37.90637,57.77832,37.90776), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.77832,37.90776,44.22221,37.90637), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.50526,37.92999,57.77832,37.90776), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.34832,37.99887,55.2961,38.00166), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.2961,38.00166,57.34832,37.99887), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.98276,38.07249,56.32665,38.08415), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.32665,38.08415,55.44276,38.0861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.44276,38.0861,56.32665,38.08415), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.37109,38.09304,55.44276,38.0861), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((55.7986,38.12248,56.35277,38.13276), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.35277,38.13276,55.7986,38.12248), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.31888,38.17332,57.05082,38.1936), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.05082,38.1936,56.31888,38.17332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.85721,38.23137,56.53888,38.2661), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.38554,38.23137,56.53888,38.2661), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.53888,38.2661,56.69526,38.26693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.69526,38.26693,56.53888,38.2661), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.23915,38.27387,57.17609,38.28027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((57.17609,38.28027,56.75638,38.28499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((56.75638,38.28499,48.87138,38.28888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.87138,38.28888,56.75638,38.28499), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.47776,38.32304,48.87138,38.28888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.32526,38.37804,48.66553,38.39054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.66553,38.39054,44.44082,38.39332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.44082,38.39332,48.66553,38.39054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.61054,38.40582,44.44082,38.39332), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.3036,38.43054,48.8605,38.44025), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.8605,38.44025,44.3036,38.43054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.3336,38.6011,44.31721,38.61304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.31721,38.61304,48.3336,38.6011), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.42999,38.62609,44.31721,38.61304), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.2436,38.66721,48.42999,38.62609), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.26082,38.72165,48.24387,38.72776), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.24387,38.72776,44.26082,38.72165), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.10304,38.7836,44.30499,38.81554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.30499,38.81554,46.16666,38.83998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.16666,38.83998,46.17796,38.84422), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.17796,38.84422,46.16666,38.83998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.01388,38.85693,44.2911,38.85971), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.2911,38.85971,48.01388,38.85693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.5336,38.87082,46.53992,38.87672), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.53992,38.87672,45.87193,38.87998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.87193,38.87998,46.53992,38.87672), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.21082,38.8911,45.87193,38.87998), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.01888,38.90915,46.35471,38.91054), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.35471,38.91054,48.01888,38.90915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.27609,38.98165,44.15942,39.00221), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.15942,39.00221,45.43054,39.00471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.43054,39.00471,44.15942,39.00221), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.32388,39.02637,45.43054,39.00471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.29388,39.11249,44.21665,39.12915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.21665,39.12915,48.29388,39.11249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((46.83832,39.15526,45.33776,39.17249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.33776,39.17249,47.02554,39.18942), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.02554,39.18942,45.33776,39.17249), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.13499,39.20888,45.18748,39.21027), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.18748,39.21027,48.13499,39.20888), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.11998,39.2636,47.09971,39.30443), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.09971,39.30443,44.08082,39.30693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.08082,39.30693,48.15026,39.30915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.15026,39.30915,44.08082,39.30693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((45.11582,39.3122,48.15026,39.30915), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.03416,39.37971,48.35971,39.38471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.35971,39.38471,44.30776,39.38693), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.30776,39.38693,48.35971,39.38471), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.07027,39.41165,44.41609,39.42526), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.41609,39.42526,48.33887,39.42554), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((48.33887,39.42554,44.41609,39.42526), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.95026,39.43581,47.35526,39.43748), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.35526,39.43748,44.95026,39.43581), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.45443,39.49638,47.35526,39.43748), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.8886,39.60582,47.74693,39.62109), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.74693,39.62109,44.8886,39.60582), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.81049,39.64273,44.79465,39.65065), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.79465,39.65065,44.81049,39.64273), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.79465,39.65065,44.81049,39.64273), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.80665,39.67721,44.47109,39.69887), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.47109,39.69887,47.97637,39.7197), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((47.97637,39.7197,44.47109,39.69887), mapfile, tile_dir, 0, 11, "ir-iran")
	render_tiles((44.60582,39.78054,47.97637,39.7197), mapfile, tile_dir, 0, 11, "ir-iran")