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
    # Region: GD
    # Region Name: Grenada

	render_tiles((-61.7525,11.9964,-61.7469,11.9975), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7525,11.9964,-61.7469,11.9975), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7469,11.9975,-61.6989,11.9983), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6989,11.9983,-61.7469,11.9975), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7794,11.9997,-61.7425,12.0003), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7031,11.9997,-61.7425,12.0003), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7425,12.0003,-61.755,12.0008), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.755,12.0008,-61.7425,12.0003), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7836,12.0014,-61.7747,12.0017), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7747,12.0017,-61.7836,12.0014), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6961,12.0022,-61.7747,12.0017), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7381,12.0028,-61.7067,12.0033), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7067,12.0033,-61.7381,12.0028), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7694,12.0033,-61.7381,12.0028), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7175,12.0039,-61.7114,12.0042), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7114,12.0042,-61.7175,12.0039), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7872,12.0047,-61.7114,12.0042), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7653,12.0058,-61.6928,12.0061), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7344,12.0058,-61.6928,12.0061), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6928,12.0061,-61.7653,12.0058), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7561,12.0061,-61.7653,12.0058), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7217,12.0067,-61.6928,12.0061), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7611,12.0086,-61.7897,12.0092), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7897,12.0092,-61.7611,12.0086), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7308,12.0092,-61.7611,12.0086), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.725,12.0103,-61.7897,12.0092), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7853,12.0103,-61.7897,12.0092), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6919,12.0122,-61.7811,12.0128), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7811,12.0128,-61.6919,12.0122), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7769,12.0153,-61.6833,12.0161), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6833,12.0161,-61.7769,12.0153), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6897,12.0169,-61.6833,12.0161), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7725,12.0181,-61.6792,12.0186), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6792,12.0186,-61.7725,12.0181), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7681,12.0206,-61.6719,12.0225), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6719,12.0225,-61.7639,12.0231), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7639,12.0231,-61.6767,12.0233), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6767,12.0233,-61.7639,12.0231), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6669,12.0244,-61.6767,12.0233), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7594,12.0256,-61.6669,12.0244), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6633,12.0275,-61.6575,12.0286), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6575,12.0286,-61.6514,12.0292), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6514,12.0292,-61.7567,12.0294), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7567,12.0294,-61.6514,12.0292), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6469,12.0317,-61.7533,12.0336), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7533,12.0336,-61.6425,12.0342), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6425,12.0342,-61.7533,12.0336), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7492,12.0361,-61.6389,12.0375), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6389,12.0375,-61.7492,12.0361), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7461,12.04,-61.6353,12.0408), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6353,12.0408,-61.7461,12.04), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6317,12.0442,-61.7447,12.0456), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7447,12.0456,-61.6317,12.0442), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6286,12.0481,-61.7447,12.0456), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.745,12.0517,-61.6261,12.0528), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6261,12.0528,-61.745,12.0517), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7461,12.0572,-61.6247,12.0581), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6247,12.0581,-61.7461,12.0572), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7489,12.0614,-61.6239,12.0642), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6239,12.0642,-61.7519,12.065), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7519,12.065,-61.6239,12.0642), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7511,12.0686,-61.6214,12.0689), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6214,12.0689,-61.7511,12.0686), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6183,12.0731,-61.7511,12.0739), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7511,12.0739,-61.6183,12.0731), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6147,12.0761,-61.7536,12.0781), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7536,12.0781,-61.6111,12.0794), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6111,12.0794,-61.7536,12.0781), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7553,12.0831,-61.6131,12.0844), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6131,12.0844,-61.7553,12.0831), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7572,12.0881,-61.6131,12.0844), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6161,12.0881,-61.6131,12.0844), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6189,12.0922,-61.7556,12.0933), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7556,12.0933,-61.6189,12.0922), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7528,12.0972,-61.6194,12.0983), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6194,12.0983,-61.7528,12.0972), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7489,12.1006,-61.6194,12.0983), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6203,12.1039,-61.7469,12.1053), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7469,12.1053,-61.6203,12.1039), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6217,12.1094,-61.745,12.1108), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.745,12.1108,-61.6217,12.1094), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6214,12.1164,-61.7442,12.1169), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7442,12.1169,-61.6214,12.1164), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6039,12.1189,-61.6106,12.1192), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6106,12.1192,-61.6178,12.1194), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6178,12.1194,-61.6106,12.1192), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.5986,12.1208,-61.6178,12.1194), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7431,12.1231,-61.5986,12.1208), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.5964,12.1256,-61.7431,12.1231), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7414,12.1283,-61.5964,12.1256), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.5975,12.1311,-61.7414,12.1283), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7397,12.1339,-61.6,12.1353), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6,12.1353,-61.7397,12.1339), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7375,12.1386,-61.6028,12.1394), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6028,12.1394,-61.7375,12.1386), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7339,12.1419,-61.6028,12.1394), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6047,12.1444,-61.7308,12.1458), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7308,12.1458,-61.6047,12.1444), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7272,12.1492,-61.6042,12.15), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6042,12.15,-61.7272,12.1492), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7247,12.1539,-61.6033,12.1561), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6033,12.1561,-61.7247,12.1539), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7253,12.16,-61.6019,12.1614), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6019,12.1614,-61.7253,12.16), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7247,12.1667,-61.6,12.1669), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6,12.1669,-61.7247,12.1667), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7247,12.1722,-61.5997,12.1736), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.5997,12.1736,-61.7247,12.1722), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7217,12.1764,-61.5997,12.1736), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6,12.18,-61.7189,12.1803), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7189,12.1803,-61.6,12.18), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7158,12.1842,-61.5997,12.1867), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.5997,12.1867,-61.7128,12.1883), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7128,12.1883,-61.5997,12.1867), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6025,12.1908,-61.7097,12.1922), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7097,12.1922,-61.6025,12.1908), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6042,12.1958,-61.7075,12.1969), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7075,12.1969,-61.6042,12.1958), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.7031,12.1994,-61.6061,12.2008), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6061,12.2008,-61.6986,12.2019), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6986,12.2019,-61.6061,12.2008), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.695,12.2053,-61.6081,12.2056), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6081,12.2056,-61.695,12.2053), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6928,12.21,-61.6094,12.2111), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6094,12.2111,-61.6928,12.21), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.69,12.2139,-61.6069,12.2158), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6069,12.2158,-61.69,12.2139), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6867,12.2181,-61.6069,12.2158), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6047,12.2206,-61.6831,12.2214), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6831,12.2214,-61.6047,12.2206), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6064,12.2242,-61.6794,12.2244), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6794,12.2244,-61.6064,12.2242), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.61,12.2278,-61.6708,12.2297), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6758,12.2278,-61.6708,12.2297), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6708,12.2297,-61.6653,12.2308), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6653,12.2308,-61.6119,12.2311), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6119,12.2311,-61.6653,12.2308), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6175,12.2328,-61.6244,12.2331), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6308,12.2328,-61.6244,12.2331), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6244,12.2331,-61.6175,12.2328), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6369,12.2336,-61.6617,12.2339), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6617,12.2339,-61.6369,12.2336), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6433,12.2347,-61.6617,12.2339), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6586,12.2367,-61.6475,12.2375), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6475,12.2375,-61.6531,12.2378), mapfile, tile_dir, 0, 11, "gd-grenada")
	render_tiles((-61.6531,12.2378,-61.6475,12.2375), mapfile, tile_dir, 0, 11, "gd-grenada")