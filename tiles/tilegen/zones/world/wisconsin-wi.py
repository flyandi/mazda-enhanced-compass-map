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
    # Region: Wisconsin
    # Region Name: WI

	render_tiles((-86.89989,45.29519,-86.9562,45.35201), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.9562,45.35201,-86.86774,45.35307), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.86774,45.35307,-86.9562,45.35201), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.80587,45.4129,-86.93428,45.42115), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.93428,45.42115,-86.80587,45.4129), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.93428,45.42115,-86.80587,45.4129), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.83575,45.45019,-86.93428,45.42115), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.63712,46.90672,-90.5491,46.91546), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.5491,46.91546,-90.63712,46.90672), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.52406,46.93566,-90.5491,46.91546), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.67945,46.95603,-90.51162,46.96141), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.51162,46.96141,-90.67945,46.95603), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.71203,46.98526,-90.76799,47.00233), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.76799,47.00233,-90.54488,47.01738), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.54488,47.01738,-90.77692,47.02432), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.77692,47.02432,-90.54488,47.01738), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.77692,47.02432,-90.54488,47.01738), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.74018,47.03611,-90.56094,47.03701), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.56094,47.03701,-90.74018,47.03611), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.65042,47.05468,-90.56094,47.03701), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.80048,42.49192,-87.8977,42.49285), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.8977,42.49285,-88.70738,42.49359), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.70738,42.49359,-88.7765,42.49414), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.70738,42.49359,-88.7765,42.49414), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.7765,42.49414,-88.70738,42.49359), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.50691,42.49488,-88.94038,42.49544), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.94038,42.49544,-88.30469,42.49561), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.30469,42.49561,-88.19953,42.49576), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.19953,42.49576,-88.99256,42.49585), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.99256,42.49585,-88.2169,42.49592), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.2169,42.49592,-88.99256,42.49585), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.0429,42.49626,-88.2169,42.49592), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.3658,42.50003,-89.40142,42.50044), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.40142,42.50044,-89.3658,42.50003), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.49322,42.50151,-89.40142,42.50044), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.83759,42.50491,-89.92648,42.50579), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.92648,42.50579,-89.83759,42.50491), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.92701,42.50579,-89.83759,42.50491), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.43701,42.50715,-90.42638,42.50718), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.42638,42.50718,-90.43701,42.50715), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.22319,42.50777,-90.42638,42.50718), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.64284,42.50848,-90.22319,42.50777), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.67273,42.5766,-87.81327,42.57922), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.81327,42.57922,-90.67273,42.5766), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.70086,42.62645,-87.81467,42.64402), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.81467,42.64402,-90.74368,42.64556), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.74368,42.64556,-87.81467,42.64402), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.8525,42.66482,-87.80202,42.66831), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.80202,42.66831,-90.8525,42.66482), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.89696,42.67432,-87.80202,42.66831), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.94157,42.68384,-90.89696,42.67432), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.78507,42.70082,-90.94157,42.68384), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.01724,42.71957,-87.78507,42.70082), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.07072,42.7755,-87.76668,42.7849), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.76668,42.7849,-91.07072,42.7755), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.82116,42.84227,-87.82739,42.84883), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.82739,42.84883,-87.82116,42.84227), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.83488,42.85672,-91.09882,42.86442), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.09882,42.86442,-87.83488,42.85672), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.138,42.90377,-91.09882,42.86442), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.84268,42.94412,-91.15552,42.97577), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.15552,42.97577,-91.15908,42.98748), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.15908,42.98748,-91.15552,42.97577), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.89578,43.01581,-91.17469,43.03871), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.17469,43.03871,-87.89578,43.01581), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.87018,43.06441,-91.17493,43.08026), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.17493,43.08026,-87.87018,43.06441), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.90049,43.12591,-91.17525,43.13467), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.17525,43.13467,-87.90049,43.12591), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.13417,43.17441,-87.89658,43.19213), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.89658,43.19213,-87.89629,43.19711), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.89629,43.19711,-87.89658,43.19213), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.08746,43.22189,-87.89629,43.19711), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.05791,43.25397,-91.08746,43.22189), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.88921,43.30765,-91.10724,43.31365), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.10724,43.31365,-87.88921,43.30765), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.15481,43.33483,-91.10724,43.31365), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.20737,43.37366,-91.19941,43.40303), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.19941,43.40303,-91.21066,43.41944), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.21066,43.41944,-87.84096,43.42068), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.84096,43.42068,-91.21066,43.41944), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.23228,43.45095,-87.84096,43.42068), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.79324,43.49278,-91.21771,43.50055), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.21771,43.50055,-87.79324,43.49278), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.79102,43.54302,-87.79014,43.56305), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.79014,43.56305,-91.23281,43.56484), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.23281,43.56484,-87.79014,43.56305), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.25293,43.60036,-91.23281,43.56484), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.27325,43.66662,-87.7062,43.67954), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.7062,43.67954,-91.27325,43.66662), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.70819,43.7229,-91.257,43.72566), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.257,43.72566,-87.70819,43.7229), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.70025,43.76735,-91.24396,43.77305), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.24396,43.77305,-87.70025,43.76735), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.72641,43.81045,-91.28766,43.84707), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.28766,43.84707,-91.291,43.85273), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.291,43.85273,-91.28766,43.84707), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.73602,43.87372,-87.72858,43.89221), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.72858,43.89221,-87.73602,43.87372), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.35743,43.91723,-87.72858,43.89221), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.69892,43.96594,-91.42357,43.9843), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.42357,43.9843,-91.44054,44.0015), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.44054,44.0015,-91.42357,43.9843), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.55922,44.02421,-91.57328,44.0269), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.57328,44.0269,-91.55922,44.02421), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.64787,44.06411,-87.65518,44.08189), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.65518,44.08189,-91.64787,44.06411), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.7191,44.12885,-87.60088,44.1317), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.60088,44.1317,-91.7191,44.12885), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.53994,44.15969,-91.8173,44.16424), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.8173,44.16424,-87.53994,44.15969), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.8545,44.19723,-87.50742,44.2108), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.50742,44.2108,-91.8545,44.19723), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.8927,44.23111,-87.50742,44.2108), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.52176,44.25996,-91.8927,44.23111), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.91619,44.31809,-87.54538,44.32139), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.54538,44.32139,-91.91619,44.31809), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.54333,44.32751,-87.54538,44.32139), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.9636,44.36211,-87.54333,44.32751), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.08453,44.40461,-92.11109,44.41395), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.11109,44.41395,-92.08453,44.40461), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.23247,44.44543,-92.24536,44.45425), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.24536,44.45425,-87.49866,44.46069), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.49866,44.46069,-92.24536,44.45425), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29101,44.48546,-87.49866,44.46069), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.9438,44.52969,-92.31407,44.53801), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.31407,44.53801,-88.00552,44.53922), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.00552,44.53922,-92.31693,44.53928), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.31693,44.53928,-88.00552,44.53922), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.39928,44.55829,-92.36152,44.55894), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.36152,44.55894,-92.39928,44.55829), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.0412,44.57258,-87.89889,44.57414), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.89889,44.57414,-88.0412,44.57258), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.54928,44.5777,-87.89889,44.57414), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.44696,44.58627,-92.54928,44.5777), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.86688,44.60843,-87.99872,44.60929), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.99872,44.60929,-87.86688,44.60843), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.61803,44.61287,-87.99872,44.60929), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.40163,44.63119,-87.77516,44.63928), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.77516,44.63928,-87.40163,44.63119), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.76131,44.6537,-88.00209,44.66404), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.00209,44.66404,-87.74841,44.66712), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.74841,44.66712,-88.00209,44.66404), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.37549,44.67551,-87.73757,44.67701), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.73757,44.67701,-87.99757,44.67766), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.99757,44.67766,-87.73757,44.67701), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.69649,44.68944,-87.71978,44.69325), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.71978,44.69325,-92.69649,44.68944), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.73162,44.71492,-87.98349,44.7202), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.98349,44.7202,-87.72089,44.72455), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.72089,44.72455,-87.98349,44.7202), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.94145,44.75608,-92.79236,44.75898), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.79236,44.75898,-87.94145,44.75608), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.80529,44.76836,-87.31898,44.77134), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.31898,44.77134,-92.80529,44.76836), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.6463,44.79874,-87.90448,44.81872), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.90448,44.81872,-87.27603,44.83318), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.27603,44.83318,-87.90448,44.81872), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.58131,44.85179,-92.76857,44.85437), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76857,44.85437,-87.58131,44.85179), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.531,44.85744,-87.85468,44.85777), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.85468,44.85777,-87.531,44.85744), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76712,44.86152,-92.76702,44.86198), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76702,44.86198,-92.76712,44.86152), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.51514,44.8696,-87.83836,44.87399), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.83836,44.87399,-87.51514,44.8696), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.20629,44.88593,-87.44648,44.88611), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.44648,44.88611,-87.20629,44.88593), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.84343,44.92436,-87.39341,44.93439), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.39341,44.93439,-92.7508,44.94157), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.7508,44.94157,-87.18838,44.94808), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.18838,44.94808,-87.81299,44.95401), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.81299,44.95401,-87.18838,44.94808), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.76264,44.96275,-87.81299,44.95401), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.69649,44.97423,-87.6303,44.97687), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.6303,44.97687,-87.69649,44.97423), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.13938,45.01257,-87.33646,45.01353), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.33646,45.01353,-87.13938,45.01257), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.7619,45.02247,-87.33646,45.01353), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.62575,45.04516,-92.80291,45.0654), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.80291,45.0654,-87.06316,45.07932), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.06316,45.07932,-87.26488,45.08136), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.26488,45.08136,-87.06316,45.07932), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.59021,45.09526,-87.64819,45.10637), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.64819,45.10637,-92.74051,45.1134), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.74051,45.1134,-87.64819,45.10637), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.74382,45.12365,-92.74051,45.1134), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.04575,45.13499,-92.74382,45.12365), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.69506,45.15052,-87.04575,45.13499), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.23822,45.16726,-87.17507,45.17305), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.17507,45.17305,-87.23822,45.16726), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.04417,45.18695,-92.76693,45.19511), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76693,45.19511,-87.74181,45.19705), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.74181,45.19705,-92.76693,45.19511), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.12161,45.20978,-92.76609,45.21002), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76609,45.21002,-87.12161,45.20978), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.97876,45.22733,-92.76609,45.21002), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.71148,45.24522,-87.10874,45.257), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.10874,45.257,-87.71148,45.24522), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.76187,45.28494,-86.97778,45.29068), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-86.97778,45.29068,-87.05763,45.29284), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.05763,45.29284,-86.97778,45.29068), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.74827,45.29606,-87.01704,45.29925), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.01704,45.29925,-92.74827,45.29606), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.66742,45.31636,-87.01704,45.29925), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.69897,45.33637,-87.86349,45.35302), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.86349,45.35302,-87.80046,45.35361), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.80046,45.35361,-87.86349,45.35302), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.75093,45.35504,-87.80046,45.35361), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.65735,45.36875,-87.75093,45.35504), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.70677,45.38383,-87.85683,45.39311), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.85683,45.39311,-92.65849,45.39606), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.65849,45.39606,-87.85683,45.39311), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.64677,45.43793,-87.84743,45.44418), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.84743,45.44418,-92.64677,45.43793), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.68679,45.47227,-87.80577,45.47314), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.80577,45.47314,-92.68679,45.47227), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.8042,45.52468,-92.72802,45.52565), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.72802,45.52565,-87.8042,45.52468), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.75691,45.5575,-92.8015,45.56285), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.8015,45.56285,-92.75691,45.5575), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.88114,45.57341,-87.78729,45.57491), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.78729,45.57491,-92.88114,45.57341), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.77767,45.6092,-92.88793,45.63901), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.88793,45.63901,-92.8867,45.64415), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.8867,45.64415,-92.88793,45.63901), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.8867,45.64415,-92.88793,45.63901), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.82468,45.65321,-92.8867,45.64415), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.78101,45.67393,-87.82468,45.65321), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.80508,45.70356,-92.86969,45.71514), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.86969,45.71514,-87.83305,45.72275), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.83305,45.72275,-92.84074,45.7294), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.84074,45.7294,-87.83305,45.72275), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.82601,45.73665,-92.84074,45.7294), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.87981,45.75484,-87.96697,45.76402), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.96697,45.76402,-87.87981,45.75484), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.04851,45.78255,-88.05701,45.78498), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.05701,45.78498,-88.04851,45.78255), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.7765,45.79001,-88.05701,45.78498), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-87.99588,45.79544,-88.10552,45.79884), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.10552,45.79884,-87.99588,45.79544), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.13507,45.82169,-92.75946,45.83534), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.75946,45.83534,-88.13507,45.82169), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.07394,45.87559,-92.72113,45.88381), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.72113,45.88381,-88.07394,45.87559), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.11535,45.92221,-88.11686,45.92281), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.11686,45.92281,-88.11535,45.92221), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.65613,45.92444,-88.11686,45.92281), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.58057,45.94625,-88.17801,45.94711), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.17801,45.94711,-92.58057,45.94625), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.30952,45.95937,-88.24631,45.96298), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.24631,45.96298,-88.30952,45.95937), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.54568,45.97012,-92.47276,45.97295), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.47276,45.97295,-92.54568,45.97012), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.40986,45.97969,-92.47276,45.97295), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.65776,45.98929,-88.61306,45.99063), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.61306,45.99063,-88.38018,45.99165), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.38018,45.99165,-88.61306,45.99063), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.44963,46.00225,-88.38018,45.99165), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.67913,46.01354,-88.68323,46.01447), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.68323,46.01447,-88.59386,46.01513), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.59386,46.01513,-92.35176,46.01569), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.35176,46.01569,-88.59386,46.01513), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.39268,46.01954,-88.52667,46.02082), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.52667,46.02082,-88.81195,46.02161), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.81195,46.02161,-88.52667,46.02082), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.73999,46.02731,-88.81195,46.02161), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.33824,46.05215,-88.93277,46.07211), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.93277,46.07211,-92.29403,46.07438), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29403,46.07438,-88.93277,46.07211), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-88.99122,46.09654,-92.29403,46.07438), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.09163,46.13851,-92.29383,46.15732), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29383,46.15732,-89.09163,46.13851), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.63842,46.2438,-92.29362,46.24404), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29362,46.24404,-89.63842,46.2438), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-89.92913,46.29992,-90.12049,46.33685), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.12049,46.33685,-89.92913,46.29992), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29276,46.41722,-90.15824,46.42049), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.15824,46.42049,-92.29276,46.41722), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29237,46.49559,-90.21487,46.49995), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.21487,46.49995,-92.29237,46.49559), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.28571,46.51885,-90.38723,46.53366), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.38723,46.53366,-90.28571,46.51885), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.33189,46.55328,-90.4376,46.56149), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.4376,46.56149,-90.41814,46.56609), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.41814,46.56609,-90.4376,46.56149), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.89831,46.58305,-90.56556,46.58489), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.56556,46.58489,-90.54858,46.58624), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.54858,46.58624,-90.92523,46.58749), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.92523,46.58749,-90.54858,46.58624), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.50591,46.58961,-90.92523,46.58749), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.95565,46.5925,-90.50591,46.58961), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.82903,46.61607,-90.93263,46.6173), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.93263,46.6173,-90.82903,46.61607), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.79478,46.62494,-90.93263,46.6173), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.66327,46.64533,-90.75529,46.64629), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.75529,46.64629,-90.66327,46.64533), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.91515,46.65841,-92.29219,46.66324), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.29219,46.66324,-92.20549,46.66474), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.20549,46.66474,-92.29219,46.66324), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.96189,46.68254,-91.82003,46.69018), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.82003,46.69018,-91.88696,46.69021), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.88696,46.69021,-91.82003,46.69018), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.73726,46.69227,-91.88696,46.69021), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.18309,46.69524,-90.73726,46.69227), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.8527,46.69958,-92.18309,46.69524), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.01529,46.70647,-92.05082,46.71052), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.05082,46.71052,-92.01529,46.70647), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.14334,46.7316,-92.10026,46.73445), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-92.10026,46.73445,-91.6455,46.73473), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.6455,46.73473,-92.10026,46.73445), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.88084,46.73996,-91.6455,46.73473), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.51108,46.75745,-91.55134,46.75748), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.55134,46.75748,-91.57429,46.75749), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.57429,46.75749,-91.55134,46.75748), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.86105,46.76563,-91.57429,46.75749), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.4118,46.78964,-91.3608,46.79814), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.3608,46.79814,-91.4118,46.78964), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.79894,46.82314,-91.31482,46.82683), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.31482,46.82683,-90.79894,46.82314), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.25671,46.83689,-91.1676,46.84476), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.1676,46.84476,-91.25671,46.83689), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.21165,46.86682,-91.13048,46.87001), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.13048,46.87001,-91.21165,46.86682), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.08736,46.87947,-91.13048,46.87001), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.74531,46.89425,-91.03452,46.90305), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-91.03452,46.90305,-90.74531,46.89425), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.98462,46.9256,-90.80628,46.93874), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.80628,46.93874,-90.98462,46.9256), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.85284,46.96256,-90.98938,46.98227), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.98938,46.98227,-90.92413,47.00189), mapfile, tile_dir, 0, 11, "wisconsin-wi")
	render_tiles((-90.92413,47.00189,-90.98938,46.98227), mapfile, tile_dir, 0, 11, "wisconsin-wi")