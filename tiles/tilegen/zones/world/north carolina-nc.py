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
    # Region: North Carolina
    # Region Name: NC

	render_tiles((-78.54109,33.85111,-77.96017,33.85332), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.96017,33.85332,-78.54109,33.85111), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.00677,33.8587,-77.96017,33.85332), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.01869,33.88829,-78.38396,33.90195), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.38396,33.90195,-78.13695,33.91218), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.13695,33.91218,-78.27615,33.91236), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.27615,33.91236,-78.13695,33.91218), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.61593,33.91552,-78.27615,33.91236), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.93483,33.92055,-78.61593,33.91552), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.65089,33.94507,-77.93483,33.92055), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.91554,33.97172,-78.65089,33.94507), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.89709,34.01251,-77.91554,33.97172), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.81171,34.08101,-77.89709,34.01251), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.82921,34.16262,-78.81171,34.08101), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.76402,34.24564,-77.71351,34.29025), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.71351,34.29025,-79.07117,34.29924), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.07117,34.29924,-77.71351,34.29025), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.63503,34.35956,-79.07117,34.29924), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.51522,34.43738,-77.46292,34.47135), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.46292,34.47135,-77.51522,34.43738), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.32252,34.53557,-79.35832,34.54536), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.35832,34.54536,-77.32252,34.53557), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.24099,34.58751,-76.53595,34.58858), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.53595,34.58858,-77.24099,34.58751), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.45028,34.62061,-76.55381,34.62825), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.55381,34.62825,-79.46197,34.63017), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.46197,34.63017,-76.55381,34.62825), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.13684,34.63293,-79.46197,34.63017), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.11296,34.63809,-77.13684,34.63293), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.61872,34.67255,-76.90626,34.68282), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.90626,34.68282,-76.61872,34.67255), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.72697,34.69669,-76.90626,34.68282), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.45045,34.71445,-76.72697,34.69669), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.3868,34.78458,-79.6753,34.80474), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.6753,34.80474,-79.69295,34.80496), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.69295,34.80496,-79.6753,34.80474), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.92431,34.80782,-79.9276,34.80787), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.9276,34.80787,-79.92431,34.80782), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.07722,34.80972,-79.9276,34.80787), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.32042,34.81361,-80.56167,34.81748), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.56167,34.81748,-80.62578,34.81922), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.56166,34.81748,-80.62578,34.81922), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.62578,34.81922,-80.56167,34.81748), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.797,34.82387,-80.62578,34.81922), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.31021,34.85231,-80.797,34.82387), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.23309,34.90548,-80.78204,34.93578), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.78204,34.93578,-76.23309,34.90548), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.61999,34.98659,-83.93641,34.98748), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.93641,34.98748,-83.93665,34.98749), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.93665,34.98749,-83.93641,34.98748), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.00551,34.98765,-83.93665,34.98749), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.13727,34.98786,-84.12944,34.98795), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.12944,34.98795,-76.13727,34.98786), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.32187,34.98841,-83.54918,34.9888), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.54918,34.9888,-84.32187,34.98841), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.4827,34.99088,-83.54918,34.9888), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.32277,34.99587,-83.10861,35.00066), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.10861,35.00066,-80.84057,35.00147), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.10861,35.00066,-80.84057,35.00147), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.84057,35.00147,-83.10861,35.00066), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.00843,35.02693,-81.04149,35.0447), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.04149,35.0447,-82.8975,35.05602), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.8975,35.05602,-76.01315,35.06186), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.01315,35.06186,-82.8975,35.05602), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.05803,35.07319,-80.90624,35.07518), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.90624,35.07518,-81.05803,35.07319), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.74643,35.07913,-82.76206,35.08187), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.76206,35.08187,-82.74643,35.07913), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.78328,35.0856,-82.76206,35.08187), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.93495,35.10741,-75.91299,35.1196), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.91299,35.1196,-81.03676,35.12255), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.03676,35.12255,-82.68604,35.12455), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.68604,35.12455,-81.03676,35.12255), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.57772,35.14648,-81.04227,35.14661), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.04227,35.14661,-82.57772,35.14648), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.04287,35.14925,-81.04227,35.14661), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.53256,35.15562,-81.04287,35.14925), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.32809,35.16229,-81.36761,35.16409), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.36761,35.16409,-81.32809,35.16229), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.49427,35.16988,-81.36761,35.16409), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.45561,35.17743,-81.76809,35.17971), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.76809,35.17971,-82.45561,35.17743), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.75792,35.18308,-81.87411,35.18351), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.87411,35.18351,-75.75792,35.18308), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.74956,35.18562,-81.96934,35.18693), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.96934,35.18693,-75.74956,35.18562), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.03965,35.18945,-82.04839,35.18964), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.04839,35.18964,-82.03965,35.18945), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.21625,35.19326,-82.29535,35.19497), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.29535,35.19497,-82.21625,35.19326), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.35302,35.1987,-82.29535,35.19497), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.4113,35.20248,-84.2866,35.20575), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.2866,35.20575,-82.4113,35.20248), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.2866,35.20575,-82.4113,35.20248), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.63549,35.22026,-75.53363,35.22583), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.53363,35.22583,-84.28322,35.22658), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.28322,35.22658,-75.53363,35.22583), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.17852,35.24068,-84.09751,35.24738), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.09751,35.24738,-84.17852,35.24068), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.22372,35.26908,-84.09751,35.24738), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.02911,35.29212,-84.02351,35.29578), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.02351,35.29578,-84.02911,35.29212), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.03808,35.34836,-84.00759,35.37166), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.00759,35.37166,-75.48677,35.39165), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.48677,35.39165,-84.02178,35.40742), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-84.02178,35.40742,-75.48677,35.39165), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.97317,35.45258,-83.95888,35.45791), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.95888,35.45791,-83.95311,35.46007), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.95311,35.46007,-83.95888,35.45791), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.9168,35.47361,-83.95311,35.46007), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.8485,35.51926,-83.77174,35.56212), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.77174,35.56212,-83.49834,35.56298), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.49834,35.56298,-83.77174,35.56212), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.58783,35.56696,-83.66289,35.5678), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.66289,35.5678,-83.65316,35.56831), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.65316,35.56831,-83.66289,35.5678), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.45866,35.5966,-83.45243,35.60292), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.45243,35.60292,-75.45866,35.5966), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.42158,35.61119,-83.45243,35.60292), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.29715,35.65775,-83.34726,35.66047), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.34726,35.66047,-83.29715,35.65775), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.26463,35.70324,-83.25619,35.71506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.25619,35.71506,-83.25535,35.71623), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.25535,35.71623,-83.25619,35.71506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.19827,35.72549,-75.49609,35.72852), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.49609,35.72852,-83.19827,35.72549), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.16154,35.76336,-75.51901,35.76909), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.51901,35.76909,-83.16154,35.76336), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.09719,35.77607,-82.97841,35.78261), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.97841,35.78261,-83.04853,35.78771), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-83.04853,35.78771,-82.97841,35.78261), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.96655,35.79555,-83.04853,35.78771), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.93744,35.82732,-82.96655,35.79555), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.56979,35.8633,-82.89972,35.8746), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.89972,35.8746,-75.56979,35.8633), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.81613,35.92399,-82.91061,35.92693), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.91061,35.92693,-82.81613,35.92399), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.89387,35.93381,-82.91061,35.92693), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.86072,35.94743,-82.78747,35.95216), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.78747,35.95216,-82.55787,35.9539), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.55787,35.9539,-82.78747,35.95216), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.61089,35.97444,-82.50787,35.98209), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.50787,35.98209,-82.61089,35.97444), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.7794,35.99251,-82.50787,35.98209), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.46456,36.00651,-82.72507,36.0182), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.72507,36.0182,-75.65854,36.02043), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.65854,36.02043,-82.72507,36.0182), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.59553,36.02601,-75.65854,36.02043), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.60573,36.03723,-82.59553,36.02601), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.62837,36.06211,-82.41695,36.07295), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.41695,36.07295,-82.40946,36.08341), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.40946,36.08341,-82.41695,36.07295), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.12715,36.10442,-82.08052,36.10571), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.08052,36.10571,-82.08014,36.10572), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.08014,36.10572,-82.08052,36.10571), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.71831,36.11367,-82.34686,36.11521), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.34686,36.11521,-75.71831,36.11367), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.02874,36.12432,-82.26569,36.12761), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.26569,36.12761,-82.02874,36.12432), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.29766,36.13351,-82.14085,36.13622), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.14085,36.13622,-82.29766,36.13351), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.22027,36.15381,-82.21125,36.15901), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-82.21125,36.15901,-82.22027,36.15381), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.9601,36.22813,-75.77065,36.23208), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.77065,36.23208,-81.9601,36.22813), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.93437,36.26472,-81.91845,36.28735), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.91845,36.28735,-75.79641,36.29035), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.79641,36.29035,-81.91845,36.28735), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.90814,36.30201,-75.79641,36.29035), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.70597,36.3385,-81.76898,36.34104), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.76898,36.34104,-81.70597,36.3385), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.8332,36.34734,-81.76898,36.34104), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.72524,36.38938,-81.73431,36.41334), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.73431,36.41334,-75.83844,36.4349), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.83844,36.4349,-81.73431,36.41334), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.69531,36.46791,-75.83844,36.4349), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.69996,36.53683,-79.51065,36.54074), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.51065,36.54074,-79.47015,36.54084), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.51096,36.54074,-79.47015,36.54084), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.47015,36.54084,-79.51065,36.54074), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.50997,36.54107,-79.34269,36.54114), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.34269,36.54114,-78.50997,36.54107), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.71486,36.54143,-79.21864,36.54144), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.21864,36.54144,-78.45743,36.54145), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.45743,36.54145,-79.21864,36.54144), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.73412,36.54161,-79.13794,36.54164), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.13794,36.54164,-78.73412,36.54161), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.7963,36.54176,-79.13794,36.54164), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-79.89157,36.54203,-78.94201,36.54211), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.94201,36.54211,-79.89157,36.54203), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.32364,36.54242,-80.02727,36.5425), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.02727,36.5425,-78.32364,36.54242), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.05345,36.54264,-80.02727,36.5425), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.13291,36.54381,-80.29524,36.54397), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.29524,36.54397,-78.13291,36.54381), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-78.0462,36.5442,-80.29524,36.54397), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.89977,36.54485,-77.7671,36.54544), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.7671,36.54544,-77.74971,36.54552), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.74971,36.54552,-77.7671,36.54544), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.29877,36.54604,-76.91732,36.54605), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.91732,36.54605,-77.29877,36.54604), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.91606,36.54608,-76.91573,36.54609), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.91573,36.54609,-76.91606,36.54608), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.16435,36.54615,-77.19018,36.54616), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-77.19018,36.54616,-77.16435,36.54615), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.43161,36.55022,-76.31322,36.55055), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.31322,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.3132,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.12235,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.02675,36.55055,-80.4401,36.5506), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.4401,36.5506,-76.31322,36.55055), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.49148,36.55073,-75.86704,36.55075), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-75.86704,36.55075,-76.49148,36.55073), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.5416,36.55078,-75.86704,36.55075), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-76.73833,36.55099,-76.5416,36.55078), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.61219,36.55822,-80.90184,36.56175), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.90184,36.56175,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.90173,36.56175,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.84021,36.56193,-80.90184,36.56175), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-80.70483,36.56232,-80.84021,36.56193), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.06187,36.56702,-80.70483,36.56232), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.17671,36.57193,-81.35313,36.57624), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.35313,36.57624,-81.49983,36.57982), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.49983,36.57982,-81.35313,36.57624), mapfile, tile_dir, 0, 11, "north carolina-nc")
	render_tiles((-81.67754,36.58812,-81.49983,36.57982), mapfile, tile_dir, 0, 11, "north carolina-nc")