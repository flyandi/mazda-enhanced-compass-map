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
    # Region: CD
    # Region Name: Zaire

	render_tiles((29.80194,-13.45195,29.66388,-13.43778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.66388,-13.43778,29.80194,-13.45195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.62971,-13.40945,29.17083,-13.39972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.17083,-13.39972,29.01583,-13.39778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.01583,-13.39778,29.17083,-13.39972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.06583,-13.38695,29.01583,-13.39778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.62138,-13.37195,29.06583,-13.38695), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.96166,-13.34778,29.62138,-13.37195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.68277,-13.30611,29.6836,-13.265), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.6836,-13.265,29.68277,-13.30611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.58944,-13.22194,29.6836,-13.265), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.92471,-13.16028,29.58944,-13.22194), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.84499,-13.09139,28.92471,-13.16028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.82222,-12.98917,28.59666,-12.89473), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.59666,-12.89473,28.56999,-12.89084), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.56999,-12.89084,28.59666,-12.89473), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.67888,-12.84222,28.63249,-12.84084), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.63249,-12.84084,28.67888,-12.84222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.80388,-12.82889,28.63249,-12.84084), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.4936,-12.74195,28.53333,-12.67139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.53333,-12.67139,28.4936,-12.74195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.48193,-12.46028,28.3661,-12.45555), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.3661,-12.45555,29.48193,-12.46028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.52222,-12.43695,28.15166,-12.43167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.15166,-12.43167,29.52222,-12.43695), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.21082,-12.40139,29.52583,-12.39167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.52583,-12.39167,29.48055,-12.38556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.48055,-12.38556,29.03694,-12.38445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.03694,-12.38445,29.48055,-12.38556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.21082,-12.37222,27.9811,-12.37056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.9811,-12.37056,29.21082,-12.37222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.04472,-12.365,27.9811,-12.37056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.67472,-12.30278,27.95555,-12.30167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.95555,-12.30167,27.67472,-12.30278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.79694,-12.29861,27.95555,-12.30167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.45943,-12.28639,27.79694,-12.29861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.85471,-12.25334,29.49916,-12.22945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.49916,-12.22945,27.85471,-12.25334), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.54721,-12.18972,29.80563,-12.15853), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.80563,-12.15853,27.54721,-12.18972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.81221,-12.07195,26.72305,-12.01889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.72305,-12.01889,27.4786,-11.96667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.4786,-11.96667,26.90221,-11.96111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.90221,-11.96111,27.4786,-11.96667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.4611,-11.91722,26.01722,-11.90361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.01722,-11.90361,26.4611,-11.91722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.9861,-11.87167,26.01722,-11.90361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.78222,-11.8025,28.46333,-11.79667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.46333,-11.79667,27.2411,-11.79361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.2411,-11.79361,28.46333,-11.79667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.87471,-11.78945,27.2411,-11.79361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.51833,-11.77834,25.87471,-11.78945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.49305,-11.76222,25.56027,-11.75334), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.56027,-11.75334,25.49305,-11.76222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.50249,-11.71945,25.56027,-11.75334), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.35971,-11.64167,27.03222,-11.59611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.03222,-11.59611,27.2136,-11.5825), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.2136,-11.5825,27.1086,-11.58222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.1086,-11.58222,27.2136,-11.5825), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.35805,-11.53417,27.1086,-11.58222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.44833,-11.46361,24.53166,-11.45972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.53166,-11.45972,24.44833,-11.46361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.39138,-11.40945,24.31277,-11.3975), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.31277,-11.3975,28.39555,-11.39584), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.39555,-11.39584,24.31277,-11.3975), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.2886,-11.37639,24.30444,-11.37556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.30444,-11.37556,25.2886,-11.37639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.3611,-11.35528,24.6986,-11.33861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.6986,-11.33861,24.8111,-11.32361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.8111,-11.32361,24.6986,-11.33861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.41027,-11.28028,22.24446,-11.25064), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.24446,-11.25064,24.41027,-11.28028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.34638,-11.21389,25.33221,-11.19333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.33221,-11.19333,25.34638,-11.21389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.48416,-11.16806,25.33221,-11.19333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.4836,-11.13,24.39972,-11.11417), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.39972,-11.11417,22.4836,-11.13), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.94277,-11.095,22.7236,-11.09333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.7236,-11.09333,23.10805,-11.09195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.10805,-11.09195,22.7236,-11.09333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.2461,-11.07306,23.10805,-11.09195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.32305,-11.05195,22.86916,-11.05056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.86916,-11.05056,24.32305,-11.05195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.51971,-11.04195,24.14305,-11.03778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.14305,-11.03778,22.58083,-11.03445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.58083,-11.03445,24.14305,-11.03778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.85055,-11.02778,22.58083,-11.03445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.8861,-11.01472,23.85055,-11.02778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.50722,-10.99445,23.8861,-11.01472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.39749,-10.97028,23.50305,-10.95972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.50305,-10.95972,23.39749,-10.97028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.13194,-10.91472,22.16555,-10.87333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.16555,-10.87333,23.98272,-10.86963), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.98272,-10.86963,22.16555,-10.87333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.18666,-10.82917,23.98272,-10.86963), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.31305,-10.76722,28.61832,-10.72167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.61832,-10.72167,22.31305,-10.76722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.69305,-10.66695,28.61832,-10.72167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.30333,-10.53778,22.26416,-10.50056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.26416,-10.50056,22.30333,-10.53778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.31444,-10.38584,22.26416,-10.50056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.57027,-10.2225,28.62499,-10.1425), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.62499,-10.1425,28.57027,-10.2225), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.14721,-9.91139,22.02499,-9.85139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.02499,-9.85139,28.6686,-9.81945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.6686,-9.81945,28.69638,-9.79834), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.69638,-9.79834,28.6686,-9.81945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.58722,-9.57,21.83499,-9.53361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.83499,-9.53361,28.58722,-9.57), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.53881,-9.42858,21.79055,-9.40556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.79055,-9.40556,28.53881,-9.42858), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.52155,-9.37815,21.79055,-9.40556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.40221,-9.31167,28.37111,-9.27334), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.37111,-9.27334,28.38805,-9.23528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.38805,-9.23528,21.85471,-9.22945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.85471,-9.22945,28.38805,-9.23528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.52527,-9.1625,21.85471,-9.22945), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.7286,-8.99195,21.86027,-8.85472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.86027,-8.85472,28.7286,-8.99195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.96166,-8.66778,28.95583,-8.60528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.95583,-8.60528,28.96166,-8.66778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.90082,-8.48772,28.89763,-8.4809), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.89763,-8.4809,28.90082,-8.48772), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.93721,-8.44306,28.89763,-8.4809), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.40888,-8.40111,21.93721,-8.44306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.08944,-8.29611,30.57626,-8.22081), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.57626,-8.22081,30.76999,-8.19083), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.76999,-8.19083,30.57626,-8.22081), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.00722,-8.10806,18.11805,-8.10694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.11805,-8.10694,18.00722,-8.10806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.63805,-8.09805,18.11805,-8.10694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.80499,-8.08639,18.11194,-8.07806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.11194,-8.07806,17.53722,-8.07722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.53722,-8.07722,18.11194,-8.07806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.80916,-8.06222,17.74055,-8.06195), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.74055,-8.06195,21.80916,-8.06222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.92221,-8.04667,17.8686,-8.04583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.8686,-8.04583,17.92221,-8.04667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.54333,-8.02139,18.13527,-8.02083), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.13527,-8.02083,17.54333,-8.02139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.7086,-8.00028,18.79499,-7.99889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.79499,-7.99889,30.7086,-8.00028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.37305,-7.99611,18.52583,-7.99556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.52583,-7.99556,19.37305,-7.99611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.74944,-7.945,18.76777,-7.93055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.76777,-7.93055,18.53111,-7.93028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.53111,-7.93028,18.76777,-7.93055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.43721,-7.92472,18.53111,-7.93028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.35916,-7.85806,17.41972,-7.84806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.41972,-7.84806,19.35916,-7.85806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.28416,-7.69944,17.28777,-7.62639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.28777,-7.62639,17.21444,-7.58861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.21444,-7.58861,30.45583,-7.58028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.45583,-7.58028,17.21444,-7.58861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.37749,-7.57167,19.47138,-7.56861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.47138,-7.56861,19.37749,-7.57167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.14666,-7.46945,19.53583,-7.46), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.53583,-7.46,21.8461,-7.4525), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.8461,-7.4525,19.53583,-7.46), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.1811,-7.42778,17.10555,-7.42222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.10555,-7.42222,17.1811,-7.42778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.49944,-7.35167,21.81971,-7.32556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.81971,-7.32556,17.02527,-7.30833), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.02527,-7.30833,21.81971,-7.32556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.54193,-7.28468,30.38194,-7.28389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.38194,-7.28389,20.54193,-7.28468), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.10305,-7.28278,21.7836,-7.28167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.7836,-7.28167,21.10305,-7.28278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.94582,-7.20833,19.50471,-7.14389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.50471,-7.14389,20.53999,-7.14222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.53999,-7.14222,19.50471,-7.14389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.56638,-7.05306,16.96082,-7.03889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.96082,-7.03889,19.56638,-7.05306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.54222,-6.99722,19.63111,-6.99694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.63111,-6.99694,19.54222,-6.99722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.31138,-6.99472,19.63111,-6.99694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.20749,-6.9925,20.31138,-6.99472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.96,-6.98528,30.20749,-6.9925), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.92722,-6.9175,20.6311,-6.91444), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.6311,-6.91444,16.92722,-6.9175), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.33249,-6.91444,16.92722,-6.9175), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.91194,-6.86556,20.6311,-6.91444), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.84111,-6.79944,16.91194,-6.86556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.82305,-6.69833,16.84111,-6.79944), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.70388,-6.46445,29.55027,-6.29528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.55027,-6.29528,16.71749,-6.17722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.71749,-6.17722,29.55027,-6.29528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.49666,-6.04389,12.43583,-6.01667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.43583,-6.01667,16.6,-6.0125), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.6,-6.0125,12.43583,-6.01667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.45805,-5.97167,12.39416,-5.95806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.39416,-5.95806,12.7125,-5.95611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.7125,-5.95611,12.39416,-5.95806), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.50471,-5.94583,12.7125,-5.95611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.57972,-5.90083,14.33555,-5.8925), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.33555,-5.8925,13.34139,-5.89056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.34139,-5.89056,14.33555,-5.8925), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.265,-5.86472,15.02806,-5.86278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.02806,-5.86278,12.265,-5.86472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.17802,-5.85961,15.70527,-5.85861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.70527,-5.85861,13.17802,-5.85961), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.50083,-5.85528,13.40805,-5.85389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.40805,-5.85389,16.36832,-5.85306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.76805,-5.85389,16.36832,-5.85306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.36832,-5.85306,13.40805,-5.85389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.98028,-5.83583,16.36832,-5.85306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.92555,-5.81778,13.98028,-5.83583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.20966,-5.77091,29.63194,-5.74833), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.63194,-5.74833,12.28389,-5.73445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.28389,-5.73445,12.52666,-5.72417), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.52666,-5.72417,12.28389,-5.73445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.62166,-5.66917,12.52666,-5.72417), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.53444,-5.44806,29.62166,-5.66917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.53889,-5.12083,12.46205,-5.09236), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.46205,-5.09236,12.53889,-5.12083), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.35138,-4.95139,12.70805,-4.91861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.70805,-4.91861,14.655,-4.91), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.655,-4.91,12.70805,-4.91861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.41904,-4.88765,14.71889,-4.885), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.71889,-4.885,13.41028,-4.88306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.41028,-4.88306,14.71889,-4.885), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.49583,-4.84083,13.49416,-4.80389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.49416,-4.80389,29.34138,-4.79778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.34138,-4.79778,13.49416,-4.80389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.84611,-4.79167,13.37555,-4.78667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.37555,-4.78667,14.84611,-4.79167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((12.82555,-4.73556,13.70055,-4.72333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.70055,-4.72333,12.82555,-4.73556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.19472,-4.68361,13.0825,-4.67), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.0825,-4.67,13.19472,-4.68361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.09105,-4.63307,14.38333,-4.59944), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.38333,-4.59944,13.14528,-4.58917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.14528,-4.58917,14.38333,-4.59944), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.10083,-4.5725,14.36472,-4.55778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.36472,-4.55778,13.10083,-4.5725), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.43082,-4.54111,14.36472,-4.55778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.965,-4.495,13.88027,-4.48472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.88027,-4.48472,13.965,-4.495), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.99111,-4.46528,14.47666,-4.45639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.47666,-4.45639,29.42359,-4.44947), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.42359,-4.44947,14.47666,-4.45639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.73916,-4.44167,29.42359,-4.44947), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.4875,-4.42695,13.82861,-4.42611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((13.82861,-4.42611,14.4875,-4.42695), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.15194,-4.42,13.82861,-4.42611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.39916,-4.34139,15.27287,-4.30676), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.27287,-4.30676,15.42861,-4.29361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.42861,-4.29361,15.27287,-4.30676), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((14.41528,-4.27417,15.42861,-4.29361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.47333,-4.24889,15.48,-4.22564), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.48,-4.22564,15.47333,-4.24889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.48707,-4.20097,15.48,-4.22564), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.38555,-4.15417,15.48707,-4.20097), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.51695,-4.0968,29.38555,-4.15417), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.56944,-4.03722,15.51695,-4.0968), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.84333,-3.96917,15.91111,-3.91972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.91111,-3.91972,29.23249,-3.885), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.23249,-3.885,15.91111,-3.91972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((15.98333,-3.75694,29.23249,-3.885), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.24694,-3.59444,15.98333,-3.75694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.21551,-3.33816,29.21532,-3.33662), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.21532,-3.33662,29.21551,-3.33816), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.22777,-3.32222,29.21532,-3.33662), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.21055,-3.23667,16.22777,-3.32222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.24055,-3.11083,16.18888,-3.06167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.18888,-3.06167,29.21916,-3.02111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.21916,-3.02111,29.1486,-2.99611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.1486,-2.99611,29.21916,-3.02111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.07444,-2.87472,16.19582,-2.8225), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.19582,-2.8225,28.98749,-2.81056), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.98749,-2.81056,16.19582,-2.8225), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.98777,-2.77,29.02415,-2.74445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.02415,-2.74445,28.98777,-2.77), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.88916,-2.65389,29.02415,-2.74445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.85332,-2.51361,28.88388,-2.47861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.88388,-2.47861,28.85332,-2.51361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.87278,-2.4412,28.86361,-2.41028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.86361,-2.41028,28.87278,-2.4412), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.88916,-2.36722,28.95777,-2.36222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.95777,-2.36222,28.88916,-2.36722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.99332,-2.28556,29.09194,-2.27306), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.09194,-2.27306,28.99332,-2.28556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.18388,-2.24583,29.1236,-2.22917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.1236,-2.22917,16.18388,-2.24583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.20694,-2.15889,29.1236,-2.22917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.17055,-2.08639,16.20694,-2.15889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.41444,-1.98111,29.12388,-1.90445), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.12388,-1.90445,16.41444,-1.98111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.1411,-1.81972,16.58388,-1.76667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.58388,-1.76667,29.1411,-1.81972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.23278,-1.69274,29.23632,-1.6877), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.23632,-1.6877,29.23278,-1.69274), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.3611,-1.51028,29.59747,-1.38525), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.59747,-1.38525,16.80722,-1.31639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.80722,-1.31639,29.59747,-1.38525), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((16.96805,-1.15389,17.30694,-1.01528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.30694,-1.01528,29.57833,-0.9), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.57833,-0.9,29.62833,-0.88889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.62833,-0.88889,29.57833,-0.9), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.63555,-0.67083,29.63277,-0.64611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.63277,-0.64611,17.63555,-0.67083), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.67194,-0.57222,29.63277,-0.64611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.6527,-0.47612,29.653,-0.47406), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.653,-0.47406,29.6527,-0.47612), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.73693,-0.44361,29.653,-0.47406), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.73677,-0.31637,17.73693,-0.44361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.70999,-0.17417,29.70989,-0.07581), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.70989,-0.07581,29.71082,-0.0693), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.71082,-0.0693,29.70989,-0.07581), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.71666,0.07222,17.8036,0.14861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.8036,0.14861,29.81388,0.15861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.81388,0.15861,17.8036,0.14861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.77722,0.17528,29.81388,0.15861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.9487,0.35261,29.85833,0.36667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.85833,0.36667,17.9487,0.35261), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.96693,0.44778,29.96249,0.48722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.96249,0.48722,17.96693,0.44778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.94416,0.5725,29.96249,0.48722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.96189,0.83036,17.87888,0.95333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((17.87888,0.95333,30.2161,0.9925), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.2161,0.9925,17.87888,0.95333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.23166,1.125,30.33221,1.15139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.33221,1.15139,30.27805,1.16889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.27805,1.16889,30.33221,1.15139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.3486,1.19611,30.27805,1.16889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.47239,1.2316,30.47565,1.23253), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.47565,1.23253,30.47239,1.2316), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.0386,1.44555,30.69333,1.49889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.69333,1.49889,18.0386,1.44555), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.07833,1.65528,31.03666,1.76556), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.03666,1.76556,18.07833,1.65528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.30277,2.12139,31.27972,2.17667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.27972,2.17667,31.27745,2.1782), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.27745,2.1782,31.27972,2.17667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.27745,2.1782,31.27972,2.17667), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.20249,2.22917,18.09333,2.22944), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.09333,2.22944,31.20249,2.22917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.04972,2.30389,31.19694,2.30583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.19694,2.30583,31.04972,2.30389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((31.07027,2.335,30.89805,2.33528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.89805,2.33528,31.07027,2.335), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.86221,2.35361,30.89805,2.33528), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.98721,2.40833,30.94638,2.40889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.94638,2.40889,30.98721,2.40833), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.21999,2.42722,30.82666,2.44222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.82666,2.44222,30.72916,2.44778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.72916,2.44778,30.82666,2.44222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.34202,2.61263,30.78694,2.67472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.78694,2.67472,18.40277,2.73139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.40277,2.73139,30.78694,2.67472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.85638,2.79083,18.40277,2.73139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.87777,2.89722,30.82499,2.98805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.82499,2.98805,30.76361,3.06722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.76361,3.06722,18.53933,3.07568), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.53933,3.07568,30.76361,3.06722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.61166,3.13194,18.53933,3.07568), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.64222,3.21056,18.61166,3.13194), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.93555,3.42472,18.626,3.47886), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.626,3.47886,30.86852,3.49214), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.86852,3.49214,18.626,3.47886), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.90916,3.52583,30.86852,3.49214), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.85527,3.5725,30.61472,3.60694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.61472,3.60694,30.55888,3.61389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.55888,3.61389,30.61472,3.60694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.76305,3.68278,30.58194,3.69722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.58194,3.69722,30.76305,3.68278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.59499,3.76917,30.58194,3.69722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.54444,3.87055,30.20527,3.9625), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.20527,3.9625,18.64888,4.0025), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.64888,4.0025,30.20527,3.9625), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.14027,4.10889,30.06527,4.12805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.06527,4.12805,22.39138,4.12861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.39138,4.12861,30.06527,4.12805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.25972,4.13416,22.39138,4.12861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.46805,4.15528,22.25972,4.13416), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((30.02666,4.20528,18.59138,4.23111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.59138,4.23111,21.92582,4.23416), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.92582,4.23416,18.59138,4.23111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.94527,4.24389,21.55194,4.24583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.55194,4.24583,29.94527,4.24389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.35527,4.28,28.36916,4.28278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.36916,4.28278,21.35527,4.28), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.44249,4.28778,28.36916,4.28278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.53777,4.29916,29.95666,4.30222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.6586,4.29916,29.95666,4.30222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.95666,4.30222,21.17221,4.30389), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.17221,4.30389,29.95666,4.30222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((21.28277,4.33417,18.54194,4.33555), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.54194,4.33555,21.28277,4.33417), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.20499,4.34361,29.83666,4.34639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.83666,4.34639,29.89972,4.34666), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.89972,4.34666,29.83666,4.34639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.20082,4.3475,29.89972,4.34666), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.33583,4.35111,28.20082,4.3475), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.25222,4.35111,28.20082,4.3475), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.61305,4.36916,18.71471,4.37028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.71471,4.37028,18.58221,4.37055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.58221,4.37055,18.71471,4.37028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.50583,4.37305,18.58221,4.37055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.79333,4.385,29.32166,4.38666), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.32166,4.38666,29.25444,4.3875), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.25444,4.3875,28.16638,4.38778), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.16638,4.38778,29.25444,4.3875), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.61472,4.40805,22.58722,4.41166), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.58722,4.41166,20.5761,4.41472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.5761,4.41472,22.58722,4.41166), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.77666,4.42333,28.65083,4.42361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.65083,4.42361,18.77666,4.42333), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.98444,4.42666,28.65083,4.42361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.06416,4.43111,20.98444,4.42666), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.8386,4.44916,29.06277,4.46111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.06277,4.46111,20.8386,4.44916), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.60138,4.47722,28.88277,4.47972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.88277,4.47972,22.60138,4.47722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.69416,4.49305,28.88277,4.47972), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.45638,4.51917,18.8161,4.52639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((18.8161,4.52639,20.45638,4.51917), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.92971,4.55111,28.7686,4.55639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.7686,4.55639,29.81472,4.56028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.81472,4.56028,28.03277,4.56055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((28.03277,4.56055,29.81472,4.56028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.42638,4.59194,27.78611,4.60694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.78611,4.60694,23.42638,4.59194), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.72666,4.62278,23.27222,4.62472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.27222,4.62472,22.72666,4.62278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.67471,4.62722,23.27222,4.62472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.44999,4.65889,29.47999,4.67694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((29.47999,4.67694,23.44999,4.65889), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.85583,4.70694,22.79277,4.72361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.79277,4.72361,23.18666,4.72444), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.18666,4.72444,22.79277,4.72361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.37027,4.72583,23.18666,4.72444), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.0225,4.74305,20.37027,4.72583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.78222,4.76028,23.70916,4.77583), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((23.70916,4.77583,27.78222,4.76028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((20.28111,4.79222,27.70388,4.79361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.70388,4.79361,20.28111,4.79222), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.89083,4.81917,22.97916,4.82861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((22.97916,4.82861,27.70277,4.83361), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.70277,4.83361,22.97916,4.82861), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.57805,4.895,24.15194,4.9), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.15194,4.9,27.64888,4.90194), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.64888,4.90194,24.15194,4.9), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.08888,4.91805,24.10555,4.92), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.67277,4.91805,24.10555,4.92), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.10555,4.92,19.08888,4.91805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.07582,4.94722,24.10555,4.92), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.97721,4.98778,25.11277,4.99805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.11277,4.99805,25.22777,5.00694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.22777,5.00694,25.11277,4.99805), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.45586,5.01714,25.22777,5.00694), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.28411,5.02781,26.87249,5.03139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.87249,5.03139,19.28411,5.02781), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.40194,5.03527,26.87249,5.03139), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.86583,5.04167,26.50055,5.04472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.50055,5.04472,19.86583,5.04167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.3236,5.0575,24.35499,5.06), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.35499,5.06,25.3236,5.0575), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.43166,5.06528,24.35499,5.06), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.41944,5.07472,24.52555,5.07722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.52555,5.07722,27.41944,5.07472), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.64638,5.08111,24.52555,5.07722), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.83833,5.08833,26.64638,5.08111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.4536,5.10639,24.39416,5.11555), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((24.39416,5.11555,19.39833,5.12), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.39833,5.12,24.39416,5.11555), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.94527,5.14611,19.56638,5.15028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((19.56638,5.15028,26.94527,5.14611), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.90527,5.16583,19.56638,5.15028), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.03111,5.19111,27.09638,5.20278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.31055,5.19111,27.09638,5.20278), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((27.09638,5.20278,26.03111,5.19111), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.87582,5.2175,26.1636,5.23), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.1636,5.23,25.97332,5.23167), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.97332,5.23167,26.1636,5.23), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.79583,5.23639,26.08833,5.24055), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((26.08833,5.24055,25.79583,5.23639), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.36194,5.31472,25.52083,5.3475), mapfile, tile_dir, 0, 11, "cd-zaire")
	render_tiles((25.52083,5.3475,25.36194,5.31472), mapfile, tile_dir, 0, 11, "cd-zaire")