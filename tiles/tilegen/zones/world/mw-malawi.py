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
    # Region: MW
    # Region Name: Malawi

	render_tiles((35.2906,-17.13581,35.09165,-17.12917), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.09165,-17.12917,35.2906,-17.13581), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.30804,-17.06139,35.05248,-17.02723), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.05248,-17.02723,35.30804,-17.06139), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.13915,-16.95195,35.2711,-16.95028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.2711,-16.95028,35.13915,-16.95195), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.30415,-16.84612,35.14638,-16.84111), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.14638,-16.84111,35.30415,-16.84612), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.04943,-16.82389,35.12832,-16.81695), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.12832,-16.81695,35.04943,-16.82389), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.27137,-16.69722,34.85971,-16.6775), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.85971,-16.6775,35.27137,-16.69722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.16859,-16.62112,34.85971,-16.6775), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.13721,-16.55056,34.76193,-16.53723), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.76193,-16.53723,35.13721,-16.55056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.25166,-16.45861,34.59443,-16.40472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.59443,-16.40472,35.25166,-16.45861), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.57555,-16.32389,34.45415,-16.28417), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.45415,-16.28417,34.57555,-16.32389), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.29332,-16.22472,34.41054,-16.205), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.41054,-16.205,35.29332,-16.22472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.60609,-16.13723,35.41165,-16.12445), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.41165,-16.12445,35.60609,-16.13723), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.42776,-16.06362,35.78693,-16.0625), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.78693,-16.0625,34.42776,-16.06362), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.2636,-15.91639,34.26027,-15.80778), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.26027,-15.80778,34.32416,-15.74361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.32416,-15.74361,34.26027,-15.80778), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.40998,-15.67583,34.32416,-15.74361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.44331,-15.5475,34.42526,-15.49667), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.42526,-15.49667,34.44331,-15.5475), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.8672,-15.41945,34.42526,-15.49667), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.84358,-15.33422,35.84182,-15.32784), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.84182,-15.32784,35.84358,-15.33422), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.58971,-15.28278,35.84182,-15.32784), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.80026,-15.17778,34.58971,-15.28278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.92416,-14.88556,35.8811,-14.885), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.8811,-14.885,35.92416,-14.88556), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.56693,-14.78722,35.8811,-14.885), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.52248,-14.68861,35.8772,-14.65611), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.8772,-14.65611,34.52248,-14.68861), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.5411,-14.61556,33.69276,-14.59861), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.69276,-14.59861,33.65054,-14.58945), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.65054,-14.58945,33.69276,-14.59861), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.82555,-14.53389,33.70776,-14.50056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.70776,-14.50056,34.07388,-14.49389), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.07388,-14.49389,33.73637,-14.4875), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.73637,-14.4875,34.07388,-14.49389), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.93221,-14.47528,33.73637,-14.4875), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.54943,-14.44223,33.47887,-14.41056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.47887,-14.41056,34.30109,-14.40361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.30109,-14.40361,34.38888,-14.39722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.38888,-14.39722,34.30109,-14.40361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.03999,-14.05278,33.00443,-14.03278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.00443,-14.03278,33.03999,-14.05278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.2218,-14.0111,33.00443,-14.03278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.16165,-13.92222,33.2218,-14.0111), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.89915,-13.82,32.78249,-13.77556), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.78249,-13.77556,32.77832,-13.74333), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.77832,-13.74333,32.78249,-13.77556), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.83276,-13.70972,35.09526,-13.68611), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((35.09526,-13.68611,32.83276,-13.70972), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.78582,-13.64056,32.6897,-13.6225), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.6897,-13.6225,32.78582,-13.64056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.69054,-13.565,32.82221,-13.53806), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.82221,-13.53806,32.69054,-13.565), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.86579,-13.50039,34.86212,-13.49742), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.86212,-13.49742,34.65804,-13.49639), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.65804,-13.49639,34.86212,-13.49742), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.56915,-13.34695,33.01221,-13.215), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.01221,-13.215,34.56915,-13.34695), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.99276,-13.03695,33.03526,-12.91028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.03526,-12.91028,32.96776,-12.85056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.96776,-12.85056,33.03526,-12.91028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.96027,-12.76167,34.52998,-12.75889), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.52998,-12.75889,32.96027,-12.76167), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.04638,-12.60389,33.22693,-12.58834), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.22693,-12.58834,33.13776,-12.58084), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.13776,-12.58084,33.22693,-12.58834), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.35721,-12.54333,33.28027,-12.52722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.28027,-12.52722,33.35721,-12.54333), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.44443,-12.50334,33.28027,-12.52722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.53777,-12.36889,33.39443,-12.34167), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.39443,-12.34167,33.54443,-12.32778), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.54443,-12.32778,33.39443,-12.34167), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.33526,-12.25778,33.54443,-12.32778), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.37387,-12.16722,33.2711,-12.13139), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.2711,-12.13139,34.37387,-12.16722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.32999,-11.80722,34.61443,-11.76389), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.61443,-11.76389,33.32999,-11.80722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.3236,-11.60778,34.62609,-11.57583), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.62609,-11.57583,33.23054,-11.57472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.23054,-11.57472,34.62609,-11.57583), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.95806,-11.57291,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.96201,-11.5729,34.95806,-11.57291), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.28443,-11.57056,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.96325,-11.56566,33.28443,-11.57056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.91859,-11.42806,34.7636,-11.345), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.7636,-11.345,34.82332,-11.34028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.82332,-11.34028,34.7636,-11.345), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.35665,-11.25611,34.82332,-11.34028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.40915,-11.17,34.66859,-11.16334), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.66859,-11.16334,33.40915,-11.17), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.59387,-11.02833,33.25027,-10.8975), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.25027,-10.8975,33.28693,-10.86555), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.28693,-10.86555,33.25027,-10.8975), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.39693,-10.79861,33.50166,-10.77972), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.50166,-10.77972,33.39693,-10.79861), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.67609,-10.7475,33.50166,-10.77972), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.69304,-10.58111,33.68498,-10.51334), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.68498,-10.51334,33.69304,-10.58111), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.57054,-10.44445,33.57276,-10.40028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.57276,-10.40028,34.57054,-10.44445), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.55943,-10.22722,34.55804,-10.18028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.55804,-10.18028,33.55943,-10.22722), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.32416,-10.06778,34.55804,-10.18028), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.35943,-9.93306,33.39027,-9.90361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.39027,-9.90361,34.44776,-9.89944), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.44776,-9.89944,33.39027,-9.90361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.36137,-9.82445,34.44776,-9.89944), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.23859,-9.73139,33.92814,-9.69981), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.92814,-9.69981,33.23859,-9.73139), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.92814,-9.69981,33.23859,-9.73139), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.10471,-9.66278,33.22915,-9.63417), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.22915,-9.63417,33.50888,-9.62195), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.50888,-9.62195,32.99332,-9.62167), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.99332,-9.62167,33.50888,-9.62195), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.42887,-9.61195,33.67693,-9.61056), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.67693,-9.61056,33.42887,-9.61195), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.11776,-9.58667,33.57887,-9.58472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.57887,-9.58472,33.75582,-9.58278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.75582,-9.58278,33.57887,-9.58472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.13387,-9.56723,33.75582,-9.58278), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.95026,-9.54639,33.39388,-9.53806), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.39388,-9.53806,33.95026,-9.54639), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.19248,-9.50917,33.98859,-9.49778), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.98859,-9.49778,32.95387,-9.48944), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.95387,-9.48944,34.0436,-9.48472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((34.0436,-9.48472,33.30193,-9.48417), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.30193,-9.48417,34.0436,-9.48472), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.98502,-9.48103,33.30193,-9.48417), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((33.02859,-9.41167,32.94068,-9.40627), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.94068,-9.40627,32.94026,-9.40361), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.94026,-9.40361,32.94068,-9.40627), mapfile, tile_dir, 0, 11, "mw-malawi")
	render_tiles((32.99776,-9.37333,32.94026,-9.40361), mapfile, tile_dir, 0, 11, "mw-malawi")