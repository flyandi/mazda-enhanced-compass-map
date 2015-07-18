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
    # Region: Alabama
    # Region Name: AL

	render_tiles((-87.81887,30.22831,-87.8932,30.23924), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.8932,30.23924,-87.65689,30.24971), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.65689,30.24971,-87.8932,30.23924), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.80647,30.2798,-87.51832,30.28044), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.51832,30.28044,-87.80647,30.2798), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.25776,30.31893,-88.13617,30.32073), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.13617,30.32073,-88.19566,30.32124), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.19566,30.32124,-88.13617,30.32073), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.79672,30.3242,-88.19566,30.32124), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.45228,30.3441,-87.79672,30.3242), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.31161,30.36891,-88.39502,30.36943), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.39502,30.36943,-88.31161,30.36891), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.86502,30.38345,-88.36402,30.38801), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.36402,30.38801,-87.86502,30.38345), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.1057,30.40187,-87.43178,30.40319), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.43178,30.40319,-88.1057,30.40187), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.3666,30.43664,-87.91414,30.44614), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.91414,30.44614,-87.3666,30.43664), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.41469,30.45729,-87.91414,30.44614), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.93336,30.48736,-88.10377,30.5009), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.10377,30.5009,-87.44472,30.50748), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.44472,30.50748,-88.10377,30.5009), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.40393,30.54336,-88.08162,30.54632), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.08162,30.54632,-88.40393,30.54336), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.43145,30.55025,-87.90171,30.55088), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.90171,30.55088,-87.43145,30.55025), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.91496,30.58589,-88.0649,30.58829), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.0649,30.58829,-87.91496,30.58589), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.40119,30.60438,-88.0649,30.58829), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.062,30.64489,-87.93107,30.65269), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.93107,30.65269,-87.40019,30.6572), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.40019,30.6572,-87.93107,30.65269), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.0084,30.68496,-87.44229,30.69266), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.44229,30.69266,-88.0084,30.68496), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.41227,30.73177,-88.41247,30.7356), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.41247,30.7356,-87.52362,30.73829), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.52362,30.73829,-88.41247,30.7356), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.54227,30.76748,-87.52362,30.73829), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.63494,30.86586,-87.59206,30.95146), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.59206,30.95146,-85.89363,30.99346), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.89363,30.99346,-86.03504,30.99375), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.03504,30.99375,-85.89363,30.99346), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.18725,30.99407,-86.03504,30.99375), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.36497,30.99444,-86.38864,30.99453), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.38864,30.99453,-86.36497,30.99444), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.56349,30.9952,-85.74972,30.99528), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.74972,30.99528,-86.56349,30.9952), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.68824,30.9962,-86.78569,30.99698), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.78569,30.99698,-85.5795,30.99703), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.5795,30.99703,-86.78569,30.99698), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.83198,30.99735,-87.59883,30.99742), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.59883,30.99742,-86.83198,30.99735), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.59894,30.99742,-86.83198,30.99735), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.51953,30.99755,-87.59883,30.99742), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.92785,30.99768,-87.51953,30.99755), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.498,30.99787,-85.4883,30.99796), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.4883,30.99796,-85.498,30.99787), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.42579,30.99806,-85.4883,30.99796), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.42602,30.99828,-87.31221,30.9984), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.31221,30.9984,-88.42602,30.99828), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.16308,30.99902,-87.16264,30.99903), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.16264,30.99903,-87.16308,30.99902), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.33332,30.99956,-87.16264,30.99903), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.03129,31.00065,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.0025,31.00068,-85.14596,31.00069), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.14596,31.00069,-85.0025,31.00068), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.01139,31.05355,-85.02111,31.07546), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.02111,31.07546,-85.01139,31.05355), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.03562,31.10819,-88.43201,31.1143), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.43201,31.1143,-85.03562,31.10819), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.10752,31.18645,-85.10819,31.25859), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.10819,31.25859,-85.08977,31.29503), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.08977,31.29503,-85.08883,31.30865), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.08883,31.30865,-85.08793,31.32165), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.08793,31.32165,-85.08883,31.30865), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.09249,31.36288,-85.08793,31.32165), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.44866,31.42128,-85.06601,31.43136), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.06601,31.43136,-88.44945,31.43584), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.44945,31.43584,-85.06601,31.43136), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.07162,31.46838,-88.44945,31.43584), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.05168,31.51954,-85.04188,31.54468), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.04188,31.54468,-85.05168,31.51954), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.05796,31.57084,-85.04188,31.54468), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.05817,31.62023,-88.45948,31.62165), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.45948,31.62165,-85.05817,31.62023), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.12553,31.69497,-88.46363,31.69794), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.46363,31.69794,-85.12553,31.69497), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.11893,31.73266,-85.12544,31.76297), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.12544,31.76297,-85.12916,31.78028), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.12916,31.78028,-88.46867,31.79072), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.46867,31.79072,-85.12916,31.78028), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.14183,31.83926,-88.46867,31.79072), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.11403,31.89336,-88.46866,31.89386), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.46866,31.89386,-85.11403,31.89336), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.46866,31.89386,-85.11403,31.89336), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.46866,31.93317,-85.06783,31.96736), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.06783,31.96736,-85.06359,31.99186), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.06359,31.99186,-85.06783,31.96736), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.45339,32.05305,-85.05141,32.06226), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.05141,32.06226,-88.45339,32.05305), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.04706,32.08739,-85.05141,32.06226), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.05875,32.13602,-85.04706,32.08739), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.99777,32.18545,-84.93013,32.21905), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.93013,32.21905,-88.43115,32.22764), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.43115,32.22764,-84.91994,32.23085), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.91994,32.23085,-88.43115,32.22764), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.42828,32.25014,-84.89184,32.2634), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.89184,32.2634,-88.42828,32.25014), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.9557,32.30591,-88.42131,32.30868), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.42131,32.30868,-84.9557,32.30591), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.0081,32.33668,-84.98347,32.36319), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.98347,32.36319,-84.98115,32.37904), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.98115,32.37904,-84.98347,32.36319), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.97183,32.44284,-84.98115,32.37904), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-84.99979,32.50707,-85.00113,32.51015), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.00113,32.51015,-84.99979,32.50707), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.0071,32.52387,-85.00113,32.51015), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.38925,32.57812,-85.06985,32.58315), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.06985,32.58315,-88.38925,32.57812), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.07607,32.60807,-85.06985,32.58315), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.08853,32.65796,-85.07607,32.60807), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.37334,32.71183,-85.11425,32.73045), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.11425,32.73045,-88.37334,32.71183), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.12453,32.75163,-85.11425,32.73045), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.16096,32.82667,-85.1844,32.86132), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.1844,32.86132,-85.18612,32.87014), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.18612,32.87014,-85.1844,32.86132), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.34749,32.92903,-85.18612,32.87014), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.34008,32.99126,-88.34749,32.92903), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.23244,33.10808,-85.2366,33.12954), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.23244,33.10808,-85.2366,33.12954), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.2366,33.12954,-85.23244,33.10808), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.31714,33.18412,-85.2366,33.12954), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.30444,33.28832,-88.31714,33.18412), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.29435,33.42799,-85.30494,33.48276), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.30494,33.48276,-85.31405,33.52981), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.31405,33.52981,-88.27452,33.534), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.27452,33.534,-85.31405,33.52981), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.33812,33.65311,-88.25445,33.69878), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.25445,33.69878,-85.33812,33.65311), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.24839,33.74491,-85.36053,33.76796), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.36053,33.76796,-88.24839,33.74491), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.38667,33.9017,-85.39887,33.96413), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.39887,33.96413,-85.38667,33.9017), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.39887,33.96413,-85.38667,33.9017), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.20723,34.05833,-85.42107,34.08081), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.42107,34.08081,-88.20358,34.08653), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.20358,34.08653,-85.42107,34.08081), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.4295,34.1251,-88.20358,34.08653), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.46314,34.28619,-88.17326,34.32104), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.17326,34.32104,-85.47515,34.34368), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.47515,34.34368,-88.17326,34.32104), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.1549,34.46303,-85.50247,34.47453), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.50247,34.47453,-88.1549,34.46303), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.51304,34.52395,-85.50247,34.47453), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.13956,34.5817,-85.52689,34.58869), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.52689,34.58869,-88.13956,34.5817), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.13426,34.62266,-85.53441,34.62379), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.53441,34.62379,-88.13426,34.62266), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.56142,34.75008,-85.58281,34.86044), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.58281,34.86044,-88.09789,34.8922), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.09789,34.8922,-88.15462,34.92239), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.15462,34.92239,-85.59517,34.92417), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.59517,34.92417,-88.15462,34.92239), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.60517,34.98468,-85.86395,34.98703), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-85.86395,34.98703,-85.60517,34.98468), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.4678,34.99069,-86.31876,34.99108), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.31876,34.99108,-86.31127,34.9911), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.31127,34.9911,-86.31876,34.99108), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.78363,34.99192,-86.78365,34.99193), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.78365,34.99193,-86.78363,34.99192), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-86.83629,34.9928,-86.78365,34.99193), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.20006,34.99563,-86.83629,34.9928), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.21076,34.99905,-87.21668,34.99915), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.21668,34.99915,-87.22405,34.99923), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.22405,34.99923,-87.21668,34.99915), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.6061,35.00352,-87.62503,35.00373), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.62503,35.00373,-87.6061,35.00352), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.85189,35.00566,-87.98492,35.00591), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-87.98492,35.00591,-88.00003,35.00594), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.00003,35.00594,-87.98492,35.00591), mapfile, tile_dir, 0, 11, "alabama-al")
	render_tiles((-88.20296,35.00803,-88.00003,35.00594), mapfile, tile_dir, 0, 11, "alabama-al")