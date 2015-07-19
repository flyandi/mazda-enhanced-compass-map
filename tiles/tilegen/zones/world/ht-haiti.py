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
    # Region: HT
    # Region Name: Haiti

	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.81557,18.69888,-73.22473,18.73972), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.80139,18.73972,-73.22473,18.69888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.80139,18.73972,-73.22473,18.69888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.80139,18.73972,-73.22473,18.69888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.80139,18.73972,-73.22473,18.69888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.21834,18.83416,-72.81557,18.96861), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.21834,18.83416,-72.81557,18.96861), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.21834,18.83416,-72.81557,18.96861), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.21834,18.83416,-72.81557,18.96861), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.85751,18.835,-73.22473,18.69888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.85751,18.835,-73.22473,18.69888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.85751,18.835,-73.22473,18.69888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.85751,18.835,-73.22473,18.69888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.29529,18.9325,-72.81557,18.96861), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.29529,18.9325,-72.81557,18.96861), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.29529,18.9325,-72.81557,18.96861), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.29529,18.9325,-72.81557,18.96861), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.22473,18.96861,-72.81557,18.83416), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.22473,18.96861,-72.81557,18.83416), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.22473,18.96861,-72.81557,18.83416), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.22473,18.96861,-72.81557,18.83416), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.88196,18.02277,-72.85057,18.11361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.88196,18.02277,-72.85057,18.11361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.88196,18.02277,-72.85057,18.11361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.88196,18.02277,-72.85057,18.11361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.80334,18.02416,-72.85057,18.04111), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.80334,18.02416,-72.85057,18.04111), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.80334,18.02416,-72.85057,18.04111), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.80334,18.02416,-72.85057,18.04111), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.78917,18.04111,-72.85057,18.02416), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.78917,18.04111,-72.85057,18.02416), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.78917,18.04111,-72.85057,18.02416), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.78917,18.04111,-72.85057,18.02416), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.77148,18.05423,-72.85057,18.20221), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.77148,18.05423,-72.85057,18.20221), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.77148,18.05423,-72.85057,18.20221), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.77148,18.05423,-72.85057,18.20221), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.74889,18.09555,-73.88196,19.70017), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.74889,18.09555,-73.88196,19.70017), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.74889,18.09555,-73.88196,19.70017), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.74889,18.09555,-73.88196,19.70017), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.84167,18.11361,-72.85057,18.14861), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.84167,18.11361,-72.85057,18.14861), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.84167,18.11361,-72.85057,18.14861), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.84167,18.11361,-72.85057,18.14861), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.8125,18.13861,-73.88196,19.04388), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.8125,18.13861,-73.88196,19.04388), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.8125,18.13861,-73.88196,19.04388), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.8125,18.13861,-73.88196,19.04388), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.81946,18.14861,-72.85057,18.02416), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.81946,18.14861,-72.85057,18.02416), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.81946,18.14861,-72.85057,18.02416), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.81946,18.14861,-72.85057,18.02416), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.75111,18.17527,-73.88196,19.37194), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.75111,18.17527,-73.88196,19.37194), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.75111,18.17527,-73.88196,19.37194), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.75111,18.17527,-73.88196,19.37194), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.58778,18.1786,-72.85057,18.78166), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.58778,18.1786,-72.85057,18.78166), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.58778,18.1786,-72.85057,18.78166), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.58778,18.1786,-72.85057,18.78166), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.91695,18.17972,-72.85057,18.44194), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.91695,18.17972,-72.85057,18.44194), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.91695,18.17972,-72.85057,18.44194), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.91695,18.17972,-72.85057,18.44194), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.72084,18.19611,-72.85057,18.54916), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.72084,18.19611,-72.85057,18.54916), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.72084,18.19611,-72.85057,18.54916), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.72084,18.19611,-72.85057,18.54916), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.76917,18.20221,-73.88196,19.33194), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.76917,18.20221,-73.88196,19.33194), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.76917,18.20221,-73.88196,19.33194), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.76917,18.20221,-73.88196,19.33194), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.60278,18.21388,-72.85057,18.58527), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.60278,18.21388,-72.85057,18.58527), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.60278,18.21388,-72.85057,18.58527), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.60278,18.21388,-72.85057,18.58527), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.68973,18.23666,-72.85057,18.54916), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.68973,18.23666,-72.85057,18.54916), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.68973,18.23666,-72.85057,18.54916), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.68973,18.23666,-72.85057,18.54916), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.06946,18.23972,-72.85057,18.60859), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.06946,18.23972,-72.85057,18.60859), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.06946,18.23972,-72.85057,18.60859), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.06946,18.23972,-72.85057,18.60859), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.34584,18.2425,-73.88196,19.63305), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.34584,18.2425,-73.88196,19.63305), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.34584,18.2425,-73.88196,19.63305), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.34584,18.2425,-73.88196,19.63305), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.09807,18.245,-72.85057,18.65833), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.09807,18.245,-72.85057,18.65833), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.09807,18.245,-72.85057,18.65833), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.09807,18.245,-72.85057,18.65833), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.44473,18.25722,-73.88196,19.67916), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.44473,18.25722,-73.88196,19.67916), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.44473,18.25722,-73.88196,19.67916), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.44473,18.25722,-73.88196,19.67916), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.36195,18.28944,-72.85057,18.60777), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.36195,18.28944,-72.85057,18.60777), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.36195,18.28944,-72.85057,18.60777), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.36195,18.28944,-72.85057,18.60777), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.23361,18.30639,-72.85057,18.66611), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.23361,18.30639,-72.85057,18.66611), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.23361,18.30639,-72.85057,18.66611), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.23361,18.30639,-72.85057,18.66611), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.69472,18.32222,-73.88196,19.24166), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.69472,18.32222,-73.88196,19.24166), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.69472,18.32222,-73.88196,19.24166), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.69472,18.32222,-73.88196,19.24166), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.47696,18.4086,-72.85057,18.60777), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.47696,18.4086,-72.85057,18.60777), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.47696,18.4086,-72.85057,18.60777), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.47696,18.4086,-72.85057,18.60777), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.73361,18.425,-73.88196,19.09472), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.73361,18.425,-73.88196,19.09472), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.73361,18.425,-73.88196,19.09472), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.73361,18.425,-73.88196,19.09472), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.89473,18.42888,-73.88196,19.93277), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.89473,18.42888,-73.88196,19.93277), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.89473,18.42888,-73.88196,19.93277), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.89473,18.42888,-73.88196,19.93277), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.77528,18.43361,-73.88196,19.28166), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.77528,18.43361,-73.88196,19.28166), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.77528,18.43361,-73.88196,19.28166), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.77528,18.43361,-73.88196,19.28166), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.91028,18.44194,-73.88196,19.67916), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.91028,18.44194,-73.88196,19.67916), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.91028,18.44194,-73.88196,19.67916), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.91028,18.44194,-73.88196,19.67916), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.88501,18.47721,-72.85057,18.61138), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.88501,18.47721,-72.85057,18.61138), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.88501,18.47721,-72.85057,18.61138), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.88501,18.47721,-72.85057,18.61138), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.65834,18.49777,-72.85057,18.23666), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.65834,18.49777,-72.85057,18.23666), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.65834,18.49777,-72.85057,18.23666), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.65834,18.49777,-72.85057,18.23666), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.42334,18.51694,-73.88196,19.83055), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.42334,18.51694,-73.88196,19.83055), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.42334,18.51694,-73.88196,19.83055), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.42334,18.51694,-73.88196,19.83055), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.36584,18.52555,-72.85057,18.53971), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.36584,18.52555,-72.85057,18.53971), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.36584,18.52555,-72.85057,18.53971), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.36584,18.52555,-72.85057,18.53971), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.75862,18.53916,-72.85057,18.04111), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.75862,18.53916,-72.85057,18.04111), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.75862,18.53916,-72.85057,18.04111), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.75862,18.53916,-72.85057,18.04111), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.34529,18.53971,-72.85057,18.52555), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.34529,18.53971,-72.85057,18.52555), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.34529,18.53971,-72.85057,18.52555), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.34529,18.53971,-72.85057,18.52555), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.94635,18.54246,-72.85057,18.62416), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.94635,18.54246,-72.85057,18.62416), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.94635,18.54246,-72.85057,18.62416), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.94635,18.54246,-72.85057,18.62416), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.59612,18.54861,-72.85057,18.21388), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.59612,18.54861,-72.85057,18.21388), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.59612,18.54861,-72.85057,18.21388), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.59612,18.54861,-72.85057,18.21388), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.7039,18.54916,-72.85057,18.23666), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.7039,18.54916,-72.85057,18.23666), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.7039,18.54916,-72.85057,18.23666), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.7039,18.54916,-72.85057,18.23666), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.63196,18.55222,-72.85057,18.1786), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.63196,18.55222,-72.85057,18.1786), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.63196,18.55222,-72.85057,18.1786), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.63196,18.55222,-72.85057,18.1786), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.41223,18.55722,-73.88196,19.81277), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.41223,18.55722,-73.88196,19.81277), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.41223,18.55722,-73.88196,19.81277), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.41223,18.55722,-73.88196,19.81277), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.57529,18.57583,-72.85057,18.54861), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.57529,18.57583,-72.85057,18.54861), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.57529,18.57583,-72.85057,18.54861), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.57529,18.57583,-72.85057,18.54861), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.60779,18.58527,-72.85057,18.21388), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.60779,18.58527,-72.85057,18.21388), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.60779,18.58527,-72.85057,18.21388), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.60779,18.58527,-72.85057,18.21388), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.42389,18.60777,-72.85057,18.4086), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.42389,18.60777,-72.85057,18.4086), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.42389,18.60777,-72.85057,18.4086), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.42389,18.60777,-72.85057,18.4086), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.99152,18.60859,-73.88196,19.71805), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.99152,18.60859,-73.88196,19.71805), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.99152,18.60859,-73.88196,19.71805), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.99152,18.60859,-73.88196,19.71805), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.88751,18.61138,-72.85057,18.47721), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.88751,18.61138,-72.85057,18.47721), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.88751,18.61138,-72.85057,18.47721), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.88751,18.61138,-72.85057,18.47721), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.97084,18.62416,-73.88196,19.71805), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.97084,18.62416,-73.88196,19.71805), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.97084,18.62416,-73.88196,19.71805), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.97084,18.62416,-73.88196,19.71805), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.14917,18.65833,-72.85057,18.245), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.14917,18.65833,-72.85057,18.245), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.14917,18.65833,-72.85057,18.245), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.14917,18.65833,-72.85057,18.245), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-74.24278,18.66611,-72.85057,18.30639), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-74.24278,18.66611,-72.85057,18.30639), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-74.24278,18.66611,-72.85057,18.30639), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-74.24278,18.66611,-72.85057,18.30639), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.32167,18.66944,-73.88196,19.75222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.32167,18.66944,-73.88196,19.75222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.32167,18.66944,-73.88196,19.75222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.32167,18.66944,-73.88196,19.75222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.72639,18.7161,-73.88196,19.34555), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.72639,18.7161,-73.88196,19.34555), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.72639,18.7161,-73.88196,19.34555), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.72639,18.7161,-73.88196,19.34555), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.54723,18.78166,-72.85057,18.1786), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.54723,18.78166,-72.85057,18.1786), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.54723,18.78166,-72.85057,18.1786), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.54723,18.78166,-72.85057,18.1786), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.71001,18.79889,-73.88196,19.24166), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.71001,18.79889,-73.88196,19.24166), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.71001,18.79889,-73.88196,19.24166), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.71001,18.79889,-73.88196,19.24166), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.77917,18.95777,-72.85057,18.05423), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.77917,18.95777,-72.85057,18.05423), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.77917,18.95777,-72.85057,18.05423), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.77917,18.95777,-72.85057,18.05423), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.85388,18.96888,-73.88196,19.70361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.85388,18.96888,-73.88196,19.70361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.85388,18.96888,-73.88196,19.70361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.85388,18.96888,-73.88196,19.70361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.81834,19.04388,-72.85057,18.13861), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.81834,19.04388,-72.85057,18.13861), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.81834,19.04388,-72.85057,18.13861), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.81834,19.04388,-72.85057,18.13861), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.78889,19.09222,-73.88196,19.21499), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.78889,19.09222,-73.88196,19.21499), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.78889,19.09222,-73.88196,19.21499), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.78889,19.09222,-73.88196,19.21499), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.72473,19.09472,-72.85057,18.425), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.72473,19.09472,-72.85057,18.425), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.72473,19.09472,-72.85057,18.425), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.72473,19.09472,-72.85057,18.425), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.67833,19.09888,-73.88196,19.49833), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.67833,19.09888,-73.88196,19.49833), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.67833,19.09888,-73.88196,19.49833), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.67833,19.09888,-73.88196,19.49833), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.70889,19.12666,-73.88196,19.29222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.70889,19.12666,-73.88196,19.29222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.70889,19.12666,-73.88196,19.29222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.70889,19.12666,-73.88196,19.29222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.79889,19.21499,-73.88196,19.09222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.79889,19.21499,-73.88196,19.09222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.79889,19.21499,-73.88196,19.09222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.79889,19.21499,-73.88196,19.09222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.62944,19.21971,-73.88196,19.09888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.62944,19.21971,-73.88196,19.09888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.62944,19.21971,-73.88196,19.09888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.62944,19.21971,-73.88196,19.09888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.69638,19.24166,-72.85057,18.32222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.69638,19.24166,-72.85057,18.32222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.69638,19.24166,-72.85057,18.32222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.69638,19.24166,-72.85057,18.32222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.77501,19.28166,-72.85057,18.43361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.77501,19.28166,-72.85057,18.43361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.77501,19.28166,-72.85057,18.43361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.77501,19.28166,-72.85057,18.43361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.71417,19.29222,-73.88196,19.12666), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.71417,19.29222,-73.88196,19.12666), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.71417,19.29222,-73.88196,19.12666), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.71417,19.29222,-73.88196,19.12666), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.76695,19.33194,-72.85057,18.20221), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.76695,19.33194,-72.85057,18.20221), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.76695,19.33194,-72.85057,18.20221), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.76695,19.33194,-72.85057,18.20221), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.77057,19.33361,-73.88196,19.28166), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.77057,19.33361,-73.88196,19.28166), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.77057,19.33361,-73.88196,19.28166), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.77057,19.33361,-73.88196,19.28166), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.73972,19.34555,-72.85057,18.09555), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.73972,19.34555,-72.85057,18.09555), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.73972,19.34555,-72.85057,18.09555), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.73972,19.34555,-72.85057,18.09555), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.74695,19.37194,-72.85057,18.17527), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.74695,19.37194,-72.85057,18.17527), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.74695,19.37194,-72.85057,18.17527), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.74695,19.37194,-72.85057,18.17527), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.67834,19.40749,-73.88196,19.45222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.67834,19.40749,-73.88196,19.45222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.67834,19.40749,-73.88196,19.45222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.67834,19.40749,-73.88196,19.45222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.7614,19.44472,-73.88196,19.33361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.7614,19.44472,-73.88196,19.33361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.7614,19.44472,-73.88196,19.33361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.7614,19.44472,-73.88196,19.33361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.69667,19.45222,-73.88196,19.92361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.69667,19.45222,-73.88196,19.92361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.69667,19.45222,-73.88196,19.92361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.69667,19.45222,-73.88196,19.92361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.69221,19.49833,-72.85057,18.32222), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.69221,19.49833,-72.85057,18.32222), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.69221,19.49833,-72.85057,18.32222), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.69221,19.49833,-72.85057,18.32222), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.99278,19.59944,-72.85057,18.42888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.99278,19.59944,-72.85057,18.42888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.99278,19.59944,-72.85057,18.42888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.99278,19.59944,-72.85057,18.42888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.40501,19.63305,-73.88196,19.83055), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.40501,19.63305,-73.88196,19.83055), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.40501,19.63305,-73.88196,19.83055), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.40501,19.63305,-73.88196,19.83055), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.82364,19.65499,-73.88196,19.70361), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.82364,19.65499,-73.88196,19.70361), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.82364,19.65499,-73.88196,19.70361), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.82364,19.65499,-73.88196,19.70361), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.90834,19.67916,-72.85057,18.44194), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.90834,19.67916,-72.85057,18.44194), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.90834,19.67916,-72.85057,18.44194), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.90834,19.67916,-72.85057,18.44194), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.46028,19.67916,-73.88196,19.72499), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.46028,19.67916,-73.88196,19.72499), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.46028,19.67916,-73.88196,19.72499), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.46028,19.67916,-73.88196,19.72499), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.75148,19.70017,-72.85057,18.09555), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.84557,19.70361,-72.85057,18.96888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.84557,19.70361,-72.85057,18.96888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.84557,19.70361,-72.85057,18.96888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.84557,19.70361,-72.85057,18.96888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.86389,19.71416,-72.85057,18.96888), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.86389,19.71416,-72.85057,18.96888), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.86389,19.71416,-72.85057,18.96888), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.86389,19.71416,-72.85057,18.96888), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-71.98918,19.71805,-72.85057,18.60859), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-71.98918,19.71805,-72.85057,18.60859), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-71.98918,19.71805,-72.85057,18.60859), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-71.98918,19.71805,-72.85057,18.60859), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.4639,19.72499,-73.88196,19.67916), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.4639,19.72499,-73.88196,19.67916), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.4639,19.72499,-73.88196,19.67916), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.4639,19.72499,-73.88196,19.67916), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.20946,19.74944,-73.88196,19.7836), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.20946,19.74944,-73.88196,19.7836), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.20946,19.74944,-73.88196,19.7836), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.20946,19.74944,-73.88196,19.7836), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.31529,19.75222,-72.85057,18.66944), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.31529,19.75222,-72.85057,18.66944), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.31529,19.75222,-72.85057,18.66944), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.31529,19.75222,-72.85057,18.66944), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.20361,19.7836,-73.88196,19.74944), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.20361,19.7836,-73.88196,19.74944), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.20361,19.7836,-73.88196,19.74944), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.20361,19.7836,-73.88196,19.74944), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.3914,19.81277,-72.85057,18.55722), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.3914,19.81277,-72.85057,18.55722), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.3914,19.81277,-72.85057,18.55722), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.3914,19.81277,-72.85057,18.55722), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.40779,19.83055,-73.88196,19.63305), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.40779,19.83055,-73.88196,19.63305), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.40779,19.83055,-73.88196,19.63305), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.40779,19.83055,-73.88196,19.63305), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.20473,19.89527,-73.88196,19.92055), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.20473,19.89527,-73.88196,19.92055), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.20473,19.89527,-73.88196,19.92055), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.20473,19.89527,-73.88196,19.92055), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-73.11446,19.92055,-73.88196,19.89527), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-73.11446,19.92055,-73.88196,19.89527), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-73.11446,19.92055,-73.88196,19.89527), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-73.11446,19.92055,-73.88196,19.89527), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.70306,19.92361,-73.88196,19.12666), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.70306,19.92361,-73.88196,19.12666), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.70306,19.92361,-73.88196,19.12666), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.70306,19.92361,-73.88196,19.12666), mapfile, tile_dir, 17, 17, "ht-haiti")
	render_tiles((-72.85057,19.93277,-73.88196,19.04388), mapfile, tile_dir, 0, 11, "ht-haiti")
	render_tiles((-72.85057,19.93277,-73.88196,19.04388), mapfile, tile_dir, 13, 13, "ht-haiti")
	render_tiles((-72.85057,19.93277,-73.88196,19.04388), mapfile, tile_dir, 15, 15, "ht-haiti")
	render_tiles((-72.85057,19.93277,-73.88196,19.04388), mapfile, tile_dir, 17, 17, "ht-haiti")