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
    # Region: ZM
    # Region Name: Zambia

	render_tiles((26.68916,-18.07528,25.97166,-18.00639), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.97166,-18.00639,25.85971,-17.97334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.85971,-17.97334,26.46277,-17.96861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.46277,-17.96861,27.02077,-17.96418), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.02077,-17.96418,26.46277,-17.96861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.03141,-17.95545,27.02077,-17.96418), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.31472,-17.93584,27.03141,-17.95545), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.85416,-17.90862,26.21499,-17.88417), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.21499,-17.88417,27.12243,-17.88076), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.12243,-17.88076,26.21499,-17.88417), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.39194,-17.85084,27.14622,-17.84528), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.14622,-17.84528,25.59972,-17.84139), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.59972,-17.84139,27.14622,-17.84528), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.71999,-17.83639,25.59972,-17.84139), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.14582,-17.80445,25.26575,-17.79766), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.26575,-17.79766,27.14582,-17.80445), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.11832,-17.69473,23.47486,-17.62453), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.47486,-17.62453,25.03277,-17.5825), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.03277,-17.5825,23.47486,-17.62453), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.76388,-17.50973,24.23567,-17.48188), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.23567,-17.48188,23.20166,-17.47972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.20166,-17.47972,24.23567,-17.48188), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.61916,-17.33723,27.63888,-17.22472), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.63888,-17.22472,27.61916,-17.33723), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.82527,-16.95917,28.1386,-16.82362), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.1386,-16.82362,28.25999,-16.72417), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.25999,-16.72417,28.1386,-16.82362), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((22.24888,-16.57,28.75976,-16.53767), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.75976,-16.53767,28.76191,-16.5344), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.76191,-16.5344,28.75976,-16.53767), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.76191,-16.5344,28.75976,-16.53767), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((22.13888,-16.4925,28.76191,-16.5344), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.85055,-16.39973,22.13888,-16.4925), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((22.07083,-16.23917,22.00079,-16.17085), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((22.00079,-16.17085,22.07083,-16.23917), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.85833,-16.0625,28.92722,-15.97222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.92722,-15.97222,28.85833,-16.0625), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.13805,-15.86,29.31916,-15.74861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.31916,-15.74861,30.37805,-15.65056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.37805,-15.65056,30.41388,-15.63361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.41388,-15.63361,30.37805,-15.65056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.83138,-15.61611,30.41388,-15.63361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.35777,-15.55472,29.83138,-15.61611), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.38805,-15.47889,30.35777,-15.55472), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.35805,-15.33028,30.26833,-15.25084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.26833,-15.25084,30.35805,-15.33028), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.21277,-14.98111,30.84555,-14.76389), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.84555,-14.76389,31.38221,-14.645), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.38221,-14.645,31.58527,-14.56167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.58527,-14.56167,21.99944,-14.52111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((21.99944,-14.52111,31.58527,-14.56167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.07777,-14.38806,21.99944,-14.52111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.03999,-14.05278,33.00443,-14.03278), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.00443,-14.03278,33.03999,-14.05278), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.2218,-14.0111,33.00443,-14.03278), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.16165,-13.92222,33.2218,-14.0111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.89915,-13.82,32.78249,-13.77556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.78249,-13.77556,32.77832,-13.74333), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.77832,-13.74333,32.78249,-13.77556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.83276,-13.70972,32.77832,-13.74333), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.78582,-13.64056,32.6897,-13.6225), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.6897,-13.6225,32.78582,-13.64056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.69054,-13.565,32.82221,-13.53806), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.82221,-13.53806,32.69054,-13.565), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.80194,-13.45195,29.66388,-13.43778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.66388,-13.43778,29.80194,-13.45195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.62971,-13.40945,29.17083,-13.39972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.17083,-13.39972,29.01583,-13.39778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.01583,-13.39778,29.17083,-13.39972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.06583,-13.38695,29.01583,-13.39778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.62138,-13.37195,29.06583,-13.38695), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.96166,-13.34778,29.62138,-13.37195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.68277,-13.30611,29.6836,-13.265), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.6836,-13.265,21.99853,-13.2535), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((21.99853,-13.2535,29.6836,-13.265), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.58944,-13.22194,33.01221,-13.215), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.01221,-13.215,29.58944,-13.22194), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.92471,-13.16028,33.01221,-13.215), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.84499,-13.09139,32.99276,-13.03695), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.99276,-13.03695,24.02055,-13.00639), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.02055,-13.00639,23.33166,-13.00584), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.33166,-13.00584,24.02055,-13.00639), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((22.64416,-13.00528,23.33166,-13.00584), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((21.99833,-13.00417,22.64416,-13.00528), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.82222,-12.98917,24.01694,-12.98722), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.01694,-12.98722,28.82222,-12.98917), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.03526,-12.91028,28.59666,-12.89473), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.59666,-12.89473,28.56999,-12.89084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.56999,-12.89084,28.59666,-12.89473), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.96776,-12.85056,28.67888,-12.84222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.67888,-12.84222,28.63249,-12.84084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.63249,-12.84084,28.67888,-12.84222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.80388,-12.82889,28.63249,-12.84084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.88694,-12.80472,29.80388,-12.82889), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.96027,-12.76167,28.4936,-12.74195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.4936,-12.74195,32.96027,-12.76167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.53333,-12.67139,23.91499,-12.66972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.91499,-12.66972,28.53333,-12.67139), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.04638,-12.60389,33.22693,-12.58834), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.22693,-12.58834,33.13776,-12.58084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.13776,-12.58084,33.22693,-12.58834), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.35721,-12.54333,33.28027,-12.52722), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.28027,-12.52722,33.35721,-12.54333), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.48193,-12.46028,28.3661,-12.45555), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.3661,-12.45555,29.48193,-12.46028), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.52222,-12.43695,28.15166,-12.43167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.15166,-12.43167,29.52222,-12.43695), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.21082,-12.40139,29.52583,-12.39167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.52583,-12.39167,29.48055,-12.38556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.48055,-12.38556,24.05222,-12.38528), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.05222,-12.38528,29.48055,-12.38556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.03694,-12.38445,24.05222,-12.38528), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.21082,-12.37222,27.9811,-12.37056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.9811,-12.37056,29.21082,-12.37222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.53777,-12.36889,27.9811,-12.37056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.04472,-12.365,33.53777,-12.36889), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.39443,-12.34167,33.54443,-12.32778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.54443,-12.32778,33.39443,-12.34167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.67472,-12.30278,27.95555,-12.30167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.95555,-12.30167,27.67472,-12.30278), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.79694,-12.29861,27.95555,-12.30167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.45943,-12.28639,27.79694,-12.29861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.03499,-12.26417,33.33526,-12.25778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.33526,-12.25778,27.85471,-12.25334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.85471,-12.25334,33.33526,-12.25778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.49916,-12.22945,27.85471,-12.25334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.97499,-12.2,27.54721,-12.18972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.54721,-12.18972,23.97499,-12.2), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.80563,-12.15853,33.2711,-12.13139), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.2711,-12.13139,29.80563,-12.15853), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.81221,-12.07195,26.72305,-12.01889), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.72305,-12.01889,27.4786,-11.96667), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.4786,-11.96667,26.90221,-11.96111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.90221,-11.96111,27.4786,-11.96667), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.4611,-11.91722,26.01722,-11.90361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.01722,-11.90361,26.4611,-11.91722), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((26.9861,-11.87167,26.01722,-11.90361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.32999,-11.80722,25.78222,-11.8025), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.78222,-11.8025,33.32999,-11.80722), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.46333,-11.79667,27.2411,-11.79361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.2411,-11.79361,28.46333,-11.79667), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.87471,-11.78945,27.2411,-11.79361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.51833,-11.77834,23.99554,-11.77167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.99554,-11.77167,25.51833,-11.77834), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.49305,-11.76222,25.56027,-11.75334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.56027,-11.75334,25.49305,-11.76222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.50249,-11.71945,25.56027,-11.75334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.35971,-11.64167,33.3236,-11.60778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.3236,-11.60778,27.03222,-11.59611), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.03222,-11.59611,33.3236,-11.60778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.2136,-11.5825,27.1086,-11.58222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((27.1086,-11.58222,27.2136,-11.5825), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.23054,-11.57472,33.28443,-11.57056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.28443,-11.57056,33.23054,-11.57472), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.35805,-11.53417,33.28443,-11.57056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.44833,-11.46361,24.53166,-11.45972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.53166,-11.45972,24.02861,-11.45778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.02861,-11.45778,24.53166,-11.45972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.06221,-11.42195,24.39138,-11.40945), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.39138,-11.40945,24.31277,-11.3975), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.31277,-11.3975,28.39555,-11.39584), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.39555,-11.39584,24.31277,-11.3975), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.2886,-11.37639,24.30444,-11.37556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.30444,-11.37556,25.2886,-11.37639), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.3611,-11.35528,24.6986,-11.33861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.6986,-11.33861,24.8111,-11.32361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.8111,-11.32361,24.6986,-11.33861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.41027,-11.28028,33.35665,-11.25611), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.35665,-11.25611,24.41027,-11.28028), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.34638,-11.21389,25.33221,-11.19333), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((25.33221,-11.19333,25.34638,-11.21389), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.40915,-11.17,28.48416,-11.16806), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.48416,-11.16806,33.40915,-11.17), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.39972,-11.11417,28.48416,-11.16806), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.32305,-11.05195,24.14305,-11.03778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.14305,-11.03778,24.32305,-11.05195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.50722,-10.99445,24.14305,-11.03778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((24.13194,-10.91472,33.25027,-10.8975), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.25027,-10.8975,24.13194,-10.91472), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((23.98272,-10.86963,33.28693,-10.86555), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.28693,-10.86555,23.98272,-10.86963), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.39693,-10.79861,33.50166,-10.77972), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.50166,-10.77972,33.39693,-10.79861), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.61832,-10.72167,28.69305,-10.66695), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.69305,-10.66695,28.61832,-10.72167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.69304,-10.58111,33.68498,-10.51334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.68498,-10.51334,33.69304,-10.58111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.57276,-10.40028,33.68498,-10.51334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.55943,-10.22722,28.57027,-10.2225), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.57027,-10.2225,33.55943,-10.22722), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.62499,-10.1425,33.32416,-10.06778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.32416,-10.06778,28.62499,-10.1425), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.35943,-9.93306,33.39027,-9.90361), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.39027,-9.90361,33.35943,-9.93306), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.36137,-9.82445,28.6686,-9.81945), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.6686,-9.81945,33.36137,-9.82445), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.69638,-9.79834,28.6686,-9.81945), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.23859,-9.73139,28.69638,-9.79834), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.10471,-9.66278,33.22915,-9.63417), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.22915,-9.63417,32.99332,-9.62167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.99332,-9.62167,33.22915,-9.63417), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((33.11776,-9.58667,28.58722,-9.57), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.58722,-9.57,33.11776,-9.58667), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.95387,-9.48944,32.98502,-9.48103), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.98502,-9.48103,32.95387,-9.48944), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.53881,-9.42858,32.94068,-9.40627), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.94068,-9.40627,28.53881,-9.42858), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.52155,-9.37815,32.83082,-9.36639), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.83082,-9.36639,28.52155,-9.37815), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.40221,-9.31167,32.74193,-9.28167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.74193,-9.28167,28.37111,-9.27334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.37111,-9.27334,32.74193,-9.28167), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.53693,-9.26111,28.37111,-9.27334), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.38805,-9.23528,32.53693,-9.26111), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.52527,-9.1625,32.47082,-9.16056), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.47082,-9.16056,28.52527,-9.1625), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.98444,-9.07306,32.04221,-9.04084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((32.04221,-9.04084,31.93749,-9.02917), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.93749,-9.02917,32.04221,-9.04084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.7286,-8.99195,31.93138,-8.98), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.93138,-8.98,28.7286,-8.99195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.9561,-8.93084,31.6836,-8.90889), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.6836,-8.90889,31.9561,-8.93084), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.78555,-8.885,31.6836,-8.90889), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.57305,-8.81861,31.78555,-8.885), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.55166,-8.68917,28.96166,-8.66778), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.96166,-8.66778,31.55166,-8.68917), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.39805,-8.62944,31.27305,-8.62195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.27305,-8.62195,31.12582,-8.61556), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.12582,-8.61556,31.27305,-8.62195), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.95583,-8.60528,31.16835,-8.59614), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.16835,-8.59614,31.16999,-8.59538), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.16999,-8.59538,31.16835,-8.59614), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.0411,-8.59028,31.16999,-8.59538), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.37555,-8.58222,31.22055,-8.57667), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((31.22055,-8.57667,31.37555,-8.58222), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.90082,-8.48772,28.89763,-8.4809), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((28.89763,-8.4809,28.90082,-8.48772), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.8986,-8.45583,28.89763,-8.4809), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((29.40888,-8.40111,30.8986,-8.45583), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.08944,-8.29611,30.57626,-8.22081), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.57626,-8.22081,30.76999,-8.19083), mapfile, tile_dir, 0, 11, "zm-zambia")
	render_tiles((30.76999,-8.19083,30.57626,-8.22081), mapfile, tile_dir, 0, 11, "zm-zambia")