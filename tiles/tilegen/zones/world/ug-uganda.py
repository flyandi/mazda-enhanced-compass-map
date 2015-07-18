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
    # Region: UG
    # Region Name: Uganda

	render_tiles((29.97499,-1.46444,29.90194,-1.45583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.90194,-1.45583,29.97499,-1.46444), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.59747,-1.38525,29.88333,-1.36778), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.88333,-1.36778,29.59747,-1.38525), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.17055,-1.33972,29.82916,-1.31917), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.82916,-1.31917,30.17055,-1.33972), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.18472,-1.275,29.82916,-1.31917), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.27972,-1.21444,30.18472,-1.275), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.64138,-1.07222,30.3561,-1.065), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.3561,-1.065,30.48231,-1.06162), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.48231,-1.06162,30.3561,-1.065), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.92033,-1.00149,33.43332,-1.00028), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.43332,-1.00028,32.76665,-0.99972), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.76665,-0.99972,32.09443,-0.99944), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.09443,-0.99944,31.83566,-0.99934), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.83566,-0.99934,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.83986,-0.99934,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.76553,-0.99931,31.83566,-0.99934), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.4236,-0.99917,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.75118,-0.99758,31.4236,-0.99917), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.57833,-0.9,29.62833,-0.88889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.62833,-0.88889,29.57833,-0.9), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.63277,-0.64611,29.67194,-0.57222), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.67194,-0.57222,29.63277,-0.64611), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.6527,-0.47612,29.653,-0.47406), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.653,-0.47406,29.6527,-0.47612), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.91859,-0.45278,29.653,-0.47406), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.97609,-0.13417,29.70989,-0.07581), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.70989,-0.07581,29.71082,-0.0693), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.71082,-0.0693,29.70989,-0.07581), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.71666,0.07222,33.9072,0.10306), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.9072,0.10306,29.71666,0.07222), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.81388,0.15861,29.77722,0.17528), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.77722,0.17528,29.81388,0.15861), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.99947,0.23042,34.00423,0.23699), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.99947,0.23042,34.00423,0.23699), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.00423,0.23699,33.99947,0.23042), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.10777,0.35694,29.85833,0.36667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.85833,0.36667,34.10777,0.35694), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.96249,0.48722,29.94416,0.5725), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.94416,0.5725,34.15998,0.60306), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.15998,0.60306,29.94416,0.5725), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.26082,0.64111,34.15998,0.60306), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.2911,0.68639,34.26082,0.64111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.41081,0.82194,29.96189,0.83036), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((29.96189,0.83036,34.41081,0.82194), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.2161,0.9925,34.57748,1.0925), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.57748,1.0925,34.52744,1.11342), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.52744,1.11342,30.23166,1.125), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.23166,1.125,34.52744,1.11342), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.33221,1.15139,34.60582,1.15889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.60582,1.15889,30.33221,1.15139), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.27805,1.16889,34.60582,1.15889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.3486,1.19611,30.27805,1.16889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.81971,1.23139,30.47239,1.2316), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.47239,1.2316,34.81971,1.23139), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.47565,1.23253,30.47239,1.2316), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.82971,1.28889,30.47565,1.23253), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.79221,1.39361,34.82971,1.28889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.69333,1.49889,34.79221,1.39361), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.99999,1.67194,31.03666,1.76556), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.03666,1.76556,34.99999,1.67194), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.99943,1.87667,35.02609,1.91972), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((35.02609,1.91972,34.99943,1.87667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.97915,1.99472,35.02609,1.91972), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.99499,2.09111,31.30277,2.12139), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.30277,2.12139,34.99499,2.09111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.27972,2.17667,31.27745,2.1782), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.27745,2.1782,31.27972,2.17667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.9447,2.21305,31.20249,2.22917), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.20249,2.22917,34.9447,2.21305), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.04972,2.30389,31.19694,2.30583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.19694,2.30583,31.04972,2.30389), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.07027,2.335,30.89805,2.33528), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.89805,2.33528,31.07027,2.335), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.86221,2.35361,30.89805,2.33528), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.98721,2.40833,30.94638,2.40889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.94638,2.40889,30.98721,2.40833), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.8822,2.41305,30.94638,2.40889), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.82666,2.44222,30.72916,2.44778), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.72916,2.44778,34.94054,2.45056), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.94054,2.45056,30.72916,2.44778), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.93581,2.50694,34.94054,2.45056), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.83915,2.60389,30.78694,2.67472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.78694,2.67472,34.83915,2.60389), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.85638,2.79083,34.74971,2.85583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.74971,2.85583,34.6622,2.86111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.6622,2.86111,34.74971,2.85583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.87777,2.89722,34.6622,2.86111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.59082,2.93944,30.87777,2.89722), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.82499,2.98805,34.59082,2.93944), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.76361,3.06722,34.55915,3.11111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.55915,3.11111,30.76361,3.06722), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.45415,3.18194,34.55915,3.11111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.40498,3.40667,30.93555,3.42472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.93555,3.42472,34.40498,3.40667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.86852,3.49214,32.19331,3.51139), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.19331,3.51139,34.45554,3.52417), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.45554,3.52417,30.90916,3.52583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.90916,3.52583,34.45554,3.52417), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.0936,3.5325,30.90916,3.52583), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.06387,3.59028,31.95027,3.59472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.95027,3.59472,32.06387,3.59028), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.19526,3.61194,31.95027,3.59472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.54083,3.65361,30.95471,3.67111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((30.95471,3.67111,34.46332,3.67139), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.46332,3.67139,30.95471,3.67111), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.93055,3.69194,31.66194,3.70555), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.66194,3.70555,34.29943,3.70667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.29943,3.70667,31.66194,3.70555), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.70193,3.71305,34.29943,3.70667), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.35499,3.73861,32.40443,3.74361), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.40443,3.74361,34.35499,3.73861), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.51693,3.75222,32.71665,3.75528), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((32.71665,3.75528,33.51693,3.75222), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.18387,3.76611,34.17609,3.77555), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.17609,3.77555,33.18387,3.76611), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.2636,3.78639,31.28805,3.79472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.28805,3.79472,31.17666,3.79528), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.17666,3.79528,31.28805,3.79472), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((31.79472,3.82333,34.16331,3.82722), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.16331,3.82722,31.79472,3.82333), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.21054,3.84055,34.16331,3.82722), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.09943,3.88,34.21415,3.8875), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.21415,3.8875,33.01665,3.88861), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.01665,3.88861,34.21415,3.8875), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.12971,3.95444,33.01665,3.88861), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((34.0547,4.18555,33.9976,4.22176), mapfile, tile_dir, 0, 11, "ug-uganda")
	render_tiles((33.9976,4.22176,34.0547,4.18555), mapfile, tile_dir, 0, 11, "ug-uganda")