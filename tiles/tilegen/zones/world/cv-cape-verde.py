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
    # Region: CV
    # Region Name: Cape Verde

	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.18639,16.92167,-25.18639,17.12083), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.3125,16.925,-25.09167,17.00249), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.3125,16.925,-25.09167,17.00249), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.3125,16.925,-25.09167,17.00249), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.3125,16.925,-25.09167,17.00249), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.31722,17.00249,-25.09167,16.925), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.31722,17.00249,-25.09167,16.925), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.31722,17.00249,-25.09167,16.925), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.31722,17.00249,-25.09167,16.925), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.36056,17.05,-25.18639,17.09361), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.36056,17.05,-25.18639,17.09361), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.36056,17.05,-25.18639,17.09361), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.36056,17.05,-25.18639,17.09361), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.98278,17.07666,-25.18639,17.11278), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.98278,17.07666,-25.18639,17.11278), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.98278,17.07666,-25.18639,17.11278), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.98278,17.07666,-25.18639,17.11278), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.33445,17.09361,-25.09167,17.00249), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.33445,17.09361,-25.09167,17.00249), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.33445,17.09361,-25.09167,17.00249), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.33445,17.09361,-25.09167,17.00249), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.97472,17.11278,-25.18639,17.07666), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.97472,17.11278,-25.18639,17.07666), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.97472,17.11278,-25.18639,17.07666), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.97472,17.11278,-25.18639,17.07666), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.26556,17.12083,-25.09167,16.925), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.26556,17.12083,-25.09167,16.925), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.26556,17.12083,-25.09167,16.925), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.26556,17.12083,-25.09167,16.925), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-25.09167,17.19166,-25.09167,16.92167), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-25.09167,17.19166,-25.09167,16.92167), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-25.09167,17.19166,-25.09167,16.92167), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-25.09167,17.19166,-25.09167,16.92167), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.49361,14.90861,-23.76306,15.00611), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.66333,14.925,-23.49361,15.23611), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.66333,14.925,-23.49361,15.23611), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.66333,14.925,-23.49361,15.23611), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.66333,14.925,-23.49361,15.23611), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.72861,15.00111,-23.49361,15.27389), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.72861,15.00111,-23.49361,15.27389), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.72861,15.00111,-23.49361,15.27389), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.72861,15.00111,-23.49361,15.27389), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.44473,15.00611,-23.76306,14.90861), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.44473,15.00611,-23.76306,14.90861), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.44473,15.00611,-23.76306,14.90861), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.44473,15.00611,-23.76306,14.90861), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.79195,15.07166,-23.49361,15.27166), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.79195,15.07166,-23.49361,15.27166), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.79195,15.07166,-23.49361,15.27166), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.79195,15.07166,-23.49361,15.27166), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.76306,15.19833,-23.49361,15.27166), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.76306,15.19833,-23.49361,15.27166), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.76306,15.19833,-23.49361,15.27166), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.76306,15.19833,-23.49361,15.27166), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.65223,15.23611,-23.76306,14.925), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.65223,15.23611,-23.76306,14.925), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.65223,15.23611,-23.76306,14.925), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.65223,15.23611,-23.76306,14.925), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.77111,15.27166,-23.49361,15.19833), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.77111,15.27166,-23.49361,15.19833), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.77111,15.27166,-23.49361,15.19833), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.77111,15.27166,-23.49361,15.19833), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.70611,15.27389,-23.49361,15.30583), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.70611,15.27389,-23.49361,15.30583), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.70611,15.27389,-23.49361,15.30583), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.70611,15.27389,-23.49361,15.30583), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.69473,15.30583,-23.49361,15.27389), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.69473,15.30583,-23.49361,15.27389), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.69473,15.30583,-23.49361,15.27389), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.69473,15.30583,-23.49361,15.27389), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-23.76306,15.32444,-23.49361,15.27166), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-23.76306,15.32444,-23.49361,15.27166), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-23.76306,15.32444,-23.49361,15.27166), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-23.76306,15.32444,-23.49361,15.27166), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.36834,14.81222,-24.36834,15.0475), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.48056,14.855,-24.36834,15.00139), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.48056,14.855,-24.36834,15.00139), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.48056,14.855,-24.36834,15.00139), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.48056,14.855,-24.36834,15.00139), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.29945,14.86028,-24.36834,15.01139), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.29945,14.86028,-24.36834,15.01139), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.29945,14.86028,-24.36834,15.01139), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.29945,14.86028,-24.36834,15.01139), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.5225,14.93194,-24.38195,14.855), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.5225,14.93194,-24.38195,14.855), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.5225,14.93194,-24.38195,14.855), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.5225,14.93194,-24.38195,14.855), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.46806,15.00139,-24.38195,14.855), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.46806,15.00139,-24.38195,14.855), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.46806,15.00139,-24.38195,14.855), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.46806,15.00139,-24.38195,14.855), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.31361,15.01139,-24.38195,14.86028), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.31361,15.01139,-24.38195,14.86028), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.31361,15.01139,-24.38195,14.86028), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.31361,15.01139,-24.38195,14.86028), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-24.38195,15.0475,-24.38195,14.81222), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-24.38195,15.0475,-24.38195,14.81222), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-24.38195,15.0475,-24.38195,14.81222), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-24.38195,15.0475,-24.38195,14.81222), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.80083,15.97805,-22.80083,16.23388), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.90945,16.00222,-22.80083,16.23777), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.90945,16.00222,-22.80083,16.23777), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.90945,16.00222,-22.80083,16.23777), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.90945,16.00222,-22.80083,16.23777), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.9625,16.04499,-22.80083,16.23777), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.9625,16.04499,-22.80083,16.23777), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.9625,16.04499,-22.80083,16.23777), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.9625,16.04499,-22.80083,16.23777), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.68389,16.05583,-22.80083,16.1475), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.68389,16.05583,-22.80083,16.1475), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.68389,16.05583,-22.80083,16.1475), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.68389,16.05583,-22.80083,16.1475), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.6725,16.1475,-22.91806,16.05583), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.6725,16.1475,-22.91806,16.05583), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.6725,16.1475,-22.91806,16.05583), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.6725,16.1475,-22.91806,16.05583), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.84834,16.20111,-22.91806,15.97805), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.84834,16.20111,-22.91806,15.97805), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.84834,16.20111,-22.91806,15.97805), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.84834,16.20111,-22.91806,15.97805), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.79306,16.23388,-22.91806,15.97805), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.79306,16.23388,-22.91806,15.97805), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.79306,16.23388,-22.91806,15.97805), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.79306,16.23388,-22.91806,15.97805), mapfile, tile_dir, 17, 17, "cv-cape-verde")
	render_tiles((-22.91806,16.23777,-22.91806,16.00222), mapfile, tile_dir, 0, 11, "cv-cape-verde")
	render_tiles((-22.91806,16.23777,-22.91806,16.00222), mapfile, tile_dir, 13, 13, "cv-cape-verde")
	render_tiles((-22.91806,16.23777,-22.91806,16.00222), mapfile, tile_dir, 15, 15, "cv-cape-verde")
	render_tiles((-22.91806,16.23777,-22.91806,16.00222), mapfile, tile_dir, 17, 17, "cv-cape-verde")