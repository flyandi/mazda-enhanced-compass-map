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
    # Region: BZ
    # Region Name: Belize

	render_tiles((-89.21777,15.88972,-88.91096,15.89272), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.91096,15.89272,-89.21777,15.88972), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.98083,15.89805,-88.91096,15.89272), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.92418,15.98917,-88.98083,15.89805), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.20222,16.13861,-88.74583,16.14694), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.74583,16.14694,-89.20222,16.13861), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.74139,16.21833,-88.69084,16.24583), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.69084,16.24583,-88.55751,16.26638), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.55751,16.26638,-88.69084,16.24583), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.4164,16.43777,-89.18056,16.47499), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.18056,16.47499,-88.4164,16.43777), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.39168,16.53749,-89.18056,16.47499), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.3475,16.60277,-88.39168,16.53749), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.29945,16.77499,-89.15834,16.81139), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.15834,16.81139,-88.2489,16.81583), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.2489,16.81583,-89.15834,16.81139), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.27917,16.89,-88.2489,16.81583), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.21223,16.98277,-88.27917,16.89), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.28001,17.09777,-89.14417,17.1475), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.14417,17.1475,-88.28001,17.09777), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.29945,17.20499,-89.14417,17.1475), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.24306,17.48305,-89.14278,17.48333), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.14278,17.48333,-88.24306,17.48305), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.2075,17.48972,-89.14278,17.48333), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.17195,17.50972,-88.2075,17.48972), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.27695,17.57472,-88.2814,17.63611), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.2814,17.63611,-88.27695,17.57472), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.22751,17.71055,-88.2814,17.63611), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.14244,17.81868,-88.84862,17.87749), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.84862,17.87749,-88.20251,17.89027), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.20251,17.89027,-88.84862,17.87749), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.1434,17.9559,-88.14612,17.97333), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.14612,17.97333,-88.77339,17.98844), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.77339,17.98844,-88.14612,17.97333), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-89.03583,18.00555,-88.77339,17.98844), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.71056,18.06055,-89.03583,18.00555), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.68111,18.18555,-88.08223,18.19278), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.08223,18.19278,-88.68111,18.18555), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.61769,18.21624,-88.08223,18.19278), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.61769,18.21624,-88.08223,18.19278), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.35861,18.29555,-88.18333,18.33611), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.18333,18.33611,-88.0939,18.34916), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.0939,18.34916,-88.37862,18.3511), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.37862,18.3511,-88.0939,18.34916), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.20862,18.35305,-88.37862,18.3511), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.31639,18.36889,-88.09668,18.37444), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.09668,18.37444,-88.3864,18.37888), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.3864,18.37888,-88.09668,18.37444), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.34306,18.40916,-88.3864,18.37888), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.49222,18.46777,-88.28198,18.48521), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.28198,18.48521,-88.40527,18.48971), mapfile, tile_dir, 0, 11, "bz-belize")
	render_tiles((-88.40527,18.48971,-88.28198,18.48521), mapfile, tile_dir, 0, 11, "bz-belize")