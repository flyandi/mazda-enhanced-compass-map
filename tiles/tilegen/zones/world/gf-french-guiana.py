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
    # Region: GF
    # Region Name: French Guiana

	render_tiles((-54.105,2.11305,-54.14417,2.12055), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.14417,2.12055,-54.105,2.11305), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.24917,2.14667,-54.18056,2.1725), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.18056,2.1725,-53.26501,2.1925), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.26501,2.1925,-53.99889,2.19444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.99889,2.19444,-52.91639,2.19583), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.91639,2.19583,-53.99889,2.19444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.47694,2.21833,-53.11861,2.2225), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.11861,2.2225,-53.9325,2.22472), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.9325,2.22472,-53.11861,2.2225), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.47166,2.25667,-53.21889,2.25805), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.21889,2.25805,-53.47166,2.25667), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.93222,2.27194,-53.21889,2.25805), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.64806,2.28833,-53.93222,2.27194), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.8175,2.30944,-54.54695,2.31833), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.54695,2.31833,-54.61536,2.31978), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.61536,2.31978,-54.54695,2.31833), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.52417,2.33611,-54.61536,2.31978), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.33972,2.35278,-53.79667,2.35417), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.79667,2.35417,-54.57389,2.35472), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.57389,2.35472,-53.79667,2.35417), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.74501,2.37389,-54.57389,2.35472), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.42667,2.42722,-54.48324,2.42855), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.48324,2.42855,-54.42667,2.42722), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.5675,2.51555,-54.48324,2.42855), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.18056,2.84083,-52.44583,2.86333), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.44583,2.86333,-54.18056,2.84083), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.18639,2.90361,-52.44583,2.86333), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.17028,3.00833,-52.33556,3.06444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.33556,3.06444,-54.17028,3.00833), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.34416,3.15417,-54.19194,3.17889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.19194,3.17889,-52.34416,3.15417), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.09444,3.295,-54.19194,3.17889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.09444,3.295,-54.19194,3.17889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.00306,3.44278,-52.10333,3.445), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.10333,3.445,-54.00306,3.44278), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.98444,3.59944,-54.00917,3.63639), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.00917,3.63639,-54.06722,3.66444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.06722,3.66444,-54.00917,3.63639), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.12389,3.78555,-51.84972,3.84361), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.84972,3.84361,-54.12389,3.78555), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.28445,3.92639,-51.84972,3.84361), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.68639,4.03185,-51.6869,4.03301), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.6869,4.03301,-51.68639,4.03185), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.36028,4.035,-51.6869,4.03301), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.33167,4.14222,-51.64944,4.16139), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.64944,4.16139,-54.33167,4.14222), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.40222,4.18139,-51.64944,4.16139), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.65389,4.22194,-54.40222,4.18139), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.71667,4.32055,-52.04028,4.33139), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.04028,4.33139,-51.71667,4.32055), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.95862,4.36639,-52.04028,4.33139), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.95667,4.40333,-51.92639,4.40889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.92639,4.40889,-51.95667,4.40333), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.76917,4.54417,-51.97834,4.58694), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.97834,4.58694,-51.92639,4.61278), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.92639,4.61278,-51.97834,4.58694), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.85085,4.65333,-51.90029,4.66194), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-51.90029,4.66194,-51.85085,4.65333), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.43444,4.70861,-52.33362,4.72444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.33362,4.72444,-52.06473,4.73389), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.06473,4.73389,-54.47472,4.73666), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.47472,4.73666,-52.06473,4.73389), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.32751,4.77389,-54.47472,4.73666), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.27473,4.84194,-52.20723,4.86361), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.20723,4.86361,-52.25945,4.87194), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.25945,4.87194,-52.20723,4.86361), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.38583,4.89111,-52.25945,4.87194), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.27834,4.93111,-52.33362,4.94889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.33362,4.94889,-52.27834,4.93111), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.42001,5.00305,-54.45583,5.00444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.45583,5.00444,-52.42001,5.00305), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.57556,5.1025,-54.45583,5.00444), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.29056,5.24805,-54.16897,5.34632), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.16897,5.34632,-54.055,5.44417), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-54.055,5.44417,-52.93224,5.45389), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-52.93224,5.45389,-54.055,5.44417), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.48361,5.56805,-52.93224,5.45389), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.98195,5.69055,-53.745,5.72889), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.745,5.72889,-53.91167,5.75028), mapfile, tile_dir, 0, 11, "gf-french-guiana")
	render_tiles((-53.91167,5.75028,-53.745,5.72889), mapfile, tile_dir, 0, 11, "gf-french-guiana")