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
    # Region: SB
    # Region Name: Solomon Islands

	render_tiles((157.1669,-8.14084,157.0491,-8.12778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.1669,-8.14084,157.0491,-8.12778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.0491,-8.12778,157.1669,-8.14084), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.2041,-8.05945,156.9677,-8.04639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.9677,-8.04639,157.2041,-8.05945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.9449,-7.97778,157.1705,-7.91195), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.1705,-7.91195,156.9841,-7.89306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.9841,-7.89306,157.1705,-7.91195), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.09109,-7.85222,156.9841,-7.89306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.3813,-9.63139,161.38161,-9.54725), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.3813,-9.63139,161.38161,-9.54725), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.38161,-9.54725,161.2574,-9.51611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.2574,-9.51611,161.38161,-9.54725), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.343,-9.46584,161.2574,-9.51611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.1636,-9.37389,161.27299,-9.29556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.27299,-9.29556,161.1636,-9.37389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.8661,-9.15639,161.18739,-9.05473), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.18739,-9.05473,160.8661,-9.15639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.0394,-8.86056,160.9711,-8.84417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.9711,-8.84417,161.0394,-8.86056), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.9305,-8.76361,160.688,-8.70889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.688,-8.70889,160.9305,-8.76361), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.0108,-8.65389,160.65581,-8.60917), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.65581,-8.60917,160.98801,-8.59639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.98801,-8.59639,160.65581,-8.60917), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.7188,-8.56445,160.9272,-8.54834), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.9272,-8.54834,160.7188,-8.56445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.64549,-8.43778,160.7216,-8.36806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.7216,-8.36806,160.80859,-8.3625), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.80859,-8.3625,160.7216,-8.36806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.5769,-8.34973,160.80859,-8.3625), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.5961,-8.3225,160.7202,-8.31028), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.7202,-8.31028,160.5961,-8.3225), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.713,-6.88195,155.87939,-6.80306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.713,-6.88195,155.87939,-6.80306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.87939,-6.80306,155.43269,-6.7975), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.43269,-6.7975,155.87939,-6.80306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.9149,-6.79194,155.43269,-6.7975), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.8199,-6.77972,155.9149,-6.79194), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.9238,-6.73556,155.9097,-6.73083), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.9097,-6.73083,155.9238,-6.73556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.9677,-6.71722,155.9097,-6.73083), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.1758,-6.54361,155.21609,-6.51639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.21609,-6.51639,155.9097,-6.51222), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.9097,-6.51222,155.21609,-6.51639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.2305,-6.36306,155.7411,-6.32083), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.7411,-6.32083,155.20441,-6.30861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.20441,-6.30861,155.7411,-6.32083), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.61501,-6.22,155.5627,-6.2175), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.5627,-6.2175,155.61501,-6.22), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.98579,-6.21139,155.5627,-6.2175), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.43269,-6.12083,154.86659,-6.05333), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.86659,-6.05333,155.40379,-5.99944), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.40379,-5.99944,154.7502,-5.94834), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.7502,-5.94834,155.29691,-5.91083), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.29691,-5.91083,154.7502,-5.94834), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.718,-5.67611,154.76801,-5.55833), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.76801,-5.55833,155.05859,-5.54806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((155.05859,-5.54806,154.91051,-5.54667), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.91051,-5.54667,155.05859,-5.54806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.69659,-5.43611,154.748,-5.43278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.748,-5.43278,154.69659,-5.43611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.7141,-7.94806,156.65939,-7.9425), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.7141,-7.94806,156.65939,-7.9425), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.65939,-7.9425,156.7141,-7.94806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.6483,-7.88278,156.6066,-7.87278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.6066,-7.87278,156.6483,-7.88278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.7236,-7.84611,156.6066,-7.87278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.7964,-7.7825,156.5838,-7.76417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.5838,-7.76417,156.7964,-7.7825), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.8058,-7.72278,156.5099,-7.71584), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.5099,-7.71584,156.8058,-7.72278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.7469,-7.70222,156.5099,-7.71584), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.6736,-7.63778,156.4975,-7.63695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.4975,-7.63695,156.6736,-7.63778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.57021,-7.57528,156.4975,-7.63695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.358,-10.84195,162.3866,-10.82306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.358,-10.84195,162.3866,-10.82306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.3866,-10.82306,162.2572,-10.80889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.2572,-10.80889,162.3866,-10.82306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.8877,-10.77,162.2572,-10.80889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.2894,-10.68861,161.8877,-10.77), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.58771,-10.58417,161.5219,-10.56389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5219,-10.56389,161.58771,-10.58417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5327,-10.48445,161.8447,-10.44945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.8447,-10.44945,162.1044,-10.44861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((162.1044,-10.44861,161.8447,-10.44945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.49159,-10.42028,162.1044,-10.44861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5136,-10.36917,161.2869,-10.33556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.2869,-10.33556,161.5136,-10.36917), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.4388,-10.21945,161.2916,-10.21611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.2916,-10.21611,161.4388,-10.21945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.3141,-10.20584,161.2916,-10.21611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.00301,-8.77111,158.0764,-8.72695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.00301,-8.77111,158.0764,-8.72695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.0764,-8.72695,157.8961,-8.70473), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.8961,-8.70473,158.0764,-8.72695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.1039,-8.65945,157.8961,-8.70473), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.06129,-8.61195,157.8755,-8.61), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.8755,-8.61,158.06129,-8.61195), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.14079,-8.56778,158.0105,-8.5475), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.0105,-8.5475,158.14079,-8.56778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.09219,-8.52389,157.99361,-8.50889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.99361,-8.50889,158.09219,-8.52389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.63161,-5.45861,154.668,-5.42944), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.63161,-5.45861,154.668,-5.42944), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.668,-5.42944,154.63161,-5.45861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.55881,-5.24361,154.7269,-5.19722), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.7269,-5.19722,154.68159,-5.16056), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.68159,-5.16056,154.5336,-5.13722), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.5336,-5.13722,154.5816,-5.11695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.5816,-5.11695,154.6947,-5.10472), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.6947,-5.10472,154.5816,-5.11695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.59579,-5.035,154.6588,-5.02389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.6588,-5.02389,154.6205,-5.01861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((154.6205,-5.01861,154.6588,-5.02389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.6414,-9.94139,160.8325,-9.85778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.6414,-9.94139,160.8325,-9.85778), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.8325,-9.85778,160.26019,-9.81528), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.26019,-9.81528,159.9519,-9.81), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.9519,-9.81,160.26019,-9.81528), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.80521,-9.77417,159.9519,-9.81), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.7664,-9.7375,160.7291,-9.71111), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.7291,-9.71111,159.7664,-9.7375), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.6394,-9.59584,159.6586,-9.58972), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.6586,-9.58972,160.6394,-9.59584), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.60519,-9.44639,159.9986,-9.4375), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.9986,-9.4375,159.60519,-9.44639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.9088,-9.41861,160.3569,-9.41528), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.3569,-9.41528,159.9088,-9.41861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.86909,-9.36334,159.60049,-9.3225), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.60049,-9.3225,159.86909,-9.36334), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.7052,-9.25445,159.60049,-9.3225), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.8811,-8.58334,157.79829,-8.55695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.8811,-8.58334,157.79829,-8.55695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.79829,-8.55695,157.8811,-8.58334), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.90581,-8.47972,157.5858,-8.44723), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.5858,-8.44723,157.618,-8.41667), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.618,-8.41667,157.6283,-8.3875), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.6283,-8.3875,157.5769,-8.37334), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.5769,-8.37334,157.6283,-8.3875), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.2664,-8.33528,157.3149,-8.3225), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.3149,-8.3225,157.2664,-8.33528), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.2191,-8.26556,157.53909,-8.25806), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.53909,-8.25806,157.2191,-8.26556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.7702,-8.24389,157.63161,-8.23639), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.63161,-8.23639,157.7702,-8.24389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.2327,-8.19972,157.30881,-8.19834), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.30881,-8.19834,157.2327,-8.19972), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.60831,-8.05306,157.4102,-8.00695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.4102,-8.00695,157.5089,-7.97111), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.5089,-7.97111,157.4102,-8.00695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5439,-9.7775,161.5719,-9.77417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5439,-9.7775,161.5719,-9.77417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.5719,-9.77417,161.5439,-9.7775), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.4472,-9.73473,161.5719,-9.77417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.4724,-9.68556,161.3972,-9.66945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.3972,-9.66945,161.4724,-9.68556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.55409,-9.63223,161.3972,-9.66945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((161.36411,-9.35111,161.55409,-9.63223), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.4975,-11.84584,160.53909,-11.81028), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.4975,-11.84584,160.53909,-11.81028), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.53909,-11.81028,160.5811,-11.78945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.5811,-11.78945,160.38721,-11.78667), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.38721,-11.78667,160.5811,-11.78945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.3605,-11.71834,160.4427,-11.68472), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.4427,-11.68472,160.3605,-11.71834), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.1349,-11.64445,160.2677,-11.63445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.2677,-11.63445,160.1349,-11.64445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.9669,-11.53445,159.9644,-11.50889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.9644,-11.50889,159.9669,-11.53445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((160.0144,-11.47195,159.9644,-11.50889), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.4438,-7.43389,157.2836,-7.35167), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.4438,-7.43389,157.2836,-7.35167), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.2836,-7.35167,157.5349,-7.35028), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.5349,-7.35028,157.2836,-7.35167), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.10609,-7.34778,157.5349,-7.35028), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.4288,-7.32445,157.5083,-7.30139), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.5083,-7.30139,157.4288,-7.32445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.06551,-7.275,157.5083,-7.30139), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.9361,-7.21917,157.21581,-7.18917), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.21581,-7.18917,156.9361,-7.21917), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((157.1066,-6.96695,156.6236,-6.87111), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.6236,-6.87111,156.888,-6.83556), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.888,-6.83556,156.6236,-6.87111), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.4511,-6.73083,156.64771,-6.65583), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.64771,-6.65583,156.4386,-6.64361), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.4386,-6.64361,156.64771,-6.65583), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((156.48579,-6.60417,156.4386,-6.64361), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.8322,-8.5475,159.895,-8.54611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.8322,-8.5475,159.895,-8.54611), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.895,-8.54611,159.8322,-8.5475), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.75661,-8.39306,159.81219,-8.38278), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.81219,-8.38278,159.75661,-8.39306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.5464,-8.34417,159.8499,-8.33), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.8499,-8.33,159.5464,-8.34417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.1386,-8.13945,159.53329,-8.10695), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.53329,-8.10695,159.1386,-8.13945), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((159.37019,-7.98611,158.77,-7.91333), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.77,-7.91333,158.99001,-7.8475), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.99001,-7.8475,158.67799,-7.8075), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.67799,-7.8075,158.9816,-7.79861), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.9816,-7.79861,158.67799,-7.8075), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.5883,-7.76417,158.8788,-7.74445), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.8788,-7.74445,158.5883,-7.76417), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.71021,-7.61056,158.5141,-7.60389), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.5141,-7.60389,158.71021,-7.61056), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.7458,-7.59306,158.5972,-7.59084), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.5972,-7.59084,158.7458,-7.59306), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.64301,-7.56722,158.4411,-7.55972), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.4411,-7.55972,158.64301,-7.56722), mapfile, tile_dir, 0, 11, "sb-solomon-islands")
	render_tiles((158.4655,-7.53528,158.4411,-7.55972), mapfile, tile_dir, 0, 11, "sb-solomon-islands")