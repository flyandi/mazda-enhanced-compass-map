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
    # Region: ZW
    # Region Name: Zimbabwe

	render_tiles((31.29763,-22.41614,31.39333,-22.35417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.39333,-22.35417,30.29499,-22.34334), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.29499,-22.34334,31.39333,-22.35417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.15583,-22.32167,30.13305,-22.30056), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.13305,-22.30056,30.86694,-22.29612), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.86694,-22.29612,30.2311,-22.29223), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.2311,-22.29223,30.86694,-22.29612), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.37053,-22.19138,29.45082,-22.16334), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.45082,-22.16334,29.76749,-22.13611), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.76749,-22.13611,29.45082,-22.16334), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.15666,-22.07695,29.25815,-22.06652), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.25815,-22.06652,29.15666,-22.07695), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.07388,-22.03778,29.25815,-22.06652), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.03722,-21.97417,29.07388,-22.03778), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.03777,-21.89473,29.08334,-21.82512), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.08334,-21.82512,29.08083,-21.82084), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.08083,-21.82084,29.08334,-21.82512), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.49194,-21.66083,28.56805,-21.63111), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.56805,-21.63111,28.35722,-21.60306), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.35722,-21.60306,28.20277,-21.59667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.20277,-21.59667,28.35722,-21.60306), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.01277,-21.56195,28.20277,-21.59667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.49217,-21.34646,27.9086,-21.31389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.9086,-21.31389,32.41026,-21.31112), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.41026,-21.31112,27.9086,-21.31389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.73943,-21.14556,32.36027,-21.13584), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.36027,-21.13584,27.73943,-21.14556), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.68527,-21.06362,32.36027,-21.13584), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.52137,-20.91417,27.71944,-20.81195), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.71944,-20.81195,32.52137,-20.91417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.4836,-20.66167,27.70082,-20.60667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.70082,-20.60667,32.50221,-20.59861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.50221,-20.59861,27.70082,-20.60667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.72471,-20.5675,32.66582,-20.55723), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.66582,-20.55723,32.55082,-20.555), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.55082,-20.555,32.66582,-20.55723), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.71397,-20.50986,27.28749,-20.49472), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.28749,-20.49472,27.71397,-20.50986), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.35722,-20.465,27.28749,-20.49472), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.29721,-20.30251,27.35722,-20.465), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.90109,-20.13473,27.21999,-20.09167), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.21999,-20.09167,32.90109,-20.13473), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.95304,-20.03639,33.02665,-20.03112), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.02665,-20.03112,32.95304,-20.03639), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.96971,-20.00973,27.02666,-20.00028), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.02666,-20.00028,26.96971,-20.00973), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.61082,-19.85445,33.03915,-19.81389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.03915,-19.81389,26.5886,-19.79917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.5886,-19.79917,33.03915,-19.81389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.05943,-19.78028,26.5886,-19.79917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.97581,-19.73667,26.4411,-19.73111), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.4411,-19.73111,32.97581,-19.73667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.91276,-19.6925,32.84554,-19.685), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.84554,-19.685,32.91276,-19.6925), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.40444,-19.67583,32.84554,-19.685), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.97859,-19.66417,26.32582,-19.65445), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.32582,-19.65445,32.9536,-19.64861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.9536,-19.64861,26.32582,-19.65445), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.34805,-19.59723,32.9536,-19.64861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.19194,-19.54473,32.85054,-19.49389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.85054,-19.49389,32.78304,-19.46778), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.78304,-19.46778,32.85054,-19.49389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.78471,-19.36639,32.85221,-19.28667), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.85221,-19.28667,32.78471,-19.36639), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.96194,-19.10028,32.84526,-19.03723), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.84526,-19.03723,32.71609,-19.02195), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.71609,-19.02195,32.84526,-19.03723), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.97332,-18.94556,32.69915,-18.94445), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.69915,-18.94445,25.97332,-18.94556), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.72026,-18.88278,32.7011,-18.83695), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.7011,-18.83695,32.72026,-18.88278), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.89999,-18.79111,32.81721,-18.77917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.81721,-18.77917,25.80833,-18.77695), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.80833,-18.77695,32.81721,-18.77917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.92804,-18.76723,25.80833,-18.77695), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.94971,-18.69028,25.79749,-18.68167), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.79749,-18.68167,32.94971,-18.69028), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.65944,-18.53112,32.88832,-18.53056), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.88832,-18.53056,25.65944,-18.53112), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.0136,-18.46695,33.00144,-18.40503), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.00144,-18.40503,33.07304,-18.34889), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.07304,-18.34889,33.00144,-18.40503), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.96665,-18.23584,33.0011,-18.18333), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.0011,-18.18333,32.96665,-18.23584), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.39333,-18.12251,32.97581,-18.10139), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.97581,-18.10139,25.39333,-18.12251), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.68916,-18.07528,25.30916,-18.06584), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.30916,-18.06584,26.68916,-18.07528), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.97166,-18.00639,32.94609,-17.975), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.94609,-17.975,25.85971,-17.97334), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.85971,-17.97334,32.94609,-17.975), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.46277,-17.96861,27.02077,-17.96418), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.02077,-17.96418,26.46277,-17.96861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.03141,-17.95545,27.02077,-17.96418), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.31472,-17.93584,32.9747,-17.92361), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.9747,-17.92361,26.31472,-17.93584), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.85416,-17.90862,25.23666,-17.89445), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.23666,-17.89445,26.21499,-17.88417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((26.21499,-17.88417,27.12243,-17.88076), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.12243,-17.88076,26.21499,-17.88417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.39194,-17.85084,27.14622,-17.84528), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.14622,-17.84528,32.96609,-17.84223), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.96609,-17.84223,25.59972,-17.84139), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.59972,-17.84139,32.96609,-17.84223), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.71999,-17.83639,25.59972,-17.84139), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.14582,-17.80445,25.26575,-17.79766), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((25.26575,-17.79766,27.14582,-17.80445), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.01859,-17.72334,25.26575,-17.79766), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.04332,-17.61389,32.96027,-17.52084), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.96027,-17.52084,33.04332,-17.61389), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((33.04492,-17.34626,27.61916,-17.33723), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.61916,-17.33723,33.04492,-17.34626), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.63888,-17.22472,32.96832,-17.1475), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.96832,-17.1475,27.63888,-17.22472), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((27.82527,-16.95917,32.84248,-16.93111), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.84248,-16.93111,27.82527,-16.95917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.94387,-16.83278,28.1386,-16.82362), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.1386,-16.82362,32.94387,-16.83278), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.25999,-16.72417,32.7685,-16.71782), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.7685,-16.71782,28.25999,-16.72417), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.9822,-16.70861,32.7685,-16.71782), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.70776,-16.68417,32.9822,-16.70861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.70832,-16.60778,32.65526,-16.58139), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.65526,-16.58139,32.70832,-16.60778), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.75976,-16.53767,28.76191,-16.5344), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.75976,-16.53767,28.76191,-16.5344), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.76191,-16.5344,28.75976,-16.53767), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.06276,-16.44917,32.24026,-16.43889), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((32.24026,-16.43889,32.06276,-16.44917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.90805,-16.41833,28.85055,-16.39973), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.85055,-16.39973,31.90805,-16.41833), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.76055,-16.24,31.42388,-16.16111), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.42388,-16.16111,31.76055,-16.24), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.85833,-16.0625,30.98471,-16.05917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.98471,-16.05917,28.85833,-16.0625), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.30083,-16.02862,31.05805,-16.02306), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((31.05805,-16.02306,31.30083,-16.02862), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.42277,-16.00917,30.9286,-16.00223), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.9286,-16.00223,30.42277,-16.00917), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((28.92722,-15.97222,30.9286,-16.00223), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.13805,-15.86,29.31916,-15.74861), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.31916,-15.74861,30.37805,-15.65056), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.37805,-15.65056,30.41388,-15.63361), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((30.41388,-15.63361,30.37805,-15.65056), mapfile, tile_dir, 0, 11, "zw-zimbabwe")
	render_tiles((29.83138,-15.61611,30.41388,-15.63361), mapfile, tile_dir, 0, 11, "zw-zimbabwe")