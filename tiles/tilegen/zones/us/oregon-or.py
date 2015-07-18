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
    # Region: Oregon
    # Region Name: OR

	render_tiles((-118.77587,41.99269,-119.20828,41.99318), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.20828,41.99318,-121.0352,41.99332), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.0352,41.99332,-119.20828,41.99318), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.87948,41.99348,-121.0352,41.99332), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.69222,41.99368,-120.50107,41.99379), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.50107,41.99379,-119.32418,41.99388), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.00102,41.99379,-119.32418,41.99388), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.32418,41.99388,-120.50107,41.99379), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.36018,41.99409,-119.32418,41.99388), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.99917,41.99454,-120.18156,41.99459), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.18156,41.99459,-119.99917,41.99454), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.657,41.99514,-118.501,41.99545), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.501,41.99545,-123.82144,41.99562), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.82144,41.99562,-118.501,41.99545), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.00119,41.99615,-119.72573,41.9963), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.72573,41.9963,-124.00119,41.99615), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.19719,41.997,-121.43961,41.99708), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.43961,41.99708,-118.19719,41.997), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.44754,41.99719,-121.43961,41.99708), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.2511,41.99757,-121.44754,41.99719), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.87347,41.99834,-124.21161,41.99846), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.21161,41.99846,-117.62373,41.99847), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.62373,41.99847,-124.21161,41.99846), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.34756,41.99911,-123.51911,41.99917), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.51911,41.99917,-123.34756,41.99911), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.40361,41.99929,-123.51911,41.99917), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.0262,41.99989,-121.67535,42.00035), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.67535,42.00035,-117.1978,42.00038), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.1978,42.00038,-121.67535,42.00035), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.43477,42.00164,-117.1978,42.00038), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.04525,42.00305,-121.84671,42.00307), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.84671,42.00307,-123.04525,42.00305), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.80008,42.00407,-123.23073,42.00498), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.23073,42.00498,-122.10192,42.00577), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.10192,42.00577,-123.23073,42.00498), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.28953,42.00776,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.28953,42.00776,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.50114,42.00846,-122.28953,42.00776), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.14596,42.00925,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.27046,42.04555,-124.31429,42.06786), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.31429,42.06786,-124.27046,42.04555), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.35154,42.1298,-124.36101,42.18075), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.36101,42.18075,-124.38363,42.22716), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.38363,42.22716,-124.41098,42.25055), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.41098,42.25055,-124.38363,42.22716), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.41056,42.30743,-124.42555,42.35187), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.42555,42.35187,-117.02655,42.37856), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02655,42.37856,-124.42555,42.35187), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.43511,42.44016,-117.02655,42.37856), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.39907,42.53993,-124.40092,42.59752), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.40092,42.59752,-124.39907,42.53993), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.41312,42.65793,-124.45074,42.6758), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.45074,42.6758,-124.44842,42.68991), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.44842,42.68991,-124.45074,42.6758), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.51002,42.73475,-124.44842,42.68991), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02625,42.80745,-124.55244,42.84057), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.55244,42.84057,-117.02625,42.80745), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.55244,42.84057,-117.02625,42.80745), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.48094,42.9515,-124.47964,42.95497), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.47964,42.95497,-124.48094,42.9515), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02665,43.02513,-124.4362,43.07131), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.4362,43.07131,-117.02665,43.02513), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.41139,43.15985,-124.39561,43.22391), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.39561,43.22391,-124.38246,43.27017), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.38246,43.27017,-124.4004,43.30212), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.4004,43.30212,-124.38246,43.27017), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.35333,43.34267,-124.4004,43.30212), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.2869,43.4363,-124.35333,43.34267), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.23353,43.55713,-117.02689,43.59603), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02689,43.59603,-124.21922,43.61032), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.21922,43.61032,-117.02689,43.59603), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02566,43.68041,-124.19346,43.70609), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.19346,43.70609,-117.02566,43.68041), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.02358,43.82381,-124.16025,43.8635), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.16025,43.8635,-116.98555,43.88118), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.98555,43.88118,-116.97602,43.89555), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.97602,43.89555,-116.98555,43.88118), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.15027,43.91085,-116.97602,43.89555), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.95987,43.98293,-116.93734,44.02938), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.93734,44.02938,-116.95987,43.98293), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.97735,44.08536,-124.12241,44.10444), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.12241,44.10444,-116.97735,44.08536), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.89791,44.15262,-116.89593,44.1543), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.89593,44.1543,-116.89791,44.15262), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.90275,44.17947,-116.9655,44.19413), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.9655,44.19413,-116.90275,44.17947), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.11105,44.23507,-116.97196,44.23568), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.97196,44.23568,-124.11105,44.23507), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.05935,44.23724,-116.97196,44.23568), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.17034,44.25889,-124.11438,44.2763), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.11438,44.2763,-117.12104,44.27759), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.12104,44.27759,-124.11438,44.2763), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.1152,44.28649,-117.21697,44.28836), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.21697,44.28836,-124.1152,44.28649), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.21201,44.29643,-117.21697,44.28836), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.1922,44.32863,-117.21691,44.36016), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.21691,44.36016,-117.24303,44.39097), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.24303,44.39097,-124.0844,44.41561), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.0844,44.41561,-117.21507,44.42716), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.21507,44.42716,-124.0844,44.41561), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.22593,44.47939,-124.0836,44.50112), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.0836,44.50112,-117.22593,44.47939), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.16719,44.52343,-124.0836,44.50112), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.14293,44.55724,-117.16719,44.52343), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.06501,44.6325,-117.09497,44.65201), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.09497,44.65201,-124.06501,44.6325), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.06341,44.70318,-117.06227,44.72714), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.06227,44.72714,-124.06341,44.70318), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.0138,44.75684,-117.06227,44.72714), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.9318,44.78718,-124.07407,44.79811), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.07407,44.79811,-116.9318,44.78718), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.06316,44.83533,-116.8893,44.84052), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.8893,44.84052,-124.06316,44.83533), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.86534,44.8706,-116.8893,44.84052), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.83363,44.92898,-124.02383,44.94983), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.02383,44.94983,-116.83363,44.92898), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.85831,44.97876,-124.02383,44.94983), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.84131,45.03091,-124.01011,45.04489), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.01011,45.04489,-124.00977,45.04727), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.00977,45.04727,-124.01011,45.04489), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.78371,45.07697,-116.78279,45.07815), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.78279,45.07815,-116.78371,45.07697), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.75464,45.11397,-123.97543,45.14548), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.97543,45.14548,-116.75464,45.11397), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.97292,45.21678,-116.69605,45.25468), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.69605,45.25468,-116.69083,45.26922), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.69083,45.26922,-123.96289,45.28022), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96289,45.28022,-116.69083,45.26922), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.67465,45.31434,-123.97972,45.34772), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.97972,45.34772,-116.67465,45.31434), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96056,45.43078,-116.5882,45.44292), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.5882,45.44292,-123.96056,45.43078), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.97654,45.48973,-116.5882,45.44292), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.26263,45.54432,-122.3315,45.54824), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.3315,45.54824,-122.24889,45.55013), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.24889,45.55013,-122.3315,45.54824), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.43867,45.56359,-123.94756,45.56488), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.94756,45.56488,-122.43867,45.56359), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.50276,45.56661,-123.94756,45.56488), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.3803,45.57594,-122.1837,45.5777), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.1837,45.5777,-122.3803,45.57594), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.49226,45.58328,-122.10168,45.58352), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.10168,45.58352,-122.49226,45.58328), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.18384,45.60644,-122.64391,45.60974), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.64391,45.60974,-121.18384,45.60644), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.4635,45.61579,-122.00369,45.61593), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.00369,45.61593,-116.4635,45.61579), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.1222,45.61607,-122.00369,45.61593), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.89558,45.64295,-122.73811,45.64414), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.73811,45.64414,-121.95184,45.64495), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.95184,45.64495,-122.73811,45.64414), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.08493,45.64789,-120.914,45.64808), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.914,45.64808,-121.08493,45.64789), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.92396,45.65428,-120.94398,45.65645), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.94398,45.65645,-121.92396,45.65428), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.93901,45.66192,-121.90086,45.66201), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.90086,45.66201,-123.93901,45.66192), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.75644,45.66242,-121.90086,45.66201), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.21578,45.67124,-120.85567,45.67155), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.85567,45.67155,-121.21578,45.67124), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.77451,45.68044,-116.52827,45.68147), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.52827,45.68147,-122.77451,45.68044), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.86717,45.69328,-121.42359,45.69399), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.42359,45.69399,-121.7351,45.69404), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.7351,45.69404,-121.42359,45.69399), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.44052,45.69902,-120.40396,45.69925), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.40396,45.69925,-121.44052,45.69902), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.4881,45.69991,-120.50586,45.70005), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.50586,45.70005,-120.4881,45.69991), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.33777,45.70495,-121.66836,45.70508), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.66836,45.70508,-121.33777,45.70495), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.8113,45.70676,-121.66836,45.70508), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.93945,45.7088,-121.8113,45.70676), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.68937,45.71585,-120.28216,45.72125), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.28216,45.72125,-121.52275,45.72346), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.52275,45.72346,-120.28216,45.72125), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.21075,45.72595,-121.53311,45.72654), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-121.53311,45.72654,-120.21075,45.72595), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.76651,45.72868,-121.53311,45.72654), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.5357,45.73423,-120.65252,45.73617), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.65252,45.73617,-116.5357,45.73423), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.63497,45.74585,-120.59117,45.74655), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.59117,45.74655,-120.63497,45.74585), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96856,45.75702,-122.76145,45.75916), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.76145,45.75916,-123.96856,45.75702), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.14135,45.77315,-116.593,45.77854), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.593,45.77854,-116.66534,45.782), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.66534,45.782,-123.96627,45.78323), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96627,45.78323,-116.66534,45.782), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-120.07015,45.78515,-123.96627,45.78323), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.79561,45.81,-119.99951,45.81168), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.99951,45.81168,-122.79561,45.81), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.96574,45.82437,-116.73627,45.82618), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.73627,45.82618,-119.96574,45.82437), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96154,45.8371,-119.8681,45.83823), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.8681,45.83823,-123.96154,45.8371), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.78752,45.8402,-119.8681,45.83823), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.80266,45.84753,-122.78809,45.85101), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.78809,45.85101,-116.7992,45.85105), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.7992,45.85105,-122.78809,45.85101), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.66988,45.85687,-116.7992,45.85105), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.78503,45.8677,-119.66988,45.85687), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.48783,45.90631,-116.8598,45.90726), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.8598,45.90726,-123.96763,45.90781), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.96763,45.90781,-116.8598,45.90726), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.81151,45.91273,-119.43208,45.91322), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.43208,45.91322,-122.81151,45.91273), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.60055,45.91958,-119.3644,45.92161), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.3644,45.92161,-119.60055,45.91958), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.57158,45.92546,-119.3644,45.92161), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.12612,45.93286,-119.25715,45.93993), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.25715,45.93993,-123.9937,45.94643), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.9937,45.94643,-119.25715,45.93993), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-119.06146,45.95853,-116.88684,45.95862), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.88684,45.95862,-119.06146,45.95853), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.814,45.96098,-116.88684,45.95862), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.93747,45.97731,-122.814,45.96098), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-116.91599,45.99541,-117.35393,45.99635), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.35393,45.99635,-116.91599,45.99541), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.48014,45.99757,-117.60316,45.99876), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.60316,45.99876,-118.98713,45.99986), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.98713,45.99986,-117.71785,45.99987), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.71785,45.99987,-118.98713,45.99986), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.97776,46.00017,-117.99691,46.00019), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-117.99691,46.00019,-117.97776,46.00017), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.36779,46.00062,-118.60679,46.00086), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.60679,46.00086,-118.67787,46.00094), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-118.67787,46.00094,-118.60679,46.00086), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.85616,46.01447,-118.67787,46.00094), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.92933,46.04198,-122.85616,46.01447), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.90412,46.08373,-122.96268,46.10482), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-122.96268,46.10482,-122.90412,46.08373), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.00423,46.13382,-123.95919,46.14168), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.95919,46.14168,-123.28017,46.14484), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.28017,46.14484,-123.36364,46.14624), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.36364,46.14624,-123.37143,46.14637), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.37143,46.14637,-123.36364,46.14624), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.21249,46.1711,-123.91241,46.17945), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.91241,46.17945,-123.43085,46.18183), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.43085,46.18183,-123.91241,46.17945), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.1159,46.18527,-123.43085,46.18183), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.16641,46.18897,-123.71815,46.18899), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.71815,46.18899,-123.16641,46.18897), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.8388,46.19221,-123.71815,46.18899), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-124.04113,46.19767,-123.8388,46.19221), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.75759,46.213,-123.66087,46.2163), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.66087,46.2163,-123.75759,46.213), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.58621,46.22865,-123.42763,46.22935), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.42763,46.22935,-123.58621,46.22865), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.99805,46.23533,-123.42763,46.22935), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.54766,46.25911,-123.47964,46.26913), mapfile, tile_dir, 0, 11, "oregon-or")
	render_tiles((-123.47964,46.26913,-123.54766,46.25911), mapfile, tile_dir, 0, 11, "oregon-or")