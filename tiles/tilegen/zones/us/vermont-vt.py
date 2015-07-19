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
    # Zone: us
    # Region: Vermont
    # Region Name: VT

	render_tiles((-72.45852,42.72685,-73.04839,43.04421), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.45852,42.72685,-73.04839,43.04421), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.45852,42.72685,-73.04839,43.04421), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.45852,42.72685,-73.04839,43.04421), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.51675,42.72847,-73.04839,42.89795), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.51675,42.72847,-73.04839,42.89795), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.51675,42.72847,-73.04839,42.89795), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.51675,42.72847,-73.04839,42.89795), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.80911,42.73658,-73.04839,42.73771), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.80911,42.73658,-73.04839,42.73771), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.80911,42.73658,-73.04839,42.73771), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.80911,42.73658,-73.04839,42.73771), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.86429,42.73771,-73.04839,42.73658), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.86429,42.73771,-73.04839,42.73658), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.86429,42.73771,-73.04839,42.73658), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.86429,42.73771,-73.04839,42.73658), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.93026,42.73907,-72.45852,45.01427), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.93026,42.73907,-72.45852,45.01427), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.93026,42.73907,-72.45852,45.01427), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.93026,42.73907,-72.45852,45.01427), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.01865,42.74088,-73.04839,42.74097), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.01865,42.74088,-73.04839,42.74097), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.01865,42.74088,-73.04839,42.74097), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.01865,42.74088,-73.04839,42.74097), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.02291,42.74097,-73.04839,42.74088), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.02291,42.74097,-73.04839,42.74088), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.02291,42.74097,-73.04839,42.74088), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.02291,42.74097,-73.04839,42.74088), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.14249,42.74343,-72.45852,45.01316), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.14249,42.74343,-72.45852,45.01316), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.14249,42.74343,-72.45852,45.01316), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.14249,42.74343,-72.45852,45.01316), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.26496,42.74594,-73.04839,43.03592), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.26496,42.74594,-73.04839,43.03592), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.26496,42.74594,-73.04839,43.03592), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.26496,42.74594,-73.04839,43.03592), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.47762,42.76125,-73.04839,42.96765), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.47762,42.76125,-73.04839,42.96765), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.47762,42.76125,-73.04839,42.96765), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.47762,42.76125,-73.04839,42.96765), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.29094,42.80192,-73.04839,43.58451), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.29094,42.80192,-73.04839,43.58451), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.29094,42.80192,-73.04839,43.58451), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.29094,42.80192,-73.04839,43.58451), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.5396,42.80483,-72.45852,45.00786), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.5396,42.80483,-72.45852,45.00786), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.5396,42.80483,-72.45852,45.00786), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.5396,42.80483,-72.45852,45.00786), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.27867,42.83341,-73.04839,42.94363), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.27867,42.83341,-73.04839,42.94363), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.27867,42.83341,-73.04839,42.94363), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.27867,42.83341,-73.04839,42.94363), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.55392,42.85809,-72.45852,45.00947), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.55392,42.85809,-72.45852,45.00947), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.55392,42.85809,-72.45852,45.00947), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.55392,42.85809,-72.45852,45.00947), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.55611,42.86625,-72.45852,45.00947), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.55611,42.86625,-72.45852,45.00947), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.55611,42.86625,-72.45852,45.00947), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.55611,42.86625,-72.45852,45.00947), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.53147,42.89795,-73.04839,42.95495), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.53147,42.89795,-73.04839,42.95495), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.53147,42.89795,-73.04839,42.95495), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.53147,42.89795,-73.04839,42.95495), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.27383,42.94363,-73.04839,43.03071), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.27383,42.94363,-73.04839,43.03071), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.27383,42.94363,-73.04839,43.03071), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.27383,42.94363,-73.04839,43.03071), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.53219,42.95495,-72.45852,45.00786), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.53219,42.95495,-72.45852,45.00786), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.53219,42.95495,-72.45852,45.00786), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.53219,42.95495,-72.45852,45.00786), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.4926,42.96765,-73.04839,42.76125), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.4926,42.96765,-73.04839,42.76125), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.4926,42.96765,-73.04839,42.76125), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.4926,42.96765,-73.04839,42.76125), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.44498,43.00442,-73.04839,43.21525), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.44498,43.00442,-73.04839,43.21525), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.44498,43.00442,-73.04839,43.21525), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.44498,43.00442,-73.04839,43.21525), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.45196,43.02052,-73.04839,43.15349), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.45196,43.02052,-73.04839,43.15349), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.45196,43.02052,-73.04839,43.15349), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.45196,43.02052,-73.04839,43.15349), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.27001,43.03071,-73.04839,43.03592), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.27001,43.03071,-73.04839,43.03592), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.27001,43.03071,-73.04839,43.03592), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.27001,43.03071,-73.04839,43.03592), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.26978,43.03592,-73.04839,43.03071), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.26978,43.03592,-73.04839,43.03071), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.26978,43.03592,-73.04839,43.03071), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.26978,43.03592,-73.04839,43.03071), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.46225,43.04421,-73.04839,42.72685), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.46225,43.04421,-73.04839,42.72685), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.46225,43.04421,-73.04839,42.72685), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.46225,43.04421,-73.04839,42.72685), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.43519,43.08662,-73.04839,43.23279), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.43519,43.08662,-73.04839,43.23279), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.43519,43.08662,-73.04839,43.23279), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.43519,43.08662,-73.04839,43.23279), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.4518,43.15349,-73.04839,43.02052), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.4518,43.15349,-73.04839,43.02052), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.4518,43.15349,-73.04839,43.02052), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.4518,43.15349,-73.04839,43.02052), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.45038,43.16127,-73.04839,43.15349), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.45038,43.16127,-73.04839,43.15349), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.45038,43.16127,-73.04839,43.15349), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.45038,43.16127,-73.04839,43.15349), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.44056,43.21525,-73.04839,43.00442), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.44056,43.21525,-73.04839,43.00442), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.44056,43.21525,-73.04839,43.00442), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.44056,43.21525,-73.04839,43.00442), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.43366,43.23279,-73.04839,43.08662), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.43366,43.23279,-73.04839,43.08662), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.43366,43.23279,-73.04839,43.08662), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.43366,43.23279,-73.04839,43.08662), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.42158,43.26344,-73.04839,43.36274), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.42158,43.26344,-73.04839,43.36274), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.42158,43.26344,-73.04839,43.36274), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.42158,43.26344,-73.04839,43.36274), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.25536,43.31471,-73.04839,43.36349), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.25536,43.31471,-73.04839,43.36349), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.25536,43.31471,-73.04839,43.36349), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.25536,43.31471,-73.04839,43.36349), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.40253,43.32038,-73.04839,43.42892), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.40253,43.32038,-73.04839,43.42892), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.40253,43.32038,-73.04839,43.42892), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.40253,43.32038,-73.04839,43.42892), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.41338,43.36274,-73.04839,43.26344), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.41338,43.36274,-73.04839,43.26344), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.41338,43.36274,-73.04839,43.26344), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.41338,43.36274,-73.04839,43.26344), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.25283,43.36349,-73.04839,43.31471), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.25283,43.36349,-73.04839,43.31471), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.25283,43.36349,-73.04839,43.31471), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.25283,43.36349,-73.04839,43.31471), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.39692,43.42892,-73.04839,43.429), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.39692,43.42892,-73.04839,43.429), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.39692,43.42892,-73.04839,43.429), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.39692,43.42892,-73.04839,43.429), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.3969,43.429,-73.04839,43.42892), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.3969,43.429,-73.04839,43.42892), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.3969,43.429,-73.04839,43.42892), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.3969,43.429,-73.04839,43.42892), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.38089,43.49339,-73.04839,43.57407), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.38089,43.49339,-73.04839,43.57407), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.38089,43.49339,-73.04839,43.57407), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.38089,43.49339,-73.04839,43.57407), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.24204,43.53493,-73.04839,43.36349), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.24204,43.53493,-73.04839,43.36349), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.24204,43.53493,-73.04839,43.36349), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.24204,43.53493,-73.04839,43.36349), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.39577,43.56809,-72.45852,44.1669), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.39577,43.56809,-72.45852,44.1669), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.39577,43.56809,-72.45852,44.1669), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.39577,43.56809,-72.45852,44.1669), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.37944,43.57407,-73.04839,43.49339), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.37944,43.57407,-73.04839,43.49339), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.37944,43.57407,-73.04839,43.49339), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.37944,43.57407,-73.04839,43.49339), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.29211,43.58451,-73.04839,42.80192), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.29211,43.58451,-73.04839,42.80192), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.29211,43.58451,-73.04839,42.80192), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.29211,43.58451,-73.04839,42.80192), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.42498,43.59878,-72.45852,44.09942), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.42498,43.59878,-72.45852,44.09942), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.42498,43.59878,-72.45852,44.09942), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.42498,43.59878,-72.45852,44.09942), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.33341,43.60572,-73.04839,43.60839), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.33341,43.60572,-73.04839,43.60839), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.33341,43.60572,-73.04839,43.60839), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.33341,43.60572,-73.04839,43.60839), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.32952,43.60839,-73.04839,43.60572), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.32952,43.60839,-73.04839,43.60572), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.32952,43.60839,-73.04839,43.60572), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.32952,43.60839,-73.04839,43.60572), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.3277,43.62591,-72.45852,44.31002), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.3277,43.62591,-72.45852,44.31002), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.3277,43.62591,-72.45852,44.31002), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.3277,43.62591,-72.45852,44.31002), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.41455,43.65821,-72.45852,44.09942), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.41455,43.65821,-72.45852,44.09942), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.41455,43.65821,-72.45852,44.09942), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.41455,43.65821,-72.45852,44.09942), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.39372,43.6992,-72.45852,44.1669), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.39372,43.6992,-72.45852,44.1669), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.39372,43.6992,-72.45852,44.1669), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.39372,43.6992,-72.45852,44.1669), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.28481,43.72036,-73.04839,43.60839), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.28481,43.72036,-73.04839,43.60839), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.28481,43.72036,-73.04839,43.60839), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.28481,43.72036,-73.04839,43.60839), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.36111,43.75323,-72.45852,44.56246), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.36111,43.75323,-72.45852,44.56246), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.36111,43.75323,-72.45852,44.56246), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.36111,43.75323,-72.45852,44.56246), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.22207,43.75983,-73.04839,43.77302), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.22207,43.75983,-73.04839,43.77302), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.22207,43.75983,-73.04839,43.77302), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.22207,43.75983,-73.04839,43.77302), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.35071,43.77046,-72.45852,44.23036), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.35071,43.77046,-72.45852,44.23036), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.35071,43.77046,-72.45852,44.23036), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.35071,43.77046,-72.45852,44.23036), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.2115,43.77302,-73.04839,43.75983), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.2115,43.77302,-73.04839,43.75983), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.2115,43.77302,-73.04839,43.75983), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.2115,43.77302,-73.04839,43.75983), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.38253,43.80816,-72.45852,44.85704), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.38253,43.80816,-72.45852,44.85704), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.38253,43.80816,-72.45852,44.85704), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.38253,43.80816,-72.45852,44.85704), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.18333,43.80818,-72.45852,43.87343), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.18333,43.80818,-72.45852,43.87343), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.18333,43.80818,-72.45852,43.87343), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.18333,43.80818,-72.45852,43.87343), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.3903,43.81737,-72.45852,44.61962), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.3903,43.81737,-72.45852,44.61962), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.3903,43.81737,-72.45852,44.61962), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.3903,43.81737,-72.45852,44.61962), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.16978,43.87343,-73.04839,43.80818), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.16978,43.87343,-73.04839,43.80818), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.16978,43.87343,-73.04839,43.80818), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.16978,43.87343,-73.04839,43.80818), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.37405,43.87556,-72.45852,44.85704), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.37405,43.87556,-72.45852,44.85704), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.37405,43.87556,-72.45852,44.85704), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.37405,43.87556,-72.45852,44.85704), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.40774,43.92989,-72.45852,44.01149), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.40774,43.92989,-72.45852,44.01149), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.40774,43.92989,-72.45852,44.01149), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.40774,43.92989,-72.45852,44.01149), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.10588,43.94937,-72.45852,43.99195), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.10588,43.94937,-72.45852,43.99195), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.10588,43.94937,-72.45852,43.99195), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.10588,43.94937,-72.45852,43.99195), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.41125,43.9756,-73.04839,43.65821), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.41125,43.9756,-73.04839,43.65821), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.41125,43.9756,-73.04839,43.65821), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.41125,43.9756,-73.04839,43.65821), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.11671,43.99195,-72.45852,43.94937), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.11671,43.99195,-72.45852,43.94937), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.11671,43.99195,-72.45852,43.94937), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.11671,43.99195,-72.45852,43.94937), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.40598,44.01149,-72.45852,43.92989), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.40598,44.01149,-72.45852,43.92989), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.40598,44.01149,-72.45852,43.92989), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.40598,44.01149,-72.45852,43.92989), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.07994,44.03,-72.45852,44.0304), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.07994,44.03,-72.45852,44.0304), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.07994,44.03,-72.45852,44.0304), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.07994,44.03,-72.45852,44.0304), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.07955,44.0304,-72.45852,44.03), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.07955,44.0304,-72.45852,44.03), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.07955,44.0304,-72.45852,44.03), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.07955,44.0304,-72.45852,44.03), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.07549,44.03461,-72.45852,44.0304), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.07549,44.03461,-72.45852,44.0304), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.07549,44.03461,-72.45852,44.0304), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.07549,44.03461,-72.45852,44.0304), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.43688,44.04258,-73.04839,43.59878), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.43688,44.04258,-73.04839,43.59878), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.43688,44.04258,-73.04839,43.59878), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.43688,44.04258,-73.04839,43.59878), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.41632,44.09942,-73.04839,43.65821), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.03688,44.10312,-72.45852,44.29198), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.03688,44.10312,-72.45852,44.29198), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.03688,44.10312,-72.45852,44.29198), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.03688,44.10312,-72.45852,44.29198), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.39987,44.15249,-73.04839,43.56809), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.39987,44.15249,-73.04839,43.56809), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.39987,44.15249,-73.04839,43.56809), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.39987,44.15249,-73.04839,43.56809), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.05383,44.15982,-72.45852,44.24693), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.05383,44.15982,-72.45852,44.24693), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.05383,44.15982,-72.45852,44.24693), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.05383,44.15982,-72.45852,44.24693), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.3954,44.1669,-73.04839,43.56809), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.3954,44.1669,-73.04839,43.56809), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.3954,44.1669,-73.04839,43.56809), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.3954,44.1669,-73.04839,43.56809), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.06134,44.18495,-72.45852,44.24693), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.06134,44.18495,-72.45852,44.24693), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.06134,44.18495,-72.45852,44.24693), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.06134,44.18495,-72.45852,44.24693), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.34989,44.23036,-73.04839,43.77046), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.34989,44.23036,-73.04839,43.77046), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.34989,44.23036,-73.04839,43.77046), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.34989,44.23036,-73.04839,43.77046), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.05399,44.24693,-72.45852,44.15982), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.05399,44.24693,-72.45852,44.15982), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.05399,44.24693,-72.45852,44.15982), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.05399,44.24693,-72.45852,44.15982), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.31662,44.25777,-72.45852,44.26352), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.31662,44.25777,-72.45852,44.26352), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.31662,44.25777,-72.45852,44.26352), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.31662,44.25777,-72.45852,44.26352), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.31746,44.26352,-72.45852,44.25777), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.31746,44.26352,-72.45852,44.25777), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.31746,44.26352,-72.45852,44.25777), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.31746,44.26352,-72.45852,44.25777), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.0463,44.29198,-72.45852,44.15982), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.0463,44.29198,-72.45852,44.15982), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.0463,44.29198,-72.45852,44.15982), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.0463,44.29198,-72.45852,44.15982), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.32423,44.31002,-72.45852,44.38267), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.32423,44.31002,-72.45852,44.38267), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.32423,44.31002,-72.45852,44.38267), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.32423,44.31002,-72.45852,44.38267), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.00231,44.32487,-72.45852,45.00679), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.00231,44.32487,-72.45852,45.00679), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.00231,44.32487,-72.45852,45.00679), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.00231,44.32487,-72.45852,45.00679), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.87586,44.33737,-72.45852,45.00807), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.87586,44.33737,-72.45852,45.00807), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.87586,44.33737,-72.45852,45.00807), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.87586,44.33737,-72.45852,45.00807), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.94516,44.33774,-72.45852,45.00779), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.94516,44.33774,-72.45852,45.00779), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.94516,44.33774,-72.45852,45.00779), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.94516,44.33774,-72.45852,45.00779), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.83766,44.3478,-72.45852,44.35294), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.83766,44.3478,-72.45852,44.35294), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.83766,44.3478,-72.45852,44.35294), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.83766,44.3478,-72.45852,44.35294), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.81884,44.35294,-72.45852,44.3478), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.81884,44.35294,-72.45852,44.3478), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.81884,44.35294,-72.45852,44.3478), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.81884,44.35294,-72.45852,44.3478), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.33464,44.35688,-72.45852,44.80219), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.33464,44.35688,-72.45852,44.80219), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.33464,44.35688,-72.45852,44.80219), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.33464,44.35688,-72.45852,44.80219), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.32095,44.38267,-72.45852,44.31002), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.32095,44.38267,-72.45852,44.31002), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.32095,44.38267,-72.45852,44.31002), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.32095,44.38267,-72.45852,44.31002), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.77861,44.3998,-72.45852,44.40381), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.77861,44.3998,-72.45852,44.40381), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.77861,44.3998,-72.45852,44.40381), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.77861,44.3998,-72.45852,44.40381), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.76221,44.40381,-72.45852,44.3998), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.76221,44.40381,-72.45852,44.3998), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.76221,44.40381,-72.45852,44.3998), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.76221,44.40381,-72.45852,44.3998), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.69092,44.42123,-72.45852,45.01142), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.69092,44.42123,-72.45852,45.01142), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.69092,44.42123,-72.45852,45.01142), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.69092,44.42123,-72.45852,45.01142), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.29361,44.44056,-73.04839,43.58451), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.29361,44.44056,-73.04839,43.58451), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.29361,44.44056,-73.04839,43.58451), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.29361,44.44056,-73.04839,43.58451), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.64655,44.46887,-72.45852,44.74722), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.64655,44.46887,-72.45852,44.74722), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.64655,44.46887,-72.45852,44.74722), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.64655,44.46887,-72.45852,44.74722), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.57997,44.50178,-72.45852,44.66535), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.57997,44.50178,-72.45852,44.66535), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.57997,44.50178,-72.45852,44.66535), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.57997,44.50178,-72.45852,44.66535), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.31287,44.50725,-72.45852,44.25777), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.31287,44.50725,-72.45852,44.25777), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.31287,44.50725,-72.45852,44.25777), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.31287,44.50725,-72.45852,44.25777), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.34798,44.54616,-72.45852,44.23036), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.34798,44.54616,-72.45852,44.23036), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.34798,44.54616,-72.45852,44.23036), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.34798,44.54616,-72.45852,44.23036), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.58808,44.54785,-72.45852,44.66535), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.58808,44.54785,-72.45852,44.66535), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.58808,44.54785,-72.45852,44.66535), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.58808,44.54785,-72.45852,44.66535), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.36268,44.56246,-73.04839,43.75323), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.36268,44.56246,-73.04839,43.75323), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.36268,44.56246,-73.04839,43.75323), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.36268,44.56246,-73.04839,43.75323), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.36728,44.56755,-72.45852,44.82645), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.36728,44.56755,-72.45852,44.82645), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.36728,44.56755,-72.45852,44.82645), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.36728,44.56755,-72.45852,44.82645), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.54492,44.57928,-72.45852,44.6276), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.54492,44.57928,-72.45852,44.6276), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.54492,44.57928,-72.45852,44.6276), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.54492,44.57928,-72.45852,44.6276), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.38997,44.61962,-73.04839,43.81737), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.38997,44.61962,-73.04839,43.81737), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.38997,44.61962,-73.04839,43.81737), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.38997,44.61962,-73.04839,43.81737), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.55172,44.6276,-72.45852,44.57928), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.55172,44.6276,-72.45852,44.57928), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.55172,44.6276,-72.45852,44.57928), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.55172,44.6276,-72.45852,44.57928), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.58457,44.66535,-72.45852,44.54785), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.58457,44.66535,-72.45852,44.54785), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.58457,44.66535,-72.45852,44.54785), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.58457,44.66535,-72.45852,44.54785), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.36556,44.7003,-72.45852,44.82645), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.36556,44.7003,-72.45852,44.82645), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.36556,44.7003,-72.45852,44.82645), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.36556,44.7003,-72.45852,44.82645), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.62691,44.74722,-72.45852,45.01271), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.62691,44.74722,-72.45852,45.01271), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.62691,44.74722,-72.45852,45.01271), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.62691,44.74722,-72.45852,45.01271), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.35767,44.75102,-73.04839,43.75323), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.35767,44.75102,-73.04839,43.75323), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.35767,44.75102,-73.04839,43.75323), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.35767,44.75102,-73.04839,43.75323), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.33443,44.80219,-72.45852,44.35688), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.33443,44.80219,-72.45852,44.35688), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.33443,44.80219,-72.45852,44.35688), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.33443,44.80219,-72.45852,44.35688), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.5704,44.80528,-72.45852,44.50178), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.5704,44.80528,-72.45852,44.50178), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.5704,44.80528,-72.45852,44.50178), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.5704,44.80528,-72.45852,44.50178), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.34201,44.80808,-72.45852,45.01084), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.34201,44.80808,-72.45852,45.01084), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.34201,44.80808,-72.45852,45.01084), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.34201,44.80808,-72.45852,45.01084), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.36568,44.82645,-72.45852,44.7003), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.36568,44.82645,-72.45852,44.7003), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.36568,44.82645,-72.45852,44.7003), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.36568,44.82645,-72.45852,44.7003), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.37982,44.85704,-73.04839,43.80816), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.37982,44.85704,-73.04839,43.80816), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.37982,44.85704,-73.04839,43.80816), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.37982,44.85704,-73.04839,43.80816), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.52239,44.88081,-72.45852,44.97602), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.52239,44.88081,-72.45852,44.97602), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.52239,44.88081,-72.45852,44.97602), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.52239,44.88081,-72.45852,44.97602), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.4944,44.91184,-72.45852,45.01338), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.4944,44.91184,-72.45852,45.01338), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.4944,44.91184,-72.45852,45.01338), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.4944,44.91184,-72.45852,45.01338), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.33898,44.91768,-72.45852,44.80808), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.33898,44.91768,-72.45852,44.80808), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.33898,44.91768,-72.45852,44.80808), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.33898,44.91768,-72.45852,44.80808), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.34474,44.97047,-72.45852,45.01084), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.34474,44.97047,-72.45852,45.01084), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.34474,44.97047,-72.45852,45.01084), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.34474,44.97047,-72.45852,45.01084), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.53161,44.97602,-72.45852,44.88081), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.53161,44.97602,-72.45852,44.88081), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.53161,44.97602,-72.45852,44.88081), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.53161,44.97602,-72.45852,44.88081), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.34858,45.00563,-73.04839,43.60572), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.34858,45.00563,-73.04839,43.60572), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.34858,45.00563,-73.04839,43.60572), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.34858,45.00563,-73.04839,43.60572), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.02329,45.00679,-72.45852,44.10312), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.02329,45.00679,-72.45852,44.10312), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.02329,45.00679,-72.45852,44.10312), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.02329,45.00679,-72.45852,44.10312), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.91501,45.00779,-72.45852,45.00807), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.91501,45.00779,-72.45852,45.00807), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.91501,45.00779,-72.45852,45.00807), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.91501,45.00779,-72.45852,45.00807), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.5325,45.00786,-73.04839,42.95495), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.5325,45.00786,-73.04839,42.95495), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.5325,45.00786,-73.04839,42.95495), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.5325,45.00786,-73.04839,42.95495), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.89771,45.00807,-72.45852,45.00779), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.89771,45.00807,-72.45852,45.00779), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.89771,45.00807,-72.45852,45.00779), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.89771,45.00807,-72.45852,45.00779), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.55427,45.00947,-73.04839,42.85809), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.55427,45.00947,-73.04839,42.85809), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.55427,45.00947,-73.04839,42.85809), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.55427,45.00947,-73.04839,42.85809), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.34312,45.01084,-72.45852,44.80808), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.34312,45.01084,-72.45852,44.80808), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.34312,45.01084,-72.45852,44.80808), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.34312,45.01084,-72.45852,44.80808), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.6919,45.01142,-72.45852,44.42123), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.6919,45.01142,-72.45852,44.42123), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.6919,45.01142,-72.45852,44.42123), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.6919,45.01142,-72.45852,44.42123), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.58237,45.01154,-73.04839,42.86625), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.58237,45.01154,-73.04839,42.86625), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.58237,45.01154,-73.04839,42.86625), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.58237,45.01154,-73.04839,42.86625), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.60984,45.01271,-72.45852,44.74722), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.60984,45.01271,-72.45852,44.74722), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.60984,45.01271,-72.45852,44.74722), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.60984,45.01271,-72.45852,44.74722), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.19231,45.01286,-72.45852,45.01316), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.19231,45.01286,-72.45852,45.01316), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.19231,45.01286,-72.45852,45.01316), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.19231,45.01286,-72.45852,45.01316), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.16992,45.01316,-72.45852,45.01286), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.16992,45.01316,-72.45852,45.01286), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.16992,45.01316,-72.45852,45.01286), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.16992,45.01316,-72.45852,45.01286), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-71.50109,45.01338,-72.45852,44.91184), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-71.50109,45.01338,-72.45852,44.91184), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-71.50109,45.01338,-72.45852,44.91184), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-71.50109,45.01338,-72.45852,44.91184), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-72.93644,45.01427,-73.04839,42.73907), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-72.93644,45.01427,-73.04839,42.73907), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-72.93644,45.01427,-73.04839,42.73907), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-72.93644,45.01427,-73.04839,42.73907), mapfile, tile_dir, 17, 17, "vermont-vt")
	render_tiles((-73.04839,45.01479,-73.04839,42.74097), mapfile, tile_dir, 0, 11, "vermont-vt")
	render_tiles((-73.04839,45.01479,-73.04839,42.74097), mapfile, tile_dir, 13, 13, "vermont-vt")
	render_tiles((-73.04839,45.01479,-73.04839,42.74097), mapfile, tile_dir, 15, 15, "vermont-vt")
	render_tiles((-73.04839,45.01479,-73.04839,42.74097), mapfile, tile_dir, 17, 17, "vermont-vt")