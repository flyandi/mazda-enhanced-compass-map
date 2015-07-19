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
    # Region: UY
    # Region Name: Uruguay

	render_tiles((-54.95251,-34.97778,-56.97583,-34.94029), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.95251,-34.97778,-56.97583,-34.94029), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.95251,-34.97778,-56.97583,-34.94029), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.95251,-34.97778,-56.97583,-34.94029), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.95639,-34.94029,-56.97583,-34.97778), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.95639,-34.94029,-56.97583,-34.97778), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.95639,-34.94029,-56.97583,-34.97778), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.95639,-34.94029,-56.97583,-34.97778), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.28001,-34.91029,-54.95251,-30.52889), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.28001,-34.91029,-54.95251,-30.52889), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.28001,-34.91029,-54.95251,-30.52889), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.28001,-34.91029,-54.95251,-30.52889), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.25695,-34.90723,-54.95251,-31.24528), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.25695,-34.90723,-54.95251,-31.24528), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.25695,-34.90723,-54.95251,-31.24528), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.25695,-34.90723,-54.95251,-31.24528), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.02473,-34.89084,-54.95251,-30.94445), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.02473,-34.89084,-54.95251,-30.94445), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.02473,-34.89084,-54.95251,-30.94445), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.02473,-34.89084,-54.95251,-30.94445), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.08362,-34.88584,-54.95251,-31.32695), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.08362,-34.88584,-54.95251,-31.32695), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.08362,-34.88584,-54.95251,-31.32695), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.08362,-34.88584,-54.95251,-31.32695), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.28639,-34.87029,-56.97583,-34.90723), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.28639,-34.87029,-56.97583,-34.90723), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.28639,-34.87029,-56.97583,-34.90723), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.28639,-34.87029,-56.97583,-34.90723), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.42639,-34.83807,-56.97583,-34.79445), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.42639,-34.83807,-56.97583,-34.79445), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.42639,-34.83807,-56.97583,-34.79445), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.42639,-34.83807,-56.97583,-34.79445), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.38583,-34.80612,-54.95251,-30.97139), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.38583,-34.80612,-54.95251,-30.97139), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.38583,-34.80612,-54.95251,-30.97139), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.38583,-34.80612,-54.95251,-30.97139), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.36751,-34.79445,-56.97583,-34.83807), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.36751,-34.79445,-56.97583,-34.83807), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.36751,-34.79445,-56.97583,-34.83807), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.36751,-34.79445,-56.97583,-34.83807), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.71889,-34.78001,-54.95251,-30.94223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.71889,-34.78001,-54.95251,-30.94223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.71889,-34.78001,-54.95251,-30.94223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.71889,-34.78001,-54.95251,-30.94223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.41667,-34.7564,-54.95251,-31.6725), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.41667,-34.7564,-54.95251,-31.6725), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.41667,-34.7564,-54.95251,-31.6725), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.41667,-34.7564,-54.95251,-31.6725), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.28306,-34.69779,-56.97583,-34.59306), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.28306,-34.69779,-56.97583,-34.59306), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.28306,-34.69779,-56.97583,-34.59306), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.28306,-34.69779,-56.97583,-34.59306), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.25695,-34.68945,-56.97583,-34.57667), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.25695,-34.68945,-56.97583,-34.57667), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.25695,-34.68945,-56.97583,-34.57667), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.25695,-34.68945,-56.97583,-34.57667), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.28028,-34.6739,-56.97583,-34.59306), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.28028,-34.6739,-56.97583,-34.59306), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.28028,-34.6739,-56.97583,-34.59306), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.28028,-34.6739,-56.97583,-34.59306), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.14528,-34.67139,-56.97583,-34.63584), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.14528,-34.67139,-56.97583,-34.63584), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.14528,-34.67139,-56.97583,-34.63584), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.14528,-34.67139,-56.97583,-34.63584), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.89362,-34.66361,-54.95251,-30.10056), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.89362,-34.66361,-54.95251,-30.10056), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.89362,-34.66361,-54.95251,-30.10056), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.89362,-34.66361,-54.95251,-30.10056), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.34584,-34.64362,-56.97583,-34.69779), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.34584,-34.64362,-56.97583,-34.69779), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.34584,-34.64362,-56.97583,-34.69779), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.34584,-34.64362,-56.97583,-34.69779), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.14084,-34.63584,-56.97583,-34.67139), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.14084,-34.63584,-56.97583,-34.67139), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.14084,-34.63584,-56.97583,-34.67139), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.14084,-34.63584,-56.97583,-34.67139), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.28167,-34.59306,-56.97583,-34.69779), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.28167,-34.59306,-56.97583,-34.69779), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.28167,-34.59306,-56.97583,-34.69779), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.28167,-34.59306,-56.97583,-34.69779), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.25362,-34.57667,-56.97583,-34.68945), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.25362,-34.57667,-56.97583,-34.68945), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.25362,-34.57667,-56.97583,-34.68945), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.25362,-34.57667,-56.97583,-34.68945), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.84028,-34.49473,-54.95251,-31.04639), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.84028,-34.49473,-54.95251,-31.04639), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.84028,-34.49473,-54.95251,-31.04639), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.84028,-34.49473,-54.95251,-31.04639), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.11806,-34.46223,-54.95251,-30.185), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.11806,-34.46223,-54.95251,-30.185), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.11806,-34.46223,-54.95251,-30.185), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.11806,-34.46223,-54.95251,-30.185), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.90417,-34.45084,-54.95251,-31.94917), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.90417,-34.45084,-54.95251,-31.94917), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.90417,-34.45084,-54.95251,-31.94917), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.90417,-34.45084,-54.95251,-31.94917), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.36501,-34.44306,-54.95251,-30.29278), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.36501,-34.44306,-54.95251,-30.29278), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.36501,-34.44306,-54.95251,-30.29278), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.36501,-34.44306,-54.95251,-30.29278), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.77139,-34.39084,-56.97583,-34.26223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.77139,-34.39084,-56.97583,-34.26223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.77139,-34.39084,-56.97583,-34.26223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.77139,-34.39084,-56.97583,-34.26223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.75306,-34.26223,-54.95251,-32.05556), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.75306,-34.26223,-54.95251,-32.05556), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.75306,-34.26223,-54.95251,-32.05556), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.75306,-34.26223,-54.95251,-32.05556), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.08362,-34.18696,-56.97583,-33.05501), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.08362,-34.18696,-56.97583,-33.05501), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.08362,-34.18696,-56.97583,-33.05501), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.08362,-34.18696,-56.97583,-33.05501), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.20862,-34.15918,-54.95251,-32.45335), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.20862,-34.15918,-54.95251,-32.45335), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.20862,-34.15918,-54.95251,-32.45335), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.20862,-34.15918,-54.95251,-32.45335), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.53722,-34.06612,-56.97583,-33.19945), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.53722,-34.06612,-56.97583,-33.19945), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.53722,-34.06612,-56.97583,-33.19945), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.53722,-34.06612,-56.97583,-33.19945), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.41028,-33.90974,-56.97583,-33.55446), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.41028,-33.90974,-56.97583,-33.55446), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.41028,-33.90974,-56.97583,-33.55446), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.41028,-33.90974,-56.97583,-33.55446), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.4775,-33.84446,-56.97583,-33.09278), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.4775,-33.84446,-56.97583,-33.09278), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.4775,-33.84446,-56.97583,-33.09278), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.4775,-33.84446,-56.97583,-33.09278), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.37582,-33.74773,-56.97583,-32.57056), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.37582,-33.74773,-56.97583,-32.57056), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.37582,-33.74773,-56.97583,-32.57056), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.37582,-33.74773,-56.97583,-32.57056), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.52306,-33.6814,-56.97583,-33.58515), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.52306,-33.6814,-56.97583,-33.58515), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.52306,-33.6814,-56.97583,-33.58515), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.52306,-33.6814,-56.97583,-33.58515), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.52412,-33.58515,-56.97583,-33.58084), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.52412,-33.58515,-56.97583,-33.58084), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.52412,-33.58515,-56.97583,-33.58084), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.52412,-33.58515,-56.97583,-33.58084), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.52417,-33.58084,-56.97583,-33.58515), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.52417,-33.58084,-56.97583,-33.58515), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.52417,-33.58084,-56.97583,-33.58515), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.52417,-33.58084,-56.97583,-33.58515), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.43862,-33.55446,-56.97583,-33.90974), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.43862,-33.55446,-56.97583,-33.90974), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.43862,-33.55446,-56.97583,-33.90974), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.43862,-33.55446,-56.97583,-33.90974), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.50111,-33.40805,-56.97583,-33.09278), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.50111,-33.40805,-56.97583,-33.09278), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.50111,-33.40805,-56.97583,-33.09278), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.50111,-33.40805,-56.97583,-33.09278), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.34639,-33.27418,-56.97583,-33.15085), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.34639,-33.27418,-56.97583,-33.15085), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.34639,-33.27418,-56.97583,-33.15085), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.34639,-33.27418,-56.97583,-33.15085), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.53056,-33.19945,-56.97583,-33.58084), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.53056,-33.19945,-56.97583,-33.58084), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.53056,-33.19945,-56.97583,-33.58084), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.53056,-33.19945,-56.97583,-33.58084), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.36667,-33.15085,-56.97583,-33.27418), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.36667,-33.15085,-56.97583,-33.27418), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.36667,-33.15085,-56.97583,-33.27418), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.36667,-33.15085,-56.97583,-33.27418), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.34639,-33.11862,-56.97583,-33.15085), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.34639,-33.11862,-56.97583,-33.15085), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.34639,-33.11862,-56.97583,-33.15085), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.34639,-33.11862,-56.97583,-33.15085), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.16667,-33.11195,-54.95251,-31.85417), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.16667,-33.11195,-54.95251,-31.85417), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.16667,-33.11195,-54.95251,-31.85417), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.16667,-33.11195,-54.95251,-31.85417), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.49667,-33.09278,-56.97583,-33.40805), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.49667,-33.09278,-56.97583,-33.40805), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.49667,-33.09278,-56.97583,-33.40805), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.49667,-33.09278,-56.97583,-33.40805), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.08778,-33.05501,-56.97583,-34.18696), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.08778,-33.05501,-56.97583,-34.18696), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.08778,-33.05501,-56.97583,-34.18696), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.08778,-33.05501,-56.97583,-34.18696), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.04556,-32.93473,-54.95251,-31.78639), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.04556,-32.93473,-54.95251,-31.78639), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.04556,-32.93473,-54.95251,-31.78639), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.04556,-32.93473,-54.95251,-31.78639), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.05528,-32.87807,-56.97583,-32.93473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.05528,-32.87807,-56.97583,-32.93473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.05528,-32.87807,-56.97583,-32.93473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.05528,-32.87807,-56.97583,-32.93473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.13195,-32.79862,-54.95251,-31.99028), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.13195,-32.79862,-54.95251,-31.99028), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.13195,-32.79862,-54.95251,-31.99028), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.13195,-32.79862,-54.95251,-31.99028), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.11167,-32.74696,-56.97583,-32.72417), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.11167,-32.74696,-56.97583,-32.72417), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.11167,-32.74696,-56.97583,-32.72417), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.11167,-32.74696,-56.97583,-32.72417), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.095,-32.72417,-56.97583,-32.74696), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.095,-32.72417,-56.97583,-32.74696), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.095,-32.72417,-56.97583,-32.74696), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.095,-32.72417,-56.97583,-32.74696), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.17174,-32.66606,-56.97583,-32.74696), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.17174,-32.66606,-56.97583,-32.74696), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.17174,-32.66606,-56.97583,-32.74696), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.17174,-32.66606,-56.97583,-32.74696), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.25528,-32.60279,-56.97583,-32.66606), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.25528,-32.60279,-56.97583,-32.66606), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.25528,-32.60279,-56.97583,-32.66606), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.25528,-32.60279,-56.97583,-32.66606), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.40028,-32.57056,-56.97583,-33.74773), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.40028,-32.57056,-56.97583,-33.74773), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.40028,-32.57056,-56.97583,-33.74773), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.40028,-32.57056,-56.97583,-33.74773), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.1983,-32.45335,-56.97583,-34.15918), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.62944,-32.36612,-54.95251,-32.14278), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.62944,-32.36612,-54.95251,-32.14278), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.62944,-32.36612,-54.95251,-32.14278), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.62944,-32.36612,-54.95251,-32.14278), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.10139,-32.32806,-54.95251,-32.25918), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.10139,-32.32806,-54.95251,-32.25918), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.10139,-32.32806,-54.95251,-32.25918), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.10139,-32.32806,-54.95251,-32.25918), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.09944,-32.25918,-54.95251,-32.32806), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.09944,-32.25918,-54.95251,-32.32806), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.09944,-32.25918,-54.95251,-32.32806), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.09944,-32.25918,-54.95251,-32.32806), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.18444,-32.15501,-54.95251,-31.92167), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.18444,-32.15501,-54.95251,-31.92167), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.18444,-32.15501,-54.95251,-31.92167), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.18444,-32.15501,-54.95251,-31.92167), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.71889,-32.14278,-54.95251,-32.05556), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.71889,-32.14278,-54.95251,-32.05556), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.71889,-32.14278,-54.95251,-32.05556), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.71889,-32.14278,-54.95251,-32.05556), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.74695,-32.05556,-56.97583,-34.26223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.74695,-32.05556,-56.97583,-34.26223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.74695,-32.05556,-56.97583,-34.26223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.74695,-32.05556,-56.97583,-34.26223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.86389,-32.01168,-56.97583,-34.45084), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.86389,-32.01168,-56.97583,-34.45084), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.86389,-32.01168,-56.97583,-34.45084), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.86389,-32.01168,-56.97583,-34.45084), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.14639,-31.99028,-56.97583,-32.79862), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.14639,-31.99028,-56.97583,-32.79862), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.14639,-31.99028,-56.97583,-32.79862), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.14639,-31.99028,-56.97583,-32.79862), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-53.9075,-31.94917,-56.97583,-34.45084), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-53.9075,-31.94917,-56.97583,-34.45084), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-53.9075,-31.94917,-56.97583,-34.45084), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-53.9075,-31.94917,-56.97583,-34.45084), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.18388,-31.92167,-54.95251,-32.15501), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.18388,-31.92167,-54.95251,-32.15501), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.18388,-31.92167,-54.95251,-32.15501), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.18388,-31.92167,-54.95251,-32.15501), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.12418,-31.91556,-56.97583,-34.63584), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.12418,-31.91556,-56.97583,-34.63584), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.12418,-31.91556,-56.97583,-34.63584), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.12418,-31.91556,-56.97583,-34.63584), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.06722,-31.875,-54.95251,-31.91556), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.06722,-31.875,-54.95251,-31.91556), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.06722,-31.875,-54.95251,-31.91556), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.06722,-31.875,-54.95251,-31.91556), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.17889,-31.85417,-54.95251,-31.92167), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.17889,-31.85417,-54.95251,-31.92167), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.17889,-31.85417,-54.95251,-31.92167), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.17889,-31.85417,-54.95251,-31.92167), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.03722,-31.78639,-56.97583,-32.93473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.03722,-31.78639,-56.97583,-32.93473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.03722,-31.78639,-56.97583,-32.93473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.03722,-31.78639,-56.97583,-32.93473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.46389,-31.6725,-54.95251,-31.58139), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.46389,-31.6725,-54.95251,-31.58139), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.46389,-31.6725,-54.95251,-31.58139), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.46389,-31.6725,-54.95251,-31.58139), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.97916,-31.61028,-54.95251,-31.55251), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.97916,-31.61028,-54.95251,-31.55251), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.97916,-31.61028,-54.95251,-31.55251), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.97916,-31.61028,-54.95251,-31.55251), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.47028,-31.58139,-54.95251,-31.6725), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.47028,-31.58139,-54.95251,-31.6725), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.47028,-31.58139,-54.95251,-31.6725), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.47028,-31.58139,-54.95251,-31.6725), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.98695,-31.55251,-54.95251,-31.40167), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.98695,-31.55251,-54.95251,-31.40167), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.98695,-31.55251,-54.95251,-31.40167), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.98695,-31.55251,-54.95251,-31.40167), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.0775,-31.48417,-54.95251,-31.45667), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.0775,-31.48417,-54.95251,-31.45667), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.0775,-31.48417,-54.95251,-31.45667), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.0775,-31.48417,-54.95251,-31.45667), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.58805,-31.46278,-54.95251,-31.58139), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.58805,-31.46278,-54.95251,-31.58139), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.58805,-31.46278,-54.95251,-31.58139), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.58805,-31.46278,-54.95251,-31.58139), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-58.07611,-31.45667,-54.95251,-31.48417), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-58.07611,-31.45667,-54.95251,-31.48417), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-58.07611,-31.45667,-54.95251,-31.48417), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-58.07611,-31.45667,-54.95251,-31.48417), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.98889,-31.40167,-54.95251,-31.55251), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.98889,-31.40167,-54.95251,-31.55251), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.98889,-31.40167,-54.95251,-31.55251), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.98889,-31.40167,-54.95251,-31.55251), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-54.90834,-31.37667,-56.97583,-34.97778), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-54.90834,-31.37667,-56.97583,-34.97778), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-54.90834,-31.37667,-56.97583,-34.97778), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-54.90834,-31.37667,-56.97583,-34.97778), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.06528,-31.32695,-56.97583,-34.88584), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.06528,-31.32695,-56.97583,-34.88584), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.06528,-31.32695,-56.97583,-34.88584), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.06528,-31.32695,-56.97583,-34.88584), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.02,-31.27056,-54.95251,-31.32695), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.02,-31.27056,-54.95251,-31.32695), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.02,-31.27056,-54.95251,-31.32695), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.02,-31.27056,-54.95251,-31.32695), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.23583,-31.24528,-56.97583,-34.90723), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.23583,-31.24528,-56.97583,-34.90723), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.23583,-31.24528,-56.97583,-34.90723), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.23583,-31.24528,-56.97583,-34.90723), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.00056,-31.08223,-54.95251,-30.79639), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.00056,-31.08223,-54.95251,-30.79639), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.00056,-31.08223,-54.95251,-30.79639), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.00056,-31.08223,-54.95251,-30.79639), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.8325,-31.07112,-56.97583,-34.78001), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.8325,-31.07112,-56.97583,-34.78001), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.8325,-31.07112,-56.97583,-34.78001), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.8325,-31.07112,-56.97583,-34.78001), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.85167,-31.04639,-56.97583,-34.49473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.85167,-31.04639,-56.97583,-34.49473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.85167,-31.04639,-56.97583,-34.49473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.85167,-31.04639,-56.97583,-34.49473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.41555,-30.97139,-56.97583,-34.80612), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.41555,-30.97139,-56.97583,-34.80612), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.41555,-30.97139,-56.97583,-34.80612), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.41555,-30.97139,-56.97583,-34.80612), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.00695,-30.94445,-54.95251,-30.79639), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.00695,-30.94445,-54.95251,-30.79639), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.00695,-30.94445,-54.95251,-30.79639), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.00695,-30.94445,-54.95251,-30.79639), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.71666,-30.94223,-56.97583,-34.78001), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.71666,-30.94223,-56.97583,-34.78001), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.71666,-30.94223,-56.97583,-34.78001), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.71666,-30.94223,-56.97583,-34.78001), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.64223,-30.94195,-54.95251,-30.84861), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.64223,-30.94195,-54.95251,-30.84861), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.64223,-30.94195,-54.95251,-30.84861), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.64223,-30.94195,-54.95251,-30.84861), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.91029,-30.93417,-54.95251,-30.59223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.91029,-30.93417,-54.95251,-30.59223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.91029,-30.93417,-54.95251,-30.59223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.91029,-30.93417,-54.95251,-30.59223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.81361,-30.91222,-54.95251,-30.73473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.81361,-30.91222,-54.95251,-30.73473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.81361,-30.91222,-54.95251,-30.73473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.81361,-30.91222,-54.95251,-30.73473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.79584,-30.87695,-54.95251,-30.73473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.79584,-30.87695,-54.95251,-30.73473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.79584,-30.87695,-54.95251,-30.73473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.79584,-30.87695,-54.95251,-30.73473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.97666,-30.86028,-54.95251,-31.08223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.97666,-30.86028,-54.95251,-31.08223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.97666,-30.86028,-54.95251,-31.08223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.97666,-30.86028,-54.95251,-31.08223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.57667,-30.84861,-54.95251,-30.84861), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.57667,-30.84861,-54.95251,-30.84861), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.57667,-30.84861,-54.95251,-30.84861), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.57667,-30.84861,-54.95251,-30.84861), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-55.62917,-30.84861,-54.95251,-30.94195), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-55.62917,-30.84861,-54.95251,-30.94195), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-55.62917,-30.84861,-54.95251,-30.94195), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-55.62917,-30.84861,-54.95251,-30.94195), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.00195,-30.79639,-54.95251,-31.08223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.00195,-30.79639,-54.95251,-31.08223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.00195,-30.79639,-54.95251,-31.08223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.00195,-30.79639,-54.95251,-31.08223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.80889,-30.73473,-54.95251,-30.91222), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.80889,-30.73473,-54.95251,-30.91222), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.80889,-30.73473,-54.95251,-30.91222), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.80889,-30.73473,-54.95251,-30.91222), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.16389,-30.64556,-54.95251,-30.52889), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.16389,-30.64556,-54.95251,-30.52889), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.16389,-30.64556,-54.95251,-30.52889), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.16389,-30.64556,-54.95251,-30.52889), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.89,-30.59223,-54.95251,-30.50445), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.89,-30.59223,-54.95251,-30.50445), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.89,-30.59223,-54.95251,-30.50445), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.89,-30.59223,-54.95251,-30.50445), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.27417,-30.52889,-56.97583,-34.91029), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.27417,-30.52889,-56.97583,-34.91029), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.27417,-30.52889,-56.97583,-34.91029), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.27417,-30.52889,-56.97583,-34.91029), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.87472,-30.50445,-54.95251,-30.59223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.87472,-30.50445,-54.95251,-30.59223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.87472,-30.50445,-54.95251,-30.59223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.87472,-30.50445,-54.95251,-30.59223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.75751,-30.42834,-54.95251,-30.87695), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.75751,-30.42834,-54.95251,-30.87695), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.75751,-30.42834,-54.95251,-30.87695), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.75751,-30.42834,-54.95251,-30.87695), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.62527,-30.29473,-54.95251,-30.25223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.62527,-30.29473,-54.95251,-30.25223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.62527,-30.29473,-54.95251,-30.25223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.62527,-30.29473,-54.95251,-30.25223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.21416,-30.29278,-54.95251,-30.24334), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.21416,-30.29278,-54.95251,-30.24334), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.21416,-30.29278,-54.95251,-30.24334), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.21416,-30.29278,-54.95251,-30.24334), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.62611,-30.28584,-54.95251,-30.18268), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.62611,-30.28584,-54.95251,-30.18268), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.62611,-30.28584,-54.95251,-30.18268), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.62611,-30.28584,-54.95251,-30.18268), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.52167,-30.27528,-54.95251,-30.20556), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.52167,-30.27528,-54.95251,-30.20556), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.52167,-30.27528,-54.95251,-30.20556), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.52167,-30.27528,-54.95251,-30.20556), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.56806,-30.25278,-54.95251,-30.20556), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.56806,-30.25278,-54.95251,-30.20556), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.56806,-30.25278,-54.95251,-30.20556), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.56806,-30.25278,-54.95251,-30.20556), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.61945,-30.25223,-54.95251,-30.29473), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.61945,-30.25223,-54.95251,-30.29473), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.61945,-30.25223,-54.95251,-30.29473), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.61945,-30.25223,-54.95251,-30.29473), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.15055,-30.24334,-54.95251,-30.185), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.15055,-30.24334,-54.95251,-30.185), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.15055,-30.24334,-54.95251,-30.185), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.15055,-30.24334,-54.95251,-30.185), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.56695,-30.20556,-54.95251,-30.25278), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.56695,-30.20556,-54.95251,-30.25278), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.56695,-30.20556,-54.95251,-30.25278), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.56695,-30.20556,-54.95251,-30.25278), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.64028,-30.19,-54.95251,-30.28584), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.64028,-30.19,-54.95251,-30.28584), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.64028,-30.19,-54.95251,-30.28584), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.64028,-30.19,-54.95251,-30.28584), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.14555,-30.185,-54.95251,-30.24334), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.14555,-30.185,-54.95251,-30.24334), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.14555,-30.185,-54.95251,-30.24334), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.14555,-30.185,-54.95251,-30.24334), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.61353,-30.18268,-54.95251,-30.28584), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.61353,-30.18268,-54.95251,-30.28584), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.61353,-30.18268,-54.95251,-30.28584), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.61353,-30.18268,-54.95251,-30.28584), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.79528,-30.11278,-56.97583,-34.66361), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.79528,-30.11278,-56.97583,-34.66361), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.79528,-30.11278,-56.97583,-34.66361), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.79528,-30.11278,-56.97583,-34.66361), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-57.07167,-30.10945,-56.97583,-34.46223), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-57.07167,-30.10945,-56.97583,-34.46223), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-57.07167,-30.10945,-56.97583,-34.46223), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-57.07167,-30.10945,-56.97583,-34.46223), mapfile, tile_dir, 17, 17, "uy-uruguay")
	render_tiles((-56.97583,-30.10056,-56.97583,-34.66361), mapfile, tile_dir, 0, 11, "uy-uruguay")
	render_tiles((-56.97583,-30.10056,-56.97583,-34.66361), mapfile, tile_dir, 13, 13, "uy-uruguay")
	render_tiles((-56.97583,-30.10056,-56.97583,-34.66361), mapfile, tile_dir, 15, 15, "uy-uruguay")
	render_tiles((-56.97583,-30.10056,-56.97583,-34.66361), mapfile, tile_dir, 17, 17, "uy-uruguay")