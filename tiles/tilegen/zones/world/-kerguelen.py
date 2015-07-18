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
    # Region: 
    # Region Name: Kerguelen

	render_tiles((68.81693,-49.72056,70.07776,-49.71946), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.81693,-49.72056,70.07776,-49.71946), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.07776,-49.71946,68.81693,-49.72056), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.87943,-49.70222,70.25887,-49.6875), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.25887,-49.6875,69.08275,-49.68723), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.08275,-49.68723,70.25887,-49.6875), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.76053,-49.68446,69.08275,-49.68723), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.66025,-49.66806,68.76053,-49.68446), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.71553,-49.65056,70.27248,-49.64696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.27248,-49.64696,69.11081,-49.64362), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.11081,-49.64362,70.27248,-49.64696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.15248,-49.62946,69.44582,-49.62556), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.44582,-49.62556,69.15248,-49.62946), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.11887,-49.61917,70.1622,-49.61696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.1622,-49.61696,70.11887,-49.61917), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.30942,-49.60917,70.1622,-49.61696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.1147,-49.59724,69.77914,-49.59584), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.77914,-49.59584,70.1147,-49.59724), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.10442,-49.59334,69.77914,-49.59584), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.7722,-49.59334,69.77914,-49.59584), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.72108,-49.58112,69.21136,-49.57696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.21136,-49.57696,69.72108,-49.58112), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.06886,-49.56834,69.36693,-49.56696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.36693,-49.56696,69.27721,-49.5664), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.27721,-49.5664,69.36693,-49.56696), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.04053,-49.56445,69.27721,-49.5664), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.22971,-49.56001,70.04053,-49.56445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.33636,-49.55306,69.22971,-49.56001), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.09886,-49.5425,69.07581,-49.54112), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.07581,-49.54112,69.81636,-49.54029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.81636,-49.54029,69.07581,-49.54112), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.31636,-49.53862,69.81636,-49.54029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.03525,-49.53362,69.78693,-49.53334), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.78693,-49.53334,70.03525,-49.53362), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.19525,-49.52751,69.78693,-49.53334), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.14359,-49.51834,68.83942,-49.51306), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.83942,-49.51306,69.63387,-49.5089), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.63387,-49.5089,70.14304,-49.50751), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.14304,-49.50751,69.63387,-49.5089), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.24858,-49.50306,70.14304,-49.50751), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.70581,-49.49557,69.20747,-49.48862), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.20747,-49.48862,68.8147,-49.48807), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.8147,-49.48807,69.20747,-49.48862), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.89914,-49.44585,70.45914,-49.44417), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.45914,-49.44417,69.89914,-49.44585), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.27887,-49.43723,69.94247,-49.4314), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.94247,-49.4314,70.27887,-49.43723), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.45387,-49.42445,69.79053,-49.42418), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.79053,-49.42418,70.45387,-49.42445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.37248,-49.41445,70.29553,-49.40918), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.29553,-49.40918,69.69247,-49.4075), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.69247,-49.4075,70.29553,-49.40918), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.90747,-49.39918,69.78442,-49.39473), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.78442,-49.39473,68.84859,-49.39029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.84859,-49.39029,69.78442,-49.39473), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.30692,-49.38223,68.84859,-49.39029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.44275,-49.37307,68.80914,-49.36668), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.80914,-49.36668,70.44275,-49.37307), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.47636,-49.35362,70.21414,-49.34695), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.21414,-49.34695,69.82776,-49.34084), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.82776,-49.34084,68.82137,-49.34), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.82137,-49.34,69.82776,-49.34084), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.35942,-49.33835,68.82137,-49.34), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.92276,-49.32446,68.86081,-49.31918), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.86081,-49.31918,68.92276,-49.32446), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.7047,-49.31167,69.58275,-49.30445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.58275,-49.30445,69.7047,-49.31167), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.81192,-49.28917,68.91386,-49.28806), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.91386,-49.28806,68.81192,-49.28917), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.63748,-49.28806,68.81192,-49.28917), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.4072,-49.2764,68.85109,-49.2739), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.85109,-49.2739,69.4072,-49.2764), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.53497,-49.24334,68.77414,-49.24279), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.77414,-49.24279,69.53497,-49.24334), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.56859,-49.23251,68.77414,-49.24279), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.42914,-49.20918,69.28247,-49.18639), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.28247,-49.18639,69.34554,-49.1814), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.34554,-49.1814,68.75859,-49.18085), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.75859,-49.18085,69.34554,-49.1814), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.20497,-49.16361,68.91331,-49.15974), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.91331,-49.15974,70.20497,-49.16361), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.47054,-49.15251,68.81192,-49.15001), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.81192,-49.15001,69.47054,-49.15251), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.40164,-49.13556,69.64081,-49.12418), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.64081,-49.12418,70.07887,-49.12001), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.07887,-49.12001,69.07053,-49.11917), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.07053,-49.11917,70.07887,-49.12001), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.35359,-49.11751,69.07053,-49.11917), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.84192,-49.11584,69.35359,-49.11751), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.57887,-49.11362,68.84192,-49.11584), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.16914,-49.10889,69.27386,-49.10834), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.27386,-49.10834,69.16914,-49.10889), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.53053,-49.0989,69.27386,-49.10834), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.4397,-49.08723,68.99969,-49.08446), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.99969,-49.08446,68.75247,-49.08334), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.75247,-49.08334,68.99969,-49.08446), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.47859,-49.07557,68.75247,-49.08334), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.45692,-49.06501,69.65997,-49.0639), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.65997,-49.0639,70.45692,-49.06501), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.82553,-49.05473,70.30609,-49.05307), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((70.30609,-49.05307,68.75137,-49.05196), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.75137,-49.05196,70.30609,-49.05307), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.05081,-49.04362,69.46025,-49.03862), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.46025,-49.03862,69.60248,-49.03557), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.60248,-49.03557,69.46025,-49.03862), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.42886,-49.03139,68.77164,-49.03085), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.77164,-49.03085,69.42886,-49.03139), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.15804,-49.01974,68.77164,-49.03085), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.09137,-49.0014,68.90137,-48.98335), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.90137,-48.98335,68.79692,-48.97279), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.79692,-48.97279,69.56859,-48.97224), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.56859,-48.97224,68.79692,-48.97279), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.91054,-48.89612,68.8261,-48.88445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.8261,-48.88445,68.91054,-48.89612), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.78998,-48.86695,68.8261,-48.88445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.91136,-48.84806,68.79053,-48.8464), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.79053,-48.8464,68.91136,-48.84806), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.0097,-48.83112,68.96414,-48.82112), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.96414,-48.82112,69.17775,-48.81946), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.17775,-48.81946,68.96414,-48.82112), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.10803,-48.81418,69.17775,-48.81946), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.97971,-48.76694,69.18608,-48.76667), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.18608,-48.76667,68.97971,-48.76694), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.15276,-48.75806,68.95081,-48.75445), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.95081,-48.75445,69.15276,-48.75806), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.1147,-48.72612,68.94054,-48.70029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((68.94054,-48.70029,69.07887,-48.69501), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.07887,-48.69501,68.94054,-48.70029), mapfile, tile_dir, 0, 11, "-kerguelen")
	render_tiles((69.02664,-48.65278,69.07887,-48.69501), mapfile, tile_dir, 0, 11, "-kerguelen")