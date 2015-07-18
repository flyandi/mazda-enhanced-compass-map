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
    # Region: MG
    # Region Name: Madagascar

	render_tiles((45.15887,-25.60028,45.53027,-25.56861), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.53027,-25.56861,45.15887,-25.60028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.6136,-25.52223,45.53027,-25.56861), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.80832,-25.33556,45.94249,-25.31917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.94249,-25.31917,44.71138,-25.30389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.71138,-25.30389,45.94249,-25.31917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.36694,-25.25778,44.38915,-25.23278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.38915,-25.23278,44.41193,-25.21167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.41193,-25.21167,46.64665,-25.19167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.64665,-25.19167,46.33665,-25.17362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.33665,-25.17362,44.29721,-25.16806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.29721,-25.16806,44.31332,-25.16306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.31332,-25.16306,46.50304,-25.16167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.50304,-25.16167,44.31332,-25.16306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.88443,-25.075,44.18443,-25.06917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.18443,-25.06917,46.88443,-25.075), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.97471,-25.04889,44.18443,-25.06917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.03221,-25.00445,47.09444,-24.9739), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.09444,-24.9739,44.03221,-25.00445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.13304,-24.92806,47.10471,-24.88445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.10471,-24.88445,47.13304,-24.92806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.13527,-24.83167,47.10471,-24.88445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.20082,-24.77,47.13527,-24.83167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.84026,-24.51445,47.31554,-24.45889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.31554,-24.45889,43.71193,-24.41723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.71193,-24.41723,47.31554,-24.45889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.66415,-24.31139,47.3761,-24.27251), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.3761,-24.27251,47.32582,-24.26806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.32582,-24.26806,47.3761,-24.27251), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.57582,-23.83917,43.65415,-23.82917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.65415,-23.82917,47.57582,-23.83917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.63582,-23.66667,47.61694,-23.62195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.61694,-23.62195,43.65221,-23.61945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.65221,-23.61945,47.61694,-23.62195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.57054,-23.59639,47.61777,-23.59028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.61777,-23.59028,47.57054,-23.59639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.74582,-23.58167,47.61777,-23.59028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.59277,-23.56112,43.74582,-23.58167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.75943,-23.46361,47.66221,-23.46223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.66221,-23.46223,43.75943,-23.46361), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.61499,-23.31139,47.66221,-23.46223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.59693,-23.09917,43.61499,-23.31139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.37582,-22.87528,43.59693,-23.09917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.28638,-22.58889,47.91971,-22.43195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.91971,-22.43195,43.28638,-22.58889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.2211,-22.25112,43.29221,-22.23667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.29221,-22.23667,43.2211,-22.25112), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.25027,-22.2164,43.29221,-22.23667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.29694,-22.17028,43.25054,-22.16278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.25054,-22.16278,43.29694,-22.17028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.25943,-21.95417,43.31138,-21.93362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.31138,-21.93362,43.32999,-21.91723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.32999,-21.91723,43.31138,-21.93362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.29971,-21.86501,43.32999,-21.91723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.20527,-21.78945,43.33415,-21.75862), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.33415,-21.75862,48.20527,-21.78945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.47221,-21.6725,43.33415,-21.75862), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.47499,-21.40862,43.50804,-21.31278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.50804,-21.31278,43.7411,-21.28612), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.7411,-21.28612,43.50804,-21.31278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.8086,-21.22528,43.7411,-21.28612), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.42693,-21.08417,48.43332,-20.99695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.43332,-20.99695,48.42693,-21.08417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.94943,-20.78806,44.0361,-20.7239), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.0361,-20.7239,43.94943,-20.78806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.1761,-20.44195,48.61665,-20.38806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.61665,-20.38806,44.25138,-20.38278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.25138,-20.38278,48.61665,-20.38806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.29276,-20.24445,44.25138,-20.38278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.47499,-19.99001,48.79555,-19.98584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.79555,-19.98584,44.47499,-19.99001), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.47332,-19.88195,44.38749,-19.81223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.38749,-19.81223,44.36999,-19.77223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.36999,-19.77223,44.38749,-19.81223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.41249,-19.69195,44.36999,-19.77223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.43832,-19.55723,44.48221,-19.53167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.48221,-19.53167,44.43832,-19.55723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.46915,-19.43834,44.48221,-19.53167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.03555,-19.18417,44.2361,-19.09028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.2361,-19.09028,49.03555,-19.18417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.24304,-18.8839,44.26166,-18.85306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.26166,-18.85306,44.24304,-18.8839), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.1636,-18.59112,44.04054,-18.42306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.04054,-18.42306,49.36499,-18.40362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.36499,-18.40362,44.04054,-18.42306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.03138,-17.75695,49.50971,-17.67834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.50971,-17.67834,43.92888,-17.61195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.92888,-17.61195,49.50971,-17.67834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((43.93138,-17.50056,43.92888,-17.61195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.42055,-17.32612,49.4736,-17.1875), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.4736,-17.1875,44.1186,-17.18528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.1186,-17.18528,49.4736,-17.1875), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.61721,-16.89417,49.81721,-16.84473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.81721,-16.84473,49.7886,-16.83028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.7886,-16.83028,49.81721,-16.84473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.73666,-16.7864,49.7886,-16.83028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.71915,-16.71473,44.43249,-16.70362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.43249,-16.70362,49.71915,-16.71473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.79943,-16.64167,44.43249,-16.70362), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.84943,-16.54251,44.4661,-16.49584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.4661,-16.49584,49.84943,-16.54251), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.3986,-16.36473,49.82499,-16.35667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.82499,-16.35667,44.3986,-16.36473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.85443,-16.23584,44.86166,-16.22639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.86166,-16.22639,49.85443,-16.23584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.44276,-16.2039,49.8361,-16.19723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.8361,-16.19723,44.44276,-16.2039), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((44.48888,-16.17723,49.8361,-16.19723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.70971,-16.12751,45.29166,-16.11056), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.29166,-16.11056,45.32693,-16.11028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.32693,-16.11028,45.29166,-16.11056), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.60693,-16.05528,49.67971,-16.03945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.67971,-16.03945,45.39777,-16.03751), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.39777,-16.03751,49.67971,-16.03945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.16554,-15.98945,45.36582,-15.98778), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.36582,-15.98778,50.16554,-15.98945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.3211,-15.97334,50.24082,-15.96889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.24082,-15.96889,46.47721,-15.96611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.47721,-15.96611,50.24082,-15.96889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.57249,-15.94917,50.14582,-15.93417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.14582,-15.93417,45.27388,-15.93278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.27388,-15.93278,45.25471,-15.93139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.25471,-15.93139,45.27388,-15.93278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.27055,-15.91223,45.64526,-15.90722), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.64526,-15.90722,46.27055,-15.91223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.39526,-15.90084,49.7336,-15.89972), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.7336,-15.89972,46.39526,-15.90084), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.0636,-15.87195,50.03387,-15.86695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.03387,-15.86695,46.42277,-15.86306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.42277,-15.86306,50.03387,-15.86695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.61916,-15.85139,45.95582,-15.845), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.95582,-15.845,45.61916,-15.85139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.82221,-15.82472,46.01138,-15.82389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.01138,-15.82389,45.82221,-15.82472), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.29804,-15.81806,46.01138,-15.82389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.23554,-15.80611,46.29804,-15.81806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.01305,-15.79361,46.07054,-15.78417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.07054,-15.78417,45.95749,-15.78389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.95749,-15.78389,46.07054,-15.78417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.68694,-15.77917,45.95749,-15.78389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((45.91082,-15.77306,45.68694,-15.77917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.36027,-15.73778,46.08999,-15.72556), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.08999,-15.72556,46.30444,-15.71723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.30444,-15.71723,46.23249,-15.71417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.23249,-15.71417,46.30444,-15.71723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.17194,-15.70361,46.23249,-15.71417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.33166,-15.63417,49.92443,-15.58278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.92443,-15.58278,50.43638,-15.56917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.43638,-15.56917,49.92443,-15.58278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.63721,-15.54723,50.43638,-15.56917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.96221,-15.50223,47.09304,-15.47972), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.09304,-15.47972,49.90221,-15.46834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.90221,-15.46834,49.6961,-15.46584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.6961,-15.46584,49.90221,-15.46834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.2236,-15.44861,47.12888,-15.44473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.12888,-15.44473,46.57416,-15.44334), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.57416,-15.44334,47.12888,-15.44473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.75138,-15.43584,46.57416,-15.44334), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.90249,-15.42667,49.75138,-15.43584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.23637,-15.4125,47.1961,-15.40139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.1961,-15.40139,47.23637,-15.4125), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.07638,-15.33445,47.11193,-15.30556), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.11193,-15.30556,47.16415,-15.29639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.16415,-15.29639,47.11193,-15.30556), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.98499,-15.28222,47.16415,-15.29639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.49944,-15.25195,46.97249,-15.23445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.97249,-15.23445,50.49944,-15.25195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((46.94721,-15.19889,47.05804,-15.19806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.05804,-15.19806,46.94721,-15.19889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.44693,-15.185,47.05804,-15.19806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.10555,-15.11917,47.4386,-15.10806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.4386,-15.10806,47.4111,-15.09861), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.4111,-15.09861,47.4386,-15.10806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.2161,-15.03361,47.56721,-14.98139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.56721,-14.98139,47.41249,-14.97139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.41249,-14.97139,47.56721,-14.98139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.31055,-14.9125,47.38165,-14.87695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.38165,-14.87695,47.29999,-14.87528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.29999,-14.87528,47.38165,-14.87695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.28027,-14.86723,47.29999,-14.87528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.49944,-14.84834,47.28553,-14.8469), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.28553,-14.8469,47.35805,-14.84611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.28553,-14.8469,47.35805,-14.84611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.35805,-14.84611,47.28553,-14.8469), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.6436,-14.77806,47.98749,-14.76), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.98749,-14.76,47.6436,-14.77806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.02082,-14.72806,47.50082,-14.71167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.50082,-14.71167,50.23832,-14.71139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.23832,-14.71139,47.50082,-14.71167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.9211,-14.71,50.23832,-14.71139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.45499,-14.66528,47.99554,-14.6625), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.99554,-14.6625,47.9361,-14.66084), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.9361,-14.66084,47.99554,-14.6625), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.01721,-14.63945,47.91888,-14.63528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.91888,-14.63528,48.01721,-14.63945), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.9686,-14.62167,50.21333,-14.61306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.21333,-14.61306,47.9686,-14.62167), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.75555,-14.60361,47.8536,-14.59834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.8536,-14.59834,47.75555,-14.60361), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.79638,-14.57139,47.93193,-14.57084), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.93193,-14.57084,47.79638,-14.57139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.72083,-14.55834,47.80749,-14.54611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.80749,-14.54611,47.72083,-14.55834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.71387,-14.35723,47.99832,-14.29473), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.99832,-14.29473,48.03443,-14.25973), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.03443,-14.25973,47.92776,-14.25389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.92776,-14.25389,48.03443,-14.25973), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.85277,-14.24639,47.92776,-14.25389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.02076,-14.21744,47.80444,-14.20972), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.80444,-14.20972,48.02076,-14.21744), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.95721,-14.185,47.80444,-14.20972), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.0111,-14.13834,48.05249,-14.10611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.05249,-14.10611,47.90443,-14.09278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.90443,-14.09278,48.05249,-14.10611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.94193,-14.00639,48.02304,-13.965), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.02304,-13.965,47.94193,-14.00639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.91055,-13.8975,48.02304,-13.965), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.28721,-13.80806,48.24194,-13.80445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.24194,-13.80445,48.28721,-13.80806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.87249,-13.77667,48.33554,-13.77306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.33554,-13.77306,47.87249,-13.77667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.17999,-13.75417,48.33554,-13.77306), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.89416,-13.71389,48.17999,-13.75417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.10194,-13.6275,50.07665,-13.61084), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.07665,-13.61084,48.09693,-13.60278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.09693,-13.60278,47.90527,-13.59639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.90527,-13.59639,48.1436,-13.59611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.1436,-13.59611,47.90527,-13.59639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.39499,-13.58667,47.96193,-13.585), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.96193,-13.585,48.39499,-13.58667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.00721,-13.58139,47.96193,-13.585), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.33415,-13.55417,48.04777,-13.55), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.04777,-13.55,48.3686,-13.54917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.3686,-13.54917,48.04777,-13.55), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.08332,-13.53945,48.3686,-13.54917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((47.96443,-13.52806,48.52693,-13.52611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.52693,-13.52611,47.96443,-13.52806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.45582,-13.51695,48.03082,-13.51472), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.03082,-13.51472,48.45582,-13.51695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.59776,-13.44889,48.73971,-13.42723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.73971,-13.42723,48.59776,-13.44889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.51804,-13.40389,48.73971,-13.42723), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((50.02666,-13.34889,49.97943,-13.34667), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.97943,-13.34667,50.02666,-13.34889), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.84165,-13.26611,48.81248,-13.19806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.81248,-13.19806,48.84165,-13.26611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.9386,-13.03056,48.89721,-12.97695), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.89721,-12.97695,49.9386,-13.03056), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.8111,-12.86528,48.95943,-12.82223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.95943,-12.82223,49.65332,-12.80611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.65332,-12.80611,49.80166,-12.80028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.80166,-12.80028,49.65332,-12.80611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.71054,-12.77973,49.80166,-12.80028), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.71721,-12.74084,49.71054,-12.77973), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.6486,-12.69306,49.71721,-12.74084), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.56138,-12.63223,48.87027,-12.605), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.87027,-12.605,49.56138,-12.63223), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.83693,-12.56973,48.88693,-12.55528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.88693,-12.55528,49.57555,-12.54195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.57555,-12.54195,49.59666,-12.5325), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.59666,-12.5325,49.57555,-12.54195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.90721,-12.49584,48.94804,-12.48278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.94804,-12.48278,49.58804,-12.47917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.58804,-12.47917,48.94804,-12.48278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.88805,-12.46028,49.58804,-12.47917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.7811,-12.43834,48.73082,-12.43417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.73082,-12.43417,48.7811,-12.43834), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.48749,-12.41139,48.76638,-12.39917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.76638,-12.39917,49.48749,-12.41139), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.54388,-12.38445,49.4586,-12.37611), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.4586,-12.37611,49.54388,-12.38445), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((48.96915,-12.35778,49.4986,-12.34639), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.4986,-12.34639,48.96915,-12.35778), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.35638,-12.29945,49.08665,-12.29806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.08665,-12.29806,49.31638,-12.2975), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.31638,-12.2975,49.08665,-12.29806), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.06388,-12.27667,49.31638,-12.2975), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.35971,-12.23584,49.39526,-12.23278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.28749,-12.23584,49.39526,-12.23278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.39526,-12.23278,49.35971,-12.23584), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.23249,-12.225,49.39526,-12.23278), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.37193,-12.20723,49.23249,-12.225), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.19888,-12.15417,49.26193,-12.14389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.26193,-12.14389,49.19888,-12.15417), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.22527,-12.10528,49.1511,-12.09917), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.1511,-12.09917,49.22527,-12.10528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.09527,-12.09917,49.22527,-12.10528), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.34721,-12.06195,49.15721,-12.05389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.15721,-12.05389,49.34721,-12.06195), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.20749,-12.00334,49.15721,-12.05389), mapfile, tile_dir, 0, 11, "mg-madagascar")
	render_tiles((49.27443,-11.94778,49.20749,-12.00334), mapfile, tile_dir, 0, 11, "mg-madagascar")