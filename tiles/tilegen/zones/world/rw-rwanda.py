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
    # Region: RW
    # Region Name: Rwanda

	render_tiles((29.52749,-2.82667,30.48231,-2.8004), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.52749,-2.82667,30.48231,-2.8004), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.52749,-2.82667,30.48231,-2.8004), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.52749,-2.82667,30.48231,-2.8004), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.37944,-2.82611,29.52749,-1.51028), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.37944,-2.82611,29.52749,-1.51028), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.37944,-2.82611,29.52749,-1.51028), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.37944,-2.82611,29.52749,-1.51028), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.34972,-2.80806,29.52749,-1.51028), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.34972,-2.80806,29.52749,-1.51028), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.34972,-2.80806,29.52749,-1.51028), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.34972,-2.80806,29.52749,-1.51028), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.57125,-2.8004,29.52749,-1.38525), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.57125,-2.8004,29.52749,-1.38525), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.57125,-2.8004,29.52749,-1.38525), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.57125,-2.8004,29.52749,-1.38525), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.44388,-2.79583,30.48231,-2.82611), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.44388,-2.79583,30.48231,-2.82611), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.44388,-2.79583,30.48231,-2.82611), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.44388,-2.79583,30.48231,-2.82611), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.82194,-2.77278,29.52749,-1.31917), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.82194,-2.77278,29.52749,-1.31917), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.82194,-2.77278,29.52749,-1.31917), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.82194,-2.77278,29.52749,-1.31917), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.02415,-2.74445,30.48231,-2.28556), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.02415,-2.74445,30.48231,-2.28556), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.02415,-2.74445,30.48231,-2.28556), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.02415,-2.74445,30.48231,-2.28556), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.89944,-2.70556,29.52749,-1.45583), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.89944,-2.70556,29.52749,-1.45583), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.89944,-2.70556,29.52749,-1.45583), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.89944,-2.70556,29.52749,-1.45583), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.88916,-2.65389,30.48231,-2.47861), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.88916,-2.65389,30.48231,-2.47861), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.88916,-2.65389,30.48231,-2.47861), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.88916,-2.65389,30.48231,-2.47861), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.32888,-2.65361,30.48231,-2.80806), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.32888,-2.65361,30.48231,-2.80806), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.32888,-2.65361,30.48231,-2.80806), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.32888,-2.65361,30.48231,-2.80806), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.05888,-2.60472,30.48231,-2.27306), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.05888,-2.60472,30.48231,-2.27306), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.05888,-2.60472,30.48231,-2.27306), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.05888,-2.60472,30.48231,-2.27306), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.14055,-2.58917,29.52749,-1.81972), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.14055,-2.58917,29.52749,-1.81972), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.14055,-2.58917,29.52749,-1.81972), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.14055,-2.58917,29.52749,-1.81972), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.85332,-2.51361,30.48231,-2.41028), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.85332,-2.51361,30.48231,-2.41028), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.85332,-2.51361,30.48231,-2.41028), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.85332,-2.51361,30.48231,-2.41028), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.88388,-2.47861,30.48231,-2.65389), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.88388,-2.47861,30.48231,-2.65389), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.88388,-2.47861,30.48231,-2.65389), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.88388,-2.47861,30.48231,-2.65389), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.95471,-2.475,30.48231,-2.32111), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.95471,-2.475,30.48231,-2.32111), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.95471,-2.475,30.48231,-2.32111), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.95471,-2.475,30.48231,-2.32111), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.87278,-2.4412,30.48231,-2.41028), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.87278,-2.4412,30.48231,-2.41028), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.87278,-2.4412,30.48231,-2.41028), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.87278,-2.4412,30.48231,-2.41028), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.1536,-2.43056,29.52749,-1.33972), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.1536,-2.43056,29.52749,-1.33972), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.1536,-2.43056,29.52749,-1.33972), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.1536,-2.43056,29.52749,-1.33972), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.13243,-2.42516,30.48231,-2.43056), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.13243,-2.42516,30.48231,-2.43056), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.13243,-2.42516,30.48231,-2.43056), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.13243,-2.42516,30.48231,-2.43056), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.86361,-2.41028,30.48231,-2.4412), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.86361,-2.41028,30.48231,-2.4412), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.86361,-2.41028,30.48231,-2.4412), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.86361,-2.41028,30.48231,-2.4412), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.57375,-2.3994,29.52749,-1.2275), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.57375,-2.3994,29.52749,-1.2275), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.57375,-2.3994,29.52749,-1.2275), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.57375,-2.3994,29.52749,-1.2275), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.78202,-2.38075,29.52749,-1.92945), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.78202,-2.38075,29.52749,-1.92945), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.78202,-2.38075,29.52749,-1.92945), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.78202,-2.38075,29.52749,-1.92945), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.09361,-2.38056,30.48231,-2.42516), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.09361,-2.38056,30.48231,-2.42516), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.09361,-2.38056,30.48231,-2.42516), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.09361,-2.38056,30.48231,-2.42516), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.88916,-2.36722,30.48231,-2.47861), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.88916,-2.36722,30.48231,-2.47861), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.88916,-2.36722,30.48231,-2.47861), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.88916,-2.36722,30.48231,-2.47861), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.95777,-2.36222,30.48231,-2.28556), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.95777,-2.36222,30.48231,-2.28556), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.95777,-2.36222,30.48231,-2.28556), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.95777,-2.36222,30.48231,-2.28556), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.82916,-2.35778,29.52749,-1.67056), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.82916,-2.35778,29.52749,-1.67056), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.82916,-2.35778,29.52749,-1.67056), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.82916,-2.35778,29.52749,-1.67056), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.03504,-2.35033,30.48231,-2.3387), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.03504,-2.35033,30.48231,-2.3387), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.03504,-2.35033,30.48231,-2.3387), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.03504,-2.35033,30.48231,-2.3387), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.9911,-2.3425,29.52749,-1.46444), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.9911,-2.3425,29.52749,-1.46444), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.9911,-2.3425,29.52749,-1.46444), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.9911,-2.3425,29.52749,-1.46444), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.00735,-2.33961,30.48231,-2.3387), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.00735,-2.33961,30.48231,-2.3387), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.00735,-2.33961,30.48231,-2.3387), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.00735,-2.33961,30.48231,-2.3387), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.01251,-2.3387,30.48231,-2.33961), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.01251,-2.3387,30.48231,-2.33961), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.01251,-2.3387,30.48231,-2.33961), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.01251,-2.3387,30.48231,-2.33961), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.22499,-2.33861,29.52749,-1.275), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.22499,-2.33861,29.52749,-1.275), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.22499,-2.33861,29.52749,-1.275), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.22499,-2.33861,29.52749,-1.275), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.29598,-2.33702,29.52749,-1.21444), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.29598,-2.33702,29.52749,-1.21444), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.29598,-2.33702,29.52749,-1.21444), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.29598,-2.33702,29.52749,-1.21444), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.35255,-2.33576,29.52749,-1.065), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.35255,-2.33576,29.52749,-1.065), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.35255,-2.33576,29.52749,-1.065), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.35255,-2.33576,29.52749,-1.065), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.35615,-2.33568,29.52749,-1.065), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.35615,-2.33568,29.52749,-1.065), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.35615,-2.33568,29.52749,-1.065), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.35615,-2.33568,29.52749,-1.065), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.94694,-2.32111,30.48231,-2.475), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.94694,-2.32111,30.48231,-2.475), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.94694,-2.32111,30.48231,-2.475), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.94694,-2.32111,30.48231,-2.475), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.3811,-2.29944,30.48231,-2.33568), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.3811,-2.29944,30.48231,-2.33568), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.3811,-2.29944,30.48231,-2.33568), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.3811,-2.29944,30.48231,-2.33568), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((28.99332,-2.28556,30.48231,-2.74445), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((28.99332,-2.28556,30.48231,-2.74445), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((28.99332,-2.28556,30.48231,-2.74445), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((28.99332,-2.28556,30.48231,-2.74445), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.09194,-2.27306,30.48231,-2.22917), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.09194,-2.27306,30.48231,-2.22917), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.09194,-2.27306,30.48231,-2.22917), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.09194,-2.27306,30.48231,-2.22917), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.1236,-2.22917,29.52749,-1.90445), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.1236,-2.22917,29.52749,-1.90445), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.1236,-2.22917,29.52749,-1.90445), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.1236,-2.22917,29.52749,-1.90445), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.17055,-2.08639,29.52749,-1.81972), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.17055,-2.08639,29.52749,-1.81972), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.17055,-2.08639,29.52749,-1.81972), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.17055,-2.08639,29.52749,-1.81972), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.87638,-2.04695,29.52749,-1.67056), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.87638,-2.04695,29.52749,-1.67056), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.87638,-2.04695,29.52749,-1.67056), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.87638,-2.04695,29.52749,-1.67056), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.80833,-1.92945,30.48231,-2.35778), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.80833,-1.92945,30.48231,-2.35778), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.80833,-1.92945,30.48231,-2.35778), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.80833,-1.92945,30.48231,-2.35778), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.12388,-1.90445,30.48231,-2.22917), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.12388,-1.90445,30.48231,-2.22917), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.12388,-1.90445,30.48231,-2.22917), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.12388,-1.90445,30.48231,-2.22917), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.1411,-1.81972,30.48231,-2.58917), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.1411,-1.81972,30.48231,-2.58917), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.1411,-1.81972,30.48231,-2.58917), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.1411,-1.81972,30.48231,-2.58917), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.23278,-1.69274,29.52749,-1.6877), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.23278,-1.69274,29.52749,-1.6877), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.23278,-1.69274,29.52749,-1.6877), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.23278,-1.69274,29.52749,-1.6877), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.23632,-1.6877,29.52749,-1.69274), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.23632,-1.6877,29.52749,-1.69274), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.23632,-1.6877,29.52749,-1.69274), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.23632,-1.6877,29.52749,-1.69274), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.84027,-1.67056,30.48231,-2.35778), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.84027,-1.67056,30.48231,-2.35778), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.84027,-1.67056,30.48231,-2.35778), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.84027,-1.67056,30.48231,-2.35778), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.3611,-1.51028,30.48231,-2.80806), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.3611,-1.51028,30.48231,-2.80806), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.3611,-1.51028,30.48231,-2.80806), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.3611,-1.51028,30.48231,-2.80806), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.97499,-1.46444,30.48231,-2.3425), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.97499,-1.46444,30.48231,-2.3425), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.97499,-1.46444,30.48231,-2.3425), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.97499,-1.46444,30.48231,-2.3425), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.90194,-1.45583,30.48231,-2.70556), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.90194,-1.45583,30.48231,-2.70556), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.90194,-1.45583,30.48231,-2.70556), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.90194,-1.45583,30.48231,-2.70556), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.73916,-1.43694,30.48231,-2.38075), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.73916,-1.43694,30.48231,-2.38075), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.73916,-1.43694,30.48231,-2.38075), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.73916,-1.43694,30.48231,-2.38075), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.59747,-1.38525,30.48231,-2.8004), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.59747,-1.38525,30.48231,-2.8004), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.59747,-1.38525,30.48231,-2.8004), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.59747,-1.38525,30.48231,-2.8004), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.63166,-1.37306,30.48231,-2.3994), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.63166,-1.37306,30.48231,-2.3994), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.63166,-1.37306,30.48231,-2.3994), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.63166,-1.37306,30.48231,-2.3994), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.88333,-1.36778,30.48231,-2.70556), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.88333,-1.36778,30.48231,-2.70556), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.88333,-1.36778,30.48231,-2.70556), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.88333,-1.36778,30.48231,-2.70556), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.17055,-1.33972,29.52749,-1.275), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.17055,-1.33972,29.52749,-1.275), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.17055,-1.33972,29.52749,-1.275), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.17055,-1.33972,29.52749,-1.275), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((29.82916,-1.31917,30.48231,-2.77278), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((29.82916,-1.31917,30.48231,-2.77278), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((29.82916,-1.31917,30.48231,-2.77278), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((29.82916,-1.31917,30.48231,-2.77278), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.18472,-1.275,29.52749,-1.33972), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.18472,-1.275,29.52749,-1.33972), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.18472,-1.275,29.52749,-1.33972), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.18472,-1.275,29.52749,-1.33972), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.52277,-1.2275,29.52749,-1.06162), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.52277,-1.2275,29.52749,-1.06162), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.52277,-1.2275,29.52749,-1.06162), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.52277,-1.2275,29.52749,-1.06162), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.27972,-1.21444,30.48231,-2.33702), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.27972,-1.21444,30.48231,-2.33702), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.27972,-1.21444,30.48231,-2.33702), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.27972,-1.21444,30.48231,-2.33702), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.3561,-1.065,30.48231,-2.33568), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.3561,-1.065,30.48231,-2.33568), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.3561,-1.065,30.48231,-2.33568), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.3561,-1.065,30.48231,-2.33568), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 17, 17, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 0, 11, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 13, 13, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 15, 15, "rw-rwanda")
	render_tiles((30.48231,-1.06162,29.52749,-1.2275), mapfile, tile_dir, 17, 17, "rw-rwanda")