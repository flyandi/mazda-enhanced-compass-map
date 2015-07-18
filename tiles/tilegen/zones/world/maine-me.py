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
    # Region: Maine
    # Region Name: ME

	render_tiles((-68.88848,43.80378,-68.94443,43.83533), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.94443,43.83533,-68.84901,43.84984), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.84901,43.84984,-68.94443,43.83533), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.92401,43.88541,-68.87478,43.90472), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.92401,43.88541,-68.87478,43.90472), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.87478,43.90472,-68.92401,43.88541), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.70382,43.05983,-70.66596,43.07623), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.66596,43.07623,-70.7564,43.07999), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.7564,43.07999,-70.66596,43.07623), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.81955,43.12323,-70.8281,43.12909), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.8281,43.12909,-70.62251,43.13457), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.62251,43.13457,-70.8281,43.12909), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.59619,43.16347,-70.8248,43.17969), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.8248,43.17969,-70.82478,43.17976), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.82478,43.17976,-70.8248,43.17969), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.81312,43.21725,-70.57579,43.22186), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.57579,43.22186,-70.81312,43.21725), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.57699,43.22802,-70.57579,43.22186), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.58518,43.27011,-70.87259,43.27015), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.87259,43.27015,-70.58518,43.27011), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.55385,43.32189,-70.92395,43.32477), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.92395,43.32477,-70.55385,43.32189), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.53415,43.33396,-70.46598,43.34025), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.46598,43.34025,-70.5177,43.34404), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.5177,43.34404,-70.46598,43.34025), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.41631,43.36106,-70.98434,43.37613), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.98434,43.37613,-70.41631,43.36106), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.38398,43.41294,-70.96836,43.42928), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.96836,43.42928,-70.38398,43.41294), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.3273,43.45852,-70.96079,43.47409), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.96079,43.47409,-70.38562,43.48703), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.38562,43.48703,-70.96079,43.47409), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.95476,43.5098,-70.32112,43.52726), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.32112,43.52726,-70.33874,43.52811), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.33874,43.52811,-70.32112,43.52726), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.36121,43.52919,-70.33874,43.52811), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.2455,43.53964,-70.96379,43.54023), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.96379,43.54023,-70.2455,43.53964), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.20612,43.55763,-70.97272,43.57026), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.97272,43.57026,-70.20612,43.55763), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.21709,43.59672,-70.97272,43.57026), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.1907,43.64558,-70.09604,43.67228), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.09604,43.67228,-70.16823,43.67514), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.16823,43.67514,-70.09604,43.67228), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.98195,43.70096,-69.83347,43.70128), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.83347,43.70128,-70.98195,43.70096), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.85508,43.70475,-69.83347,43.70128), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.00127,43.71039,-70.06278,43.71336), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.06278,43.71336,-70.0713,43.71377), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.0713,43.71377,-70.06278,43.71336), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.80736,43.72808,-70.0713,43.71377), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.75409,43.74387,-69.98369,43.7444), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.98369,43.7444,-69.75409,43.74387), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.86216,43.75896,-69.88741,43.76659), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.88741,43.76659,-69.86216,43.75896), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.91559,43.77511,-69.88741,43.76659), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.71707,43.7924,-70.98726,43.79297), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.98726,43.79297,-69.71707,43.7924), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.69582,43.79605,-70.98726,43.79297), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.65082,43.80379,-69.69582,43.79605), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.57853,43.82332,-69.50329,43.83767), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.50329,43.83767,-70.98993,43.83924), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.98993,43.83924,-69.50329,43.83767), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.55261,43.84135,-70.98993,43.83924), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.32103,43.85671,-69.55261,43.84135), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.27992,43.87958,-69.32103,43.85671), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.43807,43.90954,-69.35458,43.91777), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.35458,43.91777,-69.24281,43.91882), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.24281,43.91882,-69.35458,43.91777), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.21294,43.9214,-69.42205,43.92305), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.42205,43.92305,-69.21294,43.9214), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.38049,43.94364,-69.39329,43.95642), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.39329,43.95642,-69.38049,43.94364), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.07703,43.97365,-69.13154,43.97609), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.13154,43.97609,-69.17498,43.97695), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.17498,43.97695,-69.13154,43.97609), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.65703,44.00382,-69.04391,44.00634), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.04391,44.00634,-68.65703,44.00382), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.61709,44.0101,-69.04391,44.00634), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.87414,44.02536,-69.06811,44.03977), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.06811,44.03977,-68.87414,44.02536), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.77965,44.05775,-68.5841,44.07159), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.5841,44.07159,-68.66938,44.07636), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.66938,44.07636,-68.9051,44.07734), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.9051,44.07734,-68.66938,44.07636), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.03188,44.07904,-68.9051,44.07734), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.53141,44.08985,-71.00137,44.09293), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.00137,44.09293,-68.53141,44.08985), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.50294,44.09972,-71.00137,44.09293), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.33103,44.10758,-68.50294,44.09972), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.43852,44.11618,-68.33103,44.10758), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.07567,44.12999,-68.93533,44.13038), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.93533,44.13038,-69.07567,44.12999), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.8886,44.15955,-69.05455,44.17154), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.05455,44.17154,-68.8886,44.15955), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.31479,44.19716,-68.93498,44.20291), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.93498,44.20291,-68.31479,44.19716), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.95189,44.21872,-68.17433,44.22591), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.17433,44.22591,-69.02107,44.23044), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.02107,44.23044,-69.04019,44.23367), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.04019,44.23367,-68.30652,44.23483), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.30652,44.23483,-69.04019,44.23367), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.00874,44.25883,-68.22949,44.26692), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.22949,44.26692,-71.00874,44.25883), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.01026,44.28477,-71.01127,44.30185), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.01127,44.30185,-68.19192,44.30668), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.19192,44.30668,-71.01127,44.30185), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.17361,44.3284,-68.04933,44.33073), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.04933,44.33073,-68.17361,44.3284), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.01358,44.34088,-68.04933,44.33073), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.10376,44.36436,-68.18916,44.37383), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.18916,44.37383,-68.10376,44.36436), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.97888,44.38703,-68.12562,44.38713), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.12562,44.38713,-67.97888,44.38703), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.01399,44.39026,-68.12562,44.38713), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.89957,44.39408,-68.01399,44.39026), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.94384,44.40702,-67.93653,44.41119), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.93653,44.41119,-67.94384,44.40702), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.85511,44.41943,-67.93653,44.41119), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.57973,44.42913,-67.85511,44.41943), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.01947,44.44042,-67.57973,44.42913), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.83794,44.46467,-67.50321,44.47692), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.50321,44.47692,-67.63481,44.48705), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.63481,44.48705,-67.79359,44.49478), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.79359,44.49478,-71.02299,44.50006), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.02299,44.50006,-67.70668,44.50198), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.70668,44.50198,-71.02299,44.50006), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.52117,44.50991,-67.70668,44.50198), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.65312,44.52582,-67.52117,44.50991), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.49175,44.55612,-67.65312,44.52582), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.2934,44.59927,-67.44851,44.60032), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.44851,44.60032,-67.2934,44.59927), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.39899,44.60263,-67.44851,44.60032), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.36827,44.62467,-67.23428,44.6372), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.23428,44.6372,-67.36827,44.62467), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.16986,44.66211,-67.23428,44.6372), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.11675,44.70611,-71.03671,44.7365), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.03671,44.7365,-67.07344,44.74196), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.07344,44.74196,-71.03671,44.7365), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.02615,44.7682,-67.07344,44.74196), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-66.9499,44.81742,-66.99296,44.84918), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-66.99296,44.84918,-66.9499,44.81742), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-66.98356,44.90328,-67.03347,44.93992), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.03347,44.93992,-66.98356,44.90328), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.05786,45.00005,-67.08207,45.02961), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.08207,45.02961,-71.05786,45.00005), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.09079,45.06872,-67.08207,45.02961), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.11241,45.11232,-67.33987,45.12559), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.33987,45.12559,-67.11241,45.11232), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.29821,45.14667,-67.39058,45.15411), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.39058,45.15411,-67.29821,45.14667), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.16125,45.16288,-67.20393,45.17141), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.20393,45.17141,-67.16125,45.16288), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.27108,45.19108,-67.20393,45.17141), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.84443,45.23451,-70.89282,45.23917), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.89282,45.23917,-67.45347,45.24113), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.45347,45.24113,-70.89282,45.23917), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.48026,45.26819,-70.83402,45.27179), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.83402,45.27179,-67.48026,45.26819), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.82979,45.28694,-70.91211,45.2962), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.91211,45.2962,-67.46055,45.30038), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.46055,45.30038,-70.91211,45.2962), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.08392,45.30545,-67.46055,45.30038), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.08392,45.30545,-67.46055,45.30038), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.80861,45.31161,-71.03821,45.31192), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.03821,45.31192,-70.80861,45.31161), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.94937,45.33154,-70.81947,45.34144), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.81947,45.34144,-71.01276,45.34476), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-71.01276,45.34476,-70.81947,45.34144), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.42724,45.37369,-70.80624,45.37656), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.80624,45.37656,-67.42724,45.37369), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.63466,45.38361,-70.80624,45.37656), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.678,45.39436,-70.72997,45.39936), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.72997,45.39936,-70.82561,45.40031), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.82561,45.40031,-70.72997,45.39936), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.6355,45.42782,-70.75557,45.42836), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.75557,45.42836,-70.6355,45.42782), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.78147,45.43116,-70.75557,45.42836), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.48433,45.45196,-70.6749,45.4524), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.6749,45.4524,-67.48433,45.45196), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.47686,45.49724,-67.41742,45.50199), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.41742,45.50199,-67.47686,45.49724), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.7234,45.51039,-67.41742,45.50199), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.68821,45.56398,-67.42365,45.57215), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.42365,45.57215,-70.68821,45.56398), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.53492,45.59543,-70.64958,45.59815), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.64958,45.59815,-67.53492,45.59543), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.45541,45.60467,-70.64958,45.59815), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.63176,45.62141,-70.59128,45.63055), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.59128,45.63055,-67.67542,45.63096), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.67542,45.63096,-70.59128,45.63055), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.55282,45.66781,-70.55279,45.66784), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.55279,45.66784,-70.55282,45.66781), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.80331,45.67789,-67.80289,45.67893), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.80289,45.67893,-67.71046,45.67937), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.71046,45.67937,-67.80289,45.67893), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.4469,45.70404,-67.71046,45.67937), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.78189,45.73119,-70.38355,45.73487), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.38355,45.73487,-67.78189,45.73119), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.80363,45.78162,-70.41568,45.78616), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.41568,45.78616,-67.80363,45.78162), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.39662,45.80849,-67.76396,45.82998), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.76396,45.82998,-70.39662,45.80849), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.32975,45.8538,-67.80368,45.86938), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.80368,45.86938,-70.32975,45.8538), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.25912,45.89076,-67.80368,45.86938), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.75042,45.9179,-70.25253,45.93318), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.25253,45.93318,-67.77998,45.93816), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.77998,45.93816,-70.25253,45.93318), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.31297,45.96186,-70.26541,45.96269), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.26541,45.96269,-70.31297,45.96186), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.30303,45.99898,-70.31763,46.01908), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.31763,46.01908,-67.78044,46.03845), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.78044,46.03845,-70.31763,46.01908), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.30673,46.06134,-67.78044,46.03845), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.26635,46.10099,-70.30673,46.06134), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.23957,46.14276,-70.26635,46.10099), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.2909,46.18584,-70.23957,46.14276), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.25549,46.24644,-67.78211,46.27938), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.78211,46.27938,-70.23268,46.28443), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.23268,46.28443,-67.78211,46.27938), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.20572,46.29987,-70.23268,46.28443), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.20742,46.33132,-70.16134,46.36098), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.16134,46.36098,-70.1186,46.38423), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.1186,46.38423,-70.16134,46.36098), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.08029,46.41053,-70.05375,46.42924), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.05375,46.42924,-70.08029,46.41053), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-70.02302,46.57349,-67.78841,46.6018), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.78841,46.6018,-70.02302,46.57349), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.99709,46.69523,-67.78841,46.6018), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.7898,46.79487,-69.81855,46.87503), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.81855,46.87503,-67.7898,46.79487), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.78976,47.06574,-67.88916,47.11877), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.88916,47.11877,-69.56638,47.12503), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.56638,47.12503,-67.88916,47.11877), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.90099,47.17852,-67.95227,47.19614), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.95227,47.19614,-68.96643,47.21271), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.96643,47.21271,-68.80354,47.21603), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.80354,47.21603,-67.99817,47.21784), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-67.99817,47.21784,-68.80354,47.21603), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.67591,47.24263,-69.0402,47.2451), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.0402,47.2451,-68.67591,47.24263), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.60482,47.24942,-69.4392,47.25003), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.4392,47.25003,-68.60482,47.24942), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.0829,47.27192,-68.58873,47.28172), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.58873,47.28172,-68.46006,47.28607), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.46006,47.28607,-68.58873,47.28172), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.37562,47.29227,-68.50743,47.29664), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.50743,47.29664,-68.37562,47.29227), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.15351,47.31404,-68.38428,47.32694), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.38428,47.32694,-68.20426,47.33973), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.20426,47.33973,-68.38428,47.32694), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.26971,47.35373,-68.36156,47.35561), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-68.36156,47.35561,-68.26971,47.35373), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.05389,47.37788,-68.36156,47.35561), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.0393,47.42217,-69.10822,47.43583), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.10822,47.43583,-69.0393,47.42217), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.15607,47.45104,-69.22,47.45716), mapfile, tile_dir, 0, 11, "maine-me")
	render_tiles((-69.22,47.45716,-69.15607,47.45104), mapfile, tile_dir, 0, 11, "maine-me")