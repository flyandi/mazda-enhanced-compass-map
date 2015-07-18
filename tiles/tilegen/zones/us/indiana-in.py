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
    # Region: Indiana
    # Region Name: IN

	render_tiles((-87.10561,37.76763,-87.97026,37.78186), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.97026,37.78186,-87.93586,37.7897), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.93586,37.7897,-87.97026,37.78186), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.02803,37.79922,-87.1375,37.80726), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.1375,37.80726,-88.02803,37.79922), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.9038,37.81776,-87.05784,37.82746), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.05784,37.82746,-87.9038,37.81776), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.18006,37.84138,-87.62585,37.85192), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.62585,37.85192,-86.61522,37.85286), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.61522,37.85286,-87.62585,37.85192), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.68163,37.85592,-88.06736,37.85605), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.06736,37.85605,-87.68163,37.85592), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.6467,37.86491,-88.05947,37.86669), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.05947,37.86669,-87.25525,37.86733), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.05947,37.86669,-87.25525,37.86733), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.25525,37.86733,-88.05947,37.86669), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.65837,37.86938,-87.93813,37.87065), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.93813,37.87065,-86.65837,37.86938), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.04305,37.87505,-87.80801,37.87519), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.80801,37.87519,-87.04305,37.87505), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.27039,37.87542,-87.80801,37.87519), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.71321,37.88309,-87.27039,37.87542), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.04086,37.89177,-87.72364,37.89206), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.72364,37.89206,-88.04086,37.89177), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.72225,37.89265,-87.72364,37.89206), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.30406,37.89343,-86.72225,37.89265), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.60848,37.89879,-87.92539,37.89959), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.92539,37.89959,-87.60848,37.89879), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.50937,37.90289,-87.92539,37.89959), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.59985,37.90675,-87.92174,37.90789), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.92174,37.90789,-87.33177,37.90825), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.33177,37.90825,-87.92174,37.90789), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.01032,37.91967,-87.48635,37.92022), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.48635,37.92022,-87.01032,37.91967), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.87254,37.921,-87.48635,37.92022), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.55128,37.92542,-86.97774,37.9257), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.97774,37.9257,-87.55128,37.92542), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.44864,37.93388,-86.92775,37.93496), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.92775,37.93496,-87.44864,37.93388), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.41859,37.94476,-86.92775,37.93496), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.77999,37.95652,-88.01631,37.96157), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.01631,37.96157,-86.77999,37.95652), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.52517,37.96823,-86.03339,37.97038), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.03339,37.97038,-86.87587,37.97077), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.87587,37.97077,-86.03339,37.97038), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.99735,37.99123,-86.81366,37.99603), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.81366,37.99603,-86.81091,37.99715), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.81091,37.99715,-86.81366,37.99603), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.97603,38.00356,-86.09577,38.00893), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.09577,38.00893,-85.97603,38.00356), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.95173,38.01494,-86.09577,38.00893), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.20644,38.02188,-85.9224,38.02868), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.9224,38.02868,-88.03088,38.03071), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-88.03088,38.03071,-85.9224,38.02868), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.52183,38.03833,-86.48805,38.04367), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.48805,38.04367,-86.4719,38.04622), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.4719,38.04622,-86.48805,38.04367), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.98877,38.05559,-86.4719,38.04622), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.43405,38.08676,-86.43357,38.08714), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.43357,38.08714,-86.43405,38.08676), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.27866,38.09851,-87.96221,38.10005), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.96221,38.10005,-86.27866,38.09851), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.90516,38.11107,-87.96221,38.10005), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.38722,38.12463,-86.35641,38.13528), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.35641,38.13528,-86.38722,38.12463), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.32127,38.14742,-87.92747,38.15195), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.92747,38.15195,-86.32127,38.14742), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.89591,38.17993,-85.89476,38.18847), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.89476,38.18847,-85.89591,38.17993), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.97582,38.19783,-85.89476,38.18847), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.9702,38.23027,-87.96897,38.23739), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.96897,38.23739,-85.83966,38.23977), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.83966,38.23977,-87.96897,38.23739), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.75096,38.26787,-87.90854,38.26858), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.90854,38.26858,-85.75096,38.26787), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.79451,38.27795,-85.81616,38.28297), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.81616,38.28297,-85.79451,38.27795), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.68356,38.29547,-87.83197,38.30724), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.83197,38.30724,-85.68356,38.29547), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.6462,38.34292,-87.78,38.37084), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.78,38.37084,-85.63444,38.3784), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.63444,38.3784,-87.78,38.37084), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.62163,38.41709,-87.75111,38.41885), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.75111,38.41885,-85.62163,38.41709), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.74104,38.43558,-85.58776,38.4505), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.58776,38.4505,-87.74104,38.43558), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.49887,38.46824,-87.71405,38.47988), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.71405,38.47988,-85.49887,38.46824), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.47435,38.50407,-87.65417,38.51191), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.65417,38.51191,-85.47435,38.50407), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.43314,38.52391,-85.43297,38.52412), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.43297,38.52412,-85.43314,38.52391), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.66073,38.54109,-85.4156,38.54634), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.4156,38.54634,-87.66073,38.54109), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.64836,38.56663,-85.43142,38.58629), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.43142,38.58629,-87.63775,38.58851), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.63775,38.58851,-85.43142,38.58629), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.43617,38.59829,-87.63775,38.58851), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.62012,38.63949,-85.43874,38.65932), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.43874,38.65932,-87.54554,38.67761), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.54554,38.67761,-85.18728,38.68761), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.18728,38.68761,-85.14686,38.69543), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.14686,38.69543,-85.20176,38.69744), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.20176,38.69744,-85.14686,38.69543), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.44886,38.71337,-85.23867,38.72249), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.23867,38.72249,-85.44886,38.71337), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.34095,38.73389,-85.33264,38.73482), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.33264,38.73482,-85.34095,38.73389), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.40048,38.73598,-85.33264,38.73482), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.27545,38.74117,-85.07193,38.74157), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.07193,38.74157,-85.27545,38.74117), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.49895,38.75777,-85.02105,38.75853), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.02105,38.75853,-87.49895,38.75777), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.96254,38.77804,-84.81288,38.78609), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81288,38.78609,-84.8569,38.79022), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.8569,38.79022,-84.81288,38.78609), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52168,38.82658,-84.80325,38.85072), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80325,38.85072,-87.53526,38.85249), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53526,38.85249,-84.80325,38.85072), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.7987,38.85923,-87.53526,38.85249), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.54737,38.87561,-84.78641,38.88222), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.78641,38.88222,-87.54737,38.87561), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.83047,38.89726,-87.52872,38.90594), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52872,38.90594,-87.52765,38.90769), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52765,38.90769,-87.52872,38.90594), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.86443,38.91384,-87.52765,38.90769), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.87776,38.92036,-84.86443,38.91384), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.83262,38.96146,-87.5295,38.97193), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.5295,38.97193,-84.83262,38.96146), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.84945,39.00092,-87.57912,39.00161), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.57912,39.00161,-84.84945,39.00092), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.87757,39.03126,-84.89717,39.05241), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.89717,39.05241,-87.57259,39.05729), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.57259,39.05729,-84.89717,39.05241), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.86069,39.07814,-87.57259,39.05729), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.62538,39.10181,-84.82016,39.10548), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.82016,39.10548,-87.62538,39.10181), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.63829,39.15749,-87.64044,39.16673), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.64044,39.16673,-87.63829,39.15749), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.57703,39.21112,-84.82016,39.22723), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.82016,39.22723,-87.57703,39.21112), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.59349,39.24745,-87.59475,39.25938), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.59475,39.25938,-87.59349,39.24745), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81888,39.30514,-84.81888,39.30517), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81888,39.30517,-84.81888,39.30514), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.6004,39.3129,-84.81888,39.30517), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.57833,39.34034,-87.53165,39.34789), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53165,39.34789,-87.57833,39.34034), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81745,39.39175,-87.53165,39.34789), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53162,39.46938,-87.53167,39.47711), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53167,39.47711,-87.53162,39.46938), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81616,39.52197,-87.53167,39.47711), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81571,39.56772,-87.53239,39.6073), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53239,39.6073,-84.81571,39.56772), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.5327,39.66487,-87.53239,39.6073), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81413,39.72656,-84.81413,39.72662), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81413,39.72662,-84.81413,39.72656), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53245,39.883,-84.81142,39.91691), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81142,39.91691,-87.53245,39.883), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.81016,40.00507,-87.53231,40.01159), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53231,40.01159,-84.81016,40.00507), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80871,40.10722,-87.53102,40.14804), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53102,40.14804,-84.80871,40.10722), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.53005,40.25067,-84.80492,40.3101), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80492,40.3101,-84.80412,40.35276), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80412,40.35276,-84.80412,40.35284), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80412,40.35284,-84.80412,40.35276), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80293,40.46539,-87.52707,40.47688), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52707,40.47688,-84.80293,40.46539), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52688,40.49122,-84.80255,40.50181), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80255,40.50181,-87.52688,40.49122), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52629,40.53541,-84.80255,40.50181), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80241,40.57221,-87.52629,40.53541), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80212,40.72815,-84.80212,40.72816), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80212,40.72816,-84.80212,40.72815), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52614,40.73689,-84.80212,40.72816), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52601,40.89558,-84.80267,40.92257), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80267,40.92257,-87.52601,40.89558), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80286,40.98937,-87.52646,41.01035), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52646,41.01035,-87.52652,41.02484), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52652,41.02484,-87.52646,41.01035), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80323,41.12141,-87.52665,41.16609), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52665,41.16609,-84.80323,41.12141), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80364,41.25256,-84.8037,41.27126), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.8037,41.27126,-84.80364,41.25256), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52677,41.29805,-87.52677,41.29818), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52677,41.29818,-87.52677,41.29805), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80413,41.40829,-84.80425,41.42605), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80425,41.42605,-84.80413,41.40829), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52541,41.47028,-84.80425,41.42605), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52494,41.52974,-84.80496,41.53014), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80496,41.53014,-87.52494,41.52974), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.26154,41.62034,-87.2228,41.62889), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.2228,41.62889,-87.36544,41.62954), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.36544,41.62954,-87.2228,41.62889), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.12584,41.6503,-87.36544,41.62954), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.47074,41.67284,-87.02789,41.67466), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.02789,41.67466,-87.47074,41.67284), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.41582,41.68818,-84.80608,41.69609), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80608,41.69609,-87.41582,41.68818), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-87.52404,41.70834,-86.93285,41.7165), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.93285,41.7165,-87.52404,41.70834), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.90913,41.72694,-86.93285,41.7165), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.79136,41.75905,-85.65975,41.75924), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.79133,41.75905,-85.65975,41.75924), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.65975,41.75924,-85.79136,41.75905), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.50177,41.75955,-86.52422,41.75957), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.52422,41.75957,-86.50177,41.75955), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.06256,41.75965,-86.64004,41.75967), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.64004,41.75967,-86.06256,41.75965), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.64132,41.75967,-86.06256,41.75965), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.29218,41.75976,-85.23284,41.75984), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.23284,41.75984,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-85.19677,41.75987,-85.23284,41.75984), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.22607,41.76002,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.22609,41.76002,-85.19677,41.75987), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.82513,41.7602,-84.80588,41.76022), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-84.80588,41.76022,-84.82513,41.7602), mapfile, tile_dir, 0, 11, "indiana-in")
	render_tiles((-86.82483,41.76024,-84.80588,41.76022), mapfile, tile_dir, 0, 11, "indiana-in")