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
    # Region: PA
    # Region Name: Panama

	render_tiles((-81.63057,7.31778,-81.58974,7.33444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.63057,7.31778,-81.58974,7.33444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.58974,7.33444,-81.63057,7.31778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.63612,7.38389,-81.6925,7.41555), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.6925,7.41555,-81.82779,7.41694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.82779,7.41694,-81.6925,7.41555), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.88417,7.51111,-81.67029,7.52667), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.67029,7.52667,-81.88417,7.51111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.73848,7.63995,-81.67029,7.52667), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.87361,7.20583,-80.73779,7.21611), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.73779,7.21611,-80.87361,7.20583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.88913,7.22772,-80.73779,7.21611), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.47029,7.25111,-77.88913,7.22772), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.39612,7.28528,-80.91724,7.28694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.91724,7.28694,-80.39612,7.28528), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.0089,7.33111,-80.88501,7.33861), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.88501,7.33861,-78.0089,7.33111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.35806,7.38194,-80.30501,7.40694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.30501,7.40694,-80.16223,7.41222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.16223,7.41222,-80.30501,7.40694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.07834,7.43472,-80.16223,7.41222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.00029,7.46861,-77.81111,7.48), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.81111,7.48,-77.745,7.4875), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.745,7.4875,-77.81111,7.48), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.17223,7.51,-79.99112,7.52167), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.99112,7.52167,-77.5739,7.52528), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.5739,7.52528,-79.99112,7.52167), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.94556,7.5475,-77.72055,7.54805), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.72055,7.54805,-80.94556,7.5475), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.60638,7.54944,-77.72055,7.54805), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.21251,7.61194,-77.4386,7.62278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.4386,7.62278,-77.75862,7.62555), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.75862,7.62555,-77.4386,7.62278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.235,7.64611,-81.19334,7.65722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.19334,7.65722,-81.04529,7.66389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.04529,7.66389,-81.19334,7.65722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.43195,7.6775,-77.66222,7.67777), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.66222,7.67777,-81.43195,7.6775), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.28723,7.68361,-81.00195,7.68389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.00195,7.68389,-78.28723,7.68361), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.11806,7.70694,-81.52863,7.7075), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.52863,7.7075,-80.11806,7.70694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.755,7.71,-81.52863,7.7075), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.24139,7.71555,-77.32501,7.72083), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.32501,7.72083,-77.73528,7.72416), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.73528,7.72416,-77.32501,7.72083), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.27501,7.73528,-81.49057,7.74416), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.49057,7.74416,-81.17723,7.74611), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.17723,7.74611,-81.49057,7.74416), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.53807,7.75583,-81.08446,7.765), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.08446,7.765,-81.53807,7.75583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.37193,7.78611,-81.58778,7.79222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.58778,7.79222,-81.17111,7.79805), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.17111,7.79805,-81.07362,7.80361), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.07362,7.80361,-81.17111,7.79805), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.09361,7.84,-81.20473,7.86889), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.20473,7.86889,-80.28917,7.87305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.28917,7.87305,-81.20473,7.86889), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.5939,7.88111,-80.28917,7.87305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.05139,7.89111,-81.5939,7.88111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.29195,7.90778,-81.05139,7.89111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.40085,7.94417,-77.14667,7.94583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.14667,7.94583,-78.40085,7.94417), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.3289,7.95528,-81.64445,7.95944), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.64445,7.95944,-80.3289,7.95528), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.38037,8.00007,-82.86418,8.02278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.86418,8.02278,-82.89667,8.02671), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.89667,8.02671,-82.86418,8.02278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.43306,8.05194,-78.33195,8.05861), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.33195,8.05861,-78.43306,8.05194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.68611,8.06666,-78.33195,8.05861), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.48167,8.08167,-81.68611,8.06666), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.41307,8.09778,-78.25917,8.10028), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.25917,8.10028,-77.22166,8.10222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.22166,8.10222,-78.25917,8.10028), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.86862,8.11222,-77.22166,8.10222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.77057,8.12389,-77.73845,8.12893), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.73845,8.12893,-81.77057,8.12389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.235,8.15583,-77.82942,8.16589), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.82942,8.16589,-78.235,8.15583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.82089,8.17727,-81.94307,8.18055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.94307,8.18055,-77.82089,8.17727), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.74306,8.18861,-81.94307,8.18055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.12361,8.20222,-77.86168,8.20972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.86168,8.20972,-82.21223,8.2125), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.21223,8.2125,-82.07584,8.21277), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.07584,8.21277,-82.21223,8.2125), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.4689,8.21833,-82.07584,8.21277), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.86279,8.22944,-77.985,8.23222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.985,8.23222,-77.86279,8.22944), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.86,8.24444,-82.93083,8.25472), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.93083,8.25472,-78.21251,8.26444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.21251,8.26444,-82.93083,8.25472), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.96666,8.27472,-78.27139,8.2775), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.27139,8.2775,-82.45418,8.27972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.45418,8.27972,-78.27139,8.2775), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.40611,8.28472,-77.36389,8.28694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.36389,8.28694,-80.36334,8.28889), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.36334,8.28889,-77.36389,8.28694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.24973,8.29222,-82.57945,8.295), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.57945,8.295,-82.24973,8.29222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.03307,8.31333,-82.71474,8.31722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.71474,8.31722,-78.03307,8.31333), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.21028,8.32638,-82.33778,8.32972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.33778,8.32972,-80.15417,8.33278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.15417,8.33278,-82.33778,8.32972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-83.03168,8.33694,-80.15417,8.33278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.40889,8.34389,-82.43251,8.35055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.43251,8.35055,-78.40889,8.34389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.2189,8.37972,-78.25362,8.39222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.25362,8.39222,-77.37389,8.39639), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.37389,8.39639,-78.35973,8.39778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.35973,8.39778,-77.37389,8.39639), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.14389,8.40166,-78.35973,8.39778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.08974,8.41444,-78.14389,8.40166), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.37001,8.43472,-78.39001,8.43583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.39001,8.43583,-78.37001,8.43472), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.32751,8.44777,-78.25917,8.45055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.25917,8.45055,-78.32751,8.44777), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.9464,8.46139,-78.15224,8.47), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.15224,8.47,-77.42027,8.47055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.42027,8.47055,-78.15224,8.47), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.83057,8.47388,-77.42027,8.47055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.47861,8.47972,-78.11057,8.48305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.11057,8.48305,-78.20445,8.48444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.20445,8.48444,-78.11057,8.48305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.09167,8.51722,-78.49529,8.52194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.49529,8.52194,-78.09167,8.51722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.22278,8.53805,-78.49529,8.52194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.52917,8.56944,-82.82695,8.57583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.82695,8.57583,-78.52917,8.56944), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.74501,8.59222,-82.82695,8.57583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.50362,8.62361,-79.83,8.6325), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.83,8.6325,-78.50362,8.62361), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.44585,8.66083,-78.60861,8.66416), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.60861,8.66416,-77.44585,8.66083), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.36613,8.67617,-79.7489,8.68555), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.7489,8.68555,-77.36613,8.67617), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.65167,8.69944,-77.5314,8.70333), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.5314,8.70333,-78.65167,8.69944), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.91528,8.74527,-77.53195,8.76027), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.53195,8.76027,-78.61446,8.77028), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.61446,8.77028,-77.53195,8.76027), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.88501,8.78472,-81.42834,8.78528), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.42834,8.78528,-82.88501,8.78472), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.1564,8.78778,-81.42834,8.78528), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.75917,8.7925,-81.1564,8.78778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.81723,8.80389,-79.75917,8.7925), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.625,8.82333,-78.85139,8.82639), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.85139,8.82639,-77.625,8.82333), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.56696,8.83722,-79.72723,8.84027), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.72723,8.84027,-81.56696,8.83722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.79723,8.85278,-80.90529,8.86194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.90529,8.86194,-78.79723,8.85278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.60362,8.885,-82.74861,8.88722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.74861,8.88722,-79.60362,8.885), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.80528,8.90389,-78.89751,8.90694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.89751,8.90694,-80.80528,8.90389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.78807,8.92583,-77.73473,8.92778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.73473,8.92778,-81.78807,8.92583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.53519,8.93065,-82.05751,8.93139), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.05751,8.93139,-79.53519,8.93065), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.71916,8.94027,-82.05751,8.93139), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.84973,8.95417,-81.76723,8.96), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.76723,8.96,-79.51918,8.96333), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.51918,8.96333,-81.76723,8.96), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.07362,8.9775,-79.51918,8.96333), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.11279,8.99194,-81.77306,8.99444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.77306,8.99444,-79.11279,8.99194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.25862,9.01055,-79.3739,9.01388), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.3739,9.01388,-82.25862,9.01055), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.71556,9.02083,-79.10307,9.02446), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.10307,9.02446,-81.71556,9.02083), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.645,9.03639,-82.85583,9.04583), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.85583,9.04583,-81.82001,9.05389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.82001,9.05389,-77.86557,9.05972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.86557,9.05972,-82.92947,9.06207), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.92947,9.06207,-77.86557,9.05972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.10022,9.07848,-82.23973,9.08305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.23973,9.08305,-79.11168,9.08389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.11168,9.08389,-82.23973,9.08305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-77.86584,9.105,-82.27528,9.10611), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.27528,9.10611,-77.86584,9.105), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.82001,9.12,-81.93251,9.12166), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.93251,9.12166,-81.82001,9.12), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.40834,9.13278,-81.86195,9.13389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-81.86195,9.13389,-80.40834,9.13278), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.17084,9.14889,-81.86195,9.13389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.24529,9.17028,-82.24945,9.17861), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.24945,9.17861,-82.24529,9.17028), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.28084,9.19194,-82.17279,9.19444), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.17279,9.19444,-82.28084,9.19194), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.14835,9.19694,-82.33974,9.19722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.33974,9.19722,-80.14835,9.19694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.02724,9.22083,-82.33974,9.19722), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.3925,9.26889,-80.03839,9.27464), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-80.03839,9.27464,-82.3925,9.26889), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.26862,9.29611,-82.34167,9.2975), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.34167,9.2975,-78.26862,9.29611), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.94057,9.30305,-82.34167,9.2975), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.9093,9.32803,-79.90056,9.335), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.90056,9.335,-82.38223,9.33944), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.38223,9.33944,-79.90056,9.335), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.96806,9.35305,-79.94334,9.3575), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.94334,9.3575,-79.96806,9.35305), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.82802,9.37415,-79.94334,9.3575), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.93416,9.39833,-78.4789,9.40694), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.4789,9.40694,-82.37695,9.41111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.37695,9.41111,-78.61389,9.41139), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.61389,9.41139,-82.37695,9.41111), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.83974,9.42222,-82.34001,9.42389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.34001,9.42389,-78.83974,9.42222), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.05917,9.43166,-82.34001,9.42389), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.93472,9.47166,-79.69862,9.4825), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.69862,9.4825,-82.61555,9.48972), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.61555,9.48972,-79.69862,9.4825), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.83321,9.49819,-79.06334,9.49833), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.83321,9.49819,-79.06334,9.49833), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.06334,9.49833,-82.83321,9.49819), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-78.97945,9.53361,-79.12361,9.53833), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.12361,9.53833,-78.97945,9.53361), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.27724,9.54944,-82.87532,9.55633), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.87532,9.55633,-82.72749,9.55889), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.72749,9.55889,-82.87532,9.55633), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.47112,9.56778,-82.56519,9.56979), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.56519,9.56979,-79.47112,9.56778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.60529,9.59778,-82.84334,9.60833), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-82.84334,9.60833,-79.60529,9.59778), mapfile, tile_dir, 0, 11, "pa-panama")
	render_tiles((-79.53334,9.62028,-82.84334,9.60833), mapfile, tile_dir, 0, 11, "pa-panama")