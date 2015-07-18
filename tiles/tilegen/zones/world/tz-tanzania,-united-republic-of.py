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
    # Region: TZ
    # Region Name: Tanzania, United Republic of

	render_tiles((39.5161,-6.46861,39.4736,-6.46111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.5161,-6.46861,39.4736,-6.46111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.4736,-6.46111,39.5161,-6.46861), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.55916,-6.44417,39.4736,-6.46111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.57416,-6.38084,39.39665,-6.38), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.39665,-6.38,39.57416,-6.38084), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.41193,-6.35833,39.39665,-6.38), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.37415,-6.32306,39.27693,-6.32), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.27693,-6.32,39.37415,-6.32306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.28999,-6.24722,39.53249,-6.24667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.53249,-6.24667,39.28999,-6.24722), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.20832,-6.24667,39.28999,-6.24722), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.5036,-6.19834,39.4186,-6.19139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.4186,-6.19139,39.5036,-6.19834), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.50027,-6.15389,39.4186,-6.19139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.40221,-6.03334,39.18916,-5.93917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.18916,-5.93917,39.35416,-5.86695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.35416,-5.86695,39.18916,-5.93917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.34249,-5.76472,39.30916,-5.72306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.30916,-5.72306,39.34249,-5.76472), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.65305,-7.99695,39.72166,-7.97778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.65305,-7.99695,39.72166,-7.97778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.72166,-7.97778,39.65305,-7.99695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.59332,-7.95611,39.72166,-7.97778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.74221,-7.93222,39.59332,-7.95611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.82638,-7.90722,39.74221,-7.93222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.71054,-7.81361,39.84082,-7.75111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.84082,-7.75111,39.71054,-7.81361), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.90999,-7.67778,39.89777,-7.6425), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.89777,-7.6425,39.90999,-7.67778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7361,-5.44083,39.69554,-5.4375), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7361,-5.44083,39.69554,-5.4375), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69554,-5.4375,39.7361,-5.44083), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.64749,-5.43333,39.69554,-5.4375), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.78471,-5.39444,39.64749,-5.43333), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.64888,-5.34389,39.69693,-5.33917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69693,-5.33917,39.64888,-5.34389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.66527,-5.29611,39.69693,-5.33917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.77776,-5.24833,39.73943,-5.20945), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.73943,-5.20945,39.85582,-5.20694), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.85582,-5.20694,39.67165,-5.20556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.67165,-5.20556,39.85582,-5.20694), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.74554,-5.16306,39.83249,-5.12778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.83249,-5.12778,39.69082,-5.11195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69082,-5.11195,39.83249,-5.12778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69832,-5.0225,39.87082,-4.99861), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.87082,-4.99861,39.69832,-5.0225), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.84221,-4.96584,39.75721,-4.93722), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.75721,-4.93722,39.84221,-4.96584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.86694,-4.8975,39.67776,-4.87167), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.67776,-4.87167,39.86694,-4.8975), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.53721,-11.72695,37.44026,-11.72556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.44026,-11.72556,36.53721,-11.72695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.67221,-11.71611,37.44026,-11.72556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.2861,-11.70417,36.67221,-11.71611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.18554,-11.70417,36.67221,-11.71611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.34721,-11.68667,37.2861,-11.70417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.10804,-11.66445,37.34721,-11.68667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.47637,-11.59806,35.6436,-11.58945), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.6436,-11.58945,35.47637,-11.59806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.16304,-11.57778,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.96201,-11.5729,35.41914,-11.57212), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.41914,-11.57212,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.82499,-11.57056,35.41914,-11.57212), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.02582,-11.56723,34.96325,-11.56566), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.96325,-11.56566,37.02582,-11.56723), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.79193,-11.56111,34.96325,-11.56566), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.08637,-11.54306,37.79193,-11.56111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.82471,-11.51472,36.08637,-11.54306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.93332,-11.43084,34.91859,-11.42806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.91859,-11.42806,35.93332,-11.43084), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.81693,-11.42083,38.49331,-11.4171), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.49331,-11.4171,35.81693,-11.42083), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.84332,-11.40528,38.49331,-11.4171), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.7636,-11.345,34.82332,-11.34028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.82332,-11.34028,34.7636,-11.345), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.86859,-11.32667,34.82332,-11.34028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.67249,-11.27084,38.10221,-11.25334), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.10221,-11.25334,38.67249,-11.27084), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.85277,-11.20722,38.89777,-11.17222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.89777,-11.17222,39.25694,-11.17083), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.25694,-11.17083,38.89777,-11.17222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.66859,-11.16334,39.25694,-11.17083), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.59387,-11.02833,39.62832,-10.94806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.62832,-10.94806,39.76693,-10.92056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.76693,-10.92056,39.62832,-10.94806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.67609,-10.7475,40.16553,-10.67139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.16553,-10.67139,34.67609,-10.7475), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.27387,-10.58667,40.42054,-10.50584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.42054,-10.50584,40.43555,-10.48045), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.43555,-10.48045,40.42054,-10.50584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.57054,-10.44445,40.43555,-10.48045), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.3561,-10.35723,40.45332,-10.34889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.45332,-10.34889,40.43249,-10.34472), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.43249,-10.34472,40.45332,-10.34889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.39416,-10.30584,40.22305,-10.29723), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.22305,-10.29723,40.39416,-10.30584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.11832,-10.27528,40.26721,-10.26917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.26721,-10.26917,40.11832,-10.27528), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.1786,-10.26056,40.26721,-10.26917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.19888,-10.24472,40.1786,-10.26056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.9936,-10.21917,40.24055,-10.2025), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.24055,-10.2025,39.9936,-10.21917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.55804,-10.18028,40.13915,-10.17973), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((40.13915,-10.17973,34.55804,-10.18028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.99638,-10.125,40.13915,-10.17973), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.92999,-10.05584,39.69804,-10.05195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69804,-10.05195,39.92999,-10.05584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7886,-9.98806,39.7086,-9.95972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7086,-9.95972,39.78555,-9.93222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.78555,-9.93222,39.7086,-9.95972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.44776,-9.89944,39.78555,-9.93222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7486,-9.7525,39.69916,-9.75056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.69916,-9.75056,39.7486,-9.7525), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.92814,-9.69981,39.7436,-9.67195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.7436,-9.67195,33.92814,-9.69981), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.50888,-9.62195,33.42887,-9.61195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.42887,-9.61195,33.67693,-9.61056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.67693,-9.61056,33.42887,-9.61195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.57887,-9.58472,33.75582,-9.58278), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.75582,-9.58278,33.57887,-9.58472), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.13387,-9.56723,33.75582,-9.58278), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.95026,-9.54639,33.39388,-9.53806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.39388,-9.53806,33.95026,-9.54639), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.19248,-9.50917,33.98859,-9.49778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.98859,-9.49778,33.19248,-9.50917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.0436,-9.48472,33.30193,-9.48417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.30193,-9.48417,34.0436,-9.48472), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.65137,-9.45306,39.56554,-9.44889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.56554,-9.44889,39.65137,-9.45306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.02859,-9.41167,39.5911,-9.40639), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.5911,-9.40639,32.94068,-9.40627), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.94068,-9.40627,39.5911,-9.40639), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.94026,-9.40361,32.94068,-9.40627), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.63999,-9.38084,32.99776,-9.37333), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.99776,-9.37333,32.83082,-9.36639), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.83082,-9.36639,32.99776,-9.37333), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.74193,-9.28167,32.53693,-9.26111), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.53693,-9.26111,32.74193,-9.28167), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.64638,-9.19917,32.47082,-9.16056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.47082,-9.16056,39.49999,-9.12917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.49999,-9.12917,39.57332,-9.09889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.57332,-9.09889,31.98444,-9.07306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.98444,-9.07306,39.57332,-9.09889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.04221,-9.04084,31.93749,-9.02917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.93749,-9.02917,32.04221,-9.04084), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.93138,-8.98,39.49582,-8.94389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.49582,-8.94389,31.9561,-8.93084), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.9561,-8.93084,39.49582,-8.94389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.5436,-8.91556,39.39249,-8.91139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.39249,-8.91139,31.6836,-8.90889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.6836,-8.90889,39.39249,-8.91139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.78555,-8.885,39.40527,-8.86695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.40527,-8.86695,39.44804,-8.85889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.44804,-8.85889,39.40527,-8.86695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.57305,-8.81861,39.44804,-8.85889), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.35582,-8.71695,31.55166,-8.68917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.55166,-8.68917,39.35582,-8.71695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.39805,-8.62944,31.27305,-8.62195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.27305,-8.62195,31.12582,-8.61556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.12582,-8.61556,31.27305,-8.62195), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.16835,-8.59614,31.16999,-8.59538), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.16999,-8.59538,31.16835,-8.59614), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.0411,-8.59028,31.16999,-8.59538), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.37555,-8.58222,31.22055,-8.57667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.22055,-8.57667,31.37555,-8.58222), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.34554,-8.55667,31.22055,-8.57667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.3036,-8.50584,30.8986,-8.45583), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.8986,-8.45583,39.3036,-8.50584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.3411,-8.27861,39.29527,-8.26778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.29527,-8.26778,39.3411,-8.27861), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.76999,-8.19083,39.29527,-8.26778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.7086,-8.00028,39.43277,-7.96056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.43277,-7.96056,30.7086,-8.00028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.29027,-7.82167,39.44666,-7.81417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.44666,-7.81417,39.29027,-7.82167), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.32499,-7.73111,39.44666,-7.81417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.45583,-7.58028,39.29971,-7.45917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.29971,-7.45917,39.35387,-7.37361), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.35387,-7.37361,39.29971,-7.45917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.38194,-7.28389,39.39165,-7.26361), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.39165,-7.26361,30.38194,-7.28389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.50277,-7.18278,39.48082,-7.15584), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.48082,-7.15584,39.50277,-7.18278), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.54999,-7.01528,30.20749,-6.9925), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.20749,-6.9925,39.54999,-7.01528), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.46888,-6.8625,39.28999,-6.85278), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.28999,-6.85278,39.46888,-6.8625), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.82305,-6.69833,39.21777,-6.65722), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.21777,-6.65722,29.82305,-6.69833), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.01693,-6.48972,39.0311,-6.46417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.0311,-6.46417,39.01693,-6.48972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.87388,-6.38917,39.0311,-6.46417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.55027,-6.29528,38.84332,-6.26778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.84332,-6.26778,29.55027,-6.29528), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.86304,-6.17445,38.84332,-6.26778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.77999,-6.05583,29.49666,-6.04389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.49666,-6.04389,38.77999,-6.05583), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.50471,-5.94583,29.49666,-6.04389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((38.8286,-5.83222,29.63194,-5.74833), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.63194,-5.74833,29.62166,-5.66917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.62166,-5.66917,29.63194,-5.74833), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.53444,-5.44806,39.06666,-5.235), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.06666,-5.235,39.12166,-5.11167), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.12166,-5.11167,39.0861,-5.06667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.0861,-5.06667,39.12693,-5.05139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.12693,-5.05139,39.0861,-5.06667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.1011,-5.02278,39.12693,-5.05139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.1311,-4.97084,29.35138,-4.95139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.35138,-4.95139,39.16444,-4.94028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.16444,-4.94028,29.35138,-4.95139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.14777,-4.87972,39.16444,-4.94028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.22083,-4.81778,29.34138,-4.79778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.34138,-4.79778,39.16805,-4.78556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.16805,-4.78556,29.34138,-4.79778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.21665,-4.75722,39.16805,-4.78556), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.20562,-4.67312,39.11998,-4.61), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((39.11998,-4.61,39.20562,-4.67312), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.43082,-4.54111,39.11998,-4.61), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.71194,-4.45472,29.65313,-4.45365), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.65313,-4.45365,29.71194,-4.45472), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.42359,-4.44947,29.65313,-4.45365), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.42359,-4.44947,29.65313,-4.45365), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.78885,-4.4044,29.81277,-4.36028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.81277,-4.36028,29.90583,-4.34972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((29.90583,-4.34972,29.81277,-4.36028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.04444,-4.24639,30.07277,-4.16667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.07277,-4.16667,30.04444,-4.24639), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.20777,-4.02611,30.25583,-3.88695), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.25583,-3.88695,30.40083,-3.78611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.40083,-3.78611,30.33583,-3.77417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.33583,-3.77417,30.40083,-3.78611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.78336,-3.65143,30.4494,-3.548), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.4494,-3.548,37.7336,-3.52611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.7336,-3.52611,37.6172,-3.5075), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.6172,-3.5075,37.7336,-3.52611), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.63166,-3.45056,37.59999,-3.45028), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.59999,-3.45028,30.63166,-3.45056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.61304,-3.3975,30.6636,-3.38667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.6636,-3.38667,37.61304,-3.3975), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.62416,-3.37139,30.6636,-3.38667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.67833,-3.31667,37.71998,-3.31194), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.71998,-3.31194,30.67833,-3.31667), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.76749,-3.3,37.71998,-3.31194), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.83499,-3.25694,30.76749,-3.3), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.85388,-3.1575,30.8311,-3.11806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.8311,-3.11806,30.85388,-3.1575), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.67526,-3.05139,37.63026,-3.01694), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((37.63026,-3.01694,30.74277,-2.98972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.74277,-2.98972,30.84027,-2.97417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.84027,-2.97417,30.74277,-2.98972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.48516,-2.9465,30.84027,-2.97417), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.55722,-2.89389,30.41749,-2.86194), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.41749,-2.86194,30.55722,-2.89389), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.45721,-2.72306,30.4336,-2.67528), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.4336,-2.67528,30.48916,-2.67083), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.48916,-2.67083,30.4336,-2.67528), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.52246,-2.64964,30.43527,-2.64306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.43527,-2.64306,30.52246,-2.64964), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.57375,-2.3994,30.78202,-2.38075), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.78202,-2.38075,30.57375,-2.3994), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.82916,-2.35778,30.78202,-2.38075), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.29527,-2.27929,30.82916,-2.35778), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.04519,-2.13995,36.01339,-2.12223), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((36.01339,-2.12223,36.04519,-2.13995), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.87638,-2.04695,36.01339,-2.12223), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((35.67137,-1.93167,30.80833,-1.92945), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.80833,-1.92945,35.67137,-1.93167), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.84027,-1.67056,34.98776,-1.55056), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.98776,-1.55056,30.73916,-1.43694), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.73916,-1.43694,30.63166,-1.37306), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.63166,-1.37306,30.73916,-1.43694), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.52277,-1.2275,34.30526,-1.16806), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.30526,-1.16806,30.52277,-1.2275), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.64138,-1.07222,30.48231,-1.06162), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.48231,-1.06162,34.06935,-1.05581), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.06935,-1.05581,30.48231,-1.06162), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.02971,-1.03694,34.06935,-1.05581), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.92033,-1.00149,34.02054,-1.00139), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((34.02054,-1.00139,33.92033,-1.00149), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((33.43332,-1.00028,32.76665,-0.99972), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.76665,-0.99972,32.09443,-0.99944), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((32.09443,-0.99944,31.83566,-0.99934), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.83566,-0.99934,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.83986,-0.99934,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.76553,-0.99931,31.83566,-0.99934), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((31.4236,-0.99917,31.76553,-0.99931), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")
	render_tiles((30.75118,-0.99758,31.4236,-0.99917), mapfile, tile_dir, 0, 11, "tz-tanzania,-united-republic-of")