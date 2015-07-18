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
    # Region: New Hampshire
    # Region Name: NH

	render_tiles((-71.29421,42.69699,-71.35187,42.69815), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.35187,42.69815,-71.29421,42.69699), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.63621,42.70489,-71.74582,42.70729), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.74582,42.70729,-71.80542,42.70892), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.80542,42.70892,-71.74582,42.70729), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.89871,42.71147,-71.92882,42.71229), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.92882,42.71229,-71.89871,42.71147), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.08138,42.71646,-72.12453,42.71764), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.12453,42.71764,-72.08138,42.71646), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.20369,42.71982,-72.12453,42.71764), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.28297,42.72201,-72.20369,42.71982), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.41201,42.72557,-72.45126,42.72665), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.45126,42.72665,-72.45852,42.72685), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.45852,42.72685,-72.45126,42.72665), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.25561,42.73639,-71.25511,42.7364), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.25511,42.7364,-71.25561,42.73639), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.24541,42.73655,-71.25511,42.7364), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.1818,42.73759,-71.24541,42.73655), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.47762,42.76125,-71.1818,42.73759), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.1861,42.79069,-72.5396,42.80483), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.5396,42.80483,-71.0642,42.80629), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.0642,42.80629,-72.5396,42.80483), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.11674,42.81194,-71.1497,42.81549), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.1497,42.81549,-71.11674,42.81194), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.04871,42.83108,-71.1497,42.81549), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.55392,42.85809,-71.0312,42.85909), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.0312,42.85909,-72.55392,42.85809), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.55611,42.86625,-70.9665,42.86899), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.55611,42.86625,-70.9665,42.86899), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.9665,42.86899,-70.86475,42.87026), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.86475,42.87026,-70.9665,42.86899), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.8173,42.87229,-70.86475,42.87026), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.9308,42.88459,-70.81144,42.88861), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.81144,42.88861,-70.9308,42.88459), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.53147,42.89795,-70.81144,42.88861), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.79864,42.92429,-72.53147,42.89795), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.53219,42.95495,-72.4926,42.96765), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.4926,42.96765,-70.76522,42.97535), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.76522,42.97535,-72.4926,42.96765), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.44498,43.00442,-70.73548,43.0122), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.73548,43.0122,-72.44498,43.00442), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.45196,43.02052,-70.73548,43.0122), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.46225,43.04421,-70.70382,43.05983), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.70382,43.05983,-72.46225,43.04421), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.7564,43.07999,-72.43519,43.08662), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.43519,43.08662,-70.7564,43.07999), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.81955,43.12323,-70.8281,43.12909), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.8281,43.12909,-70.81955,43.12323), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.4518,43.15349,-72.45038,43.16127), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.45038,43.16127,-72.4518,43.15349), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.8248,43.17969,-70.82478,43.17976), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.82478,43.17976,-70.8248,43.17969), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.44056,43.21525,-70.81312,43.21725), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.81312,43.21725,-72.44056,43.21525), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.43366,43.23279,-70.81312,43.21725), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.42158,43.26344,-70.87259,43.27015), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.87259,43.27015,-72.42158,43.26344), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.40253,43.32038,-70.92395,43.32477), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.92395,43.32477,-72.40253,43.32038), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.41338,43.36274,-70.98434,43.37613), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.98434,43.37613,-72.41338,43.36274), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.39692,43.42892,-72.3969,43.429), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.3969,43.429,-72.39692,43.42892), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.96836,43.42928,-72.3969,43.429), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.96079,43.47409,-72.38089,43.49339), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.38089,43.49339,-70.95476,43.5098), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.95476,43.5098,-72.38089,43.49339), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.96379,43.54023,-70.97272,43.57026), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.97272,43.57026,-72.37944,43.57407), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.37944,43.57407,-70.97272,43.57026), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.33341,43.60572,-72.32952,43.60839), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.32952,43.60839,-72.33341,43.60572), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.98195,43.70096,-72.28481,43.72036), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.28481,43.72036,-70.98195,43.70096), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.22207,43.75983,-72.2115,43.77302), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.2115,43.77302,-72.22207,43.75983), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.98726,43.79297,-72.18333,43.80818), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.18333,43.80818,-70.98726,43.79297), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-70.98993,43.83924,-72.18333,43.80818), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.16978,43.87343,-70.98993,43.83924), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.10588,43.94937,-72.11671,43.99195), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.11671,43.99195,-72.07994,44.03), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.07994,44.03,-72.07955,44.0304), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.07955,44.0304,-72.07994,44.03), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.07549,44.03461,-72.07955,44.0304), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.00137,44.09293,-72.03688,44.10312), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.03688,44.10312,-71.00137,44.09293), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.05383,44.15982,-72.06134,44.18495), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.06134,44.18495,-72.05383,44.15982), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.05399,44.24693,-71.00874,44.25883), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.00874,44.25883,-72.05399,44.24693), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.01026,44.28477,-72.0463,44.29198), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.0463,44.29198,-71.01026,44.28477), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.01127,44.30185,-72.0463,44.29198), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-72.00231,44.32487,-71.87586,44.33737), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.87586,44.33737,-71.94516,44.33774), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.94516,44.33774,-71.87586,44.33737), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.01358,44.34088,-71.94516,44.33774), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.83766,44.3478,-71.81884,44.35294), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.81884,44.35294,-71.83766,44.3478), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.77861,44.3998,-71.76221,44.40381), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.76221,44.40381,-71.77861,44.3998), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.69092,44.42123,-71.76221,44.40381), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.01947,44.44042,-71.69092,44.42123), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.64655,44.46887,-71.01947,44.44042), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.02299,44.50006,-71.57997,44.50178), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.57997,44.50178,-71.02299,44.50006), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.58808,44.54785,-71.54492,44.57928), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.54492,44.57928,-71.58808,44.54785), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.55172,44.6276,-71.58457,44.66535), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.58457,44.66535,-71.55172,44.6276), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.03671,44.7365,-71.62691,44.74722), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.62691,44.74722,-71.03671,44.7365), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.5704,44.80528,-71.62691,44.74722), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.52239,44.88081,-71.4944,44.91184), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.4944,44.91184,-71.52239,44.88081), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.53161,44.97602,-71.05786,45.00005), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.05786,45.00005,-71.50109,45.01338), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.50109,45.01338,-71.05786,45.00005), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.4984,45.06963,-71.44868,45.109), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.44868,45.109,-71.4984,45.06963), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.41906,45.17049,-71.40564,45.19814), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.40564,45.19814,-71.41906,45.17049), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.43855,45.239,-71.18259,45.24107), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.18259,45.24107,-71.13943,45.24296), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.13943,45.24296,-71.18259,45.24107), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.2445,45.26814,-71.36066,45.26984), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.36066,45.26984,-71.2445,45.26814), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.10935,45.28222,-71.36066,45.26984), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.28368,45.30198,-71.08392,45.30545), mapfile, tile_dir, 0, 11, "new hampshire-nh")
	render_tiles((-71.08392,45.30545,-71.28368,45.30198), mapfile, tile_dir, 0, 11, "new hampshire-nh")