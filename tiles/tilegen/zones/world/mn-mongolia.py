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
    # Region: MN
    # Region Name: Mongolia

	render_tiles((105.0122,41.58138,104.9305,41.65192), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.9305,41.65192,104.5241,41.66387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.5241,41.66387,104.9305,41.65192), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((105.2269,41.74999,103.8494,41.80248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.8494,41.80248,104.0611,41.80331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.0611,41.80331,103.8494,41.80248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((105.4677,41.83138,104.0611,41.80331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.5266,41.8772,103.4164,41.88721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.4164,41.88721,104.5266,41.8772), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.1225,42.07748,102.4425,42.15109), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.4425,42.15109,102.7125,42.16137), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.7125,42.16137,102.4425,42.15109), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.0772,42.23332,106.7819,42.29554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.7819,42.29554,107.0028,42.31332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.0028,42.31332,106.7819,42.29554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.2625,42.35999,108.8455,42.39999), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((108.8455,42.39999,107.5711,42.41026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.5711,42.41026,107.2772,42.41054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.2772,42.41054,107.5711,42.41026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.3136,42.42999,108.5278,42.44221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((108.5278,42.44221,109.4377,42.45332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.4377,42.45332,109,42.45832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109,42.45832,109.4377,42.45332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.4719,42.46609,109,42.45832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.5447,42.47387,107.4719,42.46609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((101.8147,42.50971,109.5447,42.47387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.6955,42.55832,99.49858,42.57082), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((99.49858,42.57082,109.6955,42.55832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.53026,42.62887,100.255,42.6411), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.255,42.6411,109.9277,42.64137), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.9277,42.64137,100.255,42.6411), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.1075,42.64554,100.0347,42.64832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.0347,42.64832,110.1075,42.64554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.1344,42.6736,100.8355,42.67804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.8355,42.67804,110.1344,42.6736), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.315,42.69109,100.8355,42.67804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.38303,42.73109,100.315,42.69109), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.4405,42.77776,97.17163,42.79749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.17163,42.79749,110.4405,42.77776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.4719,42.84609,97.17163,42.79749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.35692,42.90693,110.4719,42.84609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.6736,43.00332,110.7253,43.07887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.7253,43.07887,110.6736,43.00332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.88135,43.27915,110.9897,43.31693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.9897,43.31693,95.88135,43.27915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.84552,43.44137,111.4355,43.48943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.4355,43.48943,111.5858,43.49721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.5858,43.49721,111.4355,43.48943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.7839,43.67249,111.9583,43.69221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.9583,43.69221,111.7839,43.67249), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.978,43.76305,111.9583,43.69221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.8794,43.93887,95.53996,43.99276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.53996,43.99276,95.33609,44.02082), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.33609,44.02082,95.53996,43.99276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.585,44.15082,95.35025,44.17776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.35025,44.17776,111.585,44.15082), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.02052,44.25694,95.42329,44.27832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.42329,44.27832,95.41109,44.29639), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.41109,44.29639,95.42329,44.27832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.4264,44.33276,94.72441,44.34915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.72441,44.34915,111.4264,44.33276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.4269,44.41276,94.59941,44.45304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.59941,44.45304,111.4269,44.41276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.36481,44.52052,111.5611,44.57777), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.5611,44.57777,94.31804,44.58749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.31804,44.58749,111.5611,44.57777), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.568,44.67915,94.12718,44.70026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.12718,44.70026,111.568,44.67915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.638,44.74526,94.12718,44.70026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.1261,44.79777,113.638,44.74526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.9169,44.92276,112.6075,44.92638), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((112.6075,44.92638,113.9169,44.92276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.0741,44.93803,112.6075,44.92638), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.53081,44.9622,114.0741,44.93803), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.785,45.00054,93.09525,45.01027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.09525,45.01027,92.3597,45.01276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.3597,45.01276,93.09525,45.01027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.68108,45.0686,92.09247,45.07943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.09247,45.07943,112.4272,45.08054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((112.4272,45.08054,92.09247,45.07943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.9808,45.09165,112.4272,45.08054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.38303,45.11859,111.9808,45.09165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.23053,45.15109,91.45358,45.15804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.45358,45.15804,91.23053,45.15109), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.93941,45.19304,90.88164,45.20415), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.88164,45.20415,114.4622,45.21165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.4622,45.21165,91.14165,45.21276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.14165,45.21276,114.4622,45.21165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.01192,45.22387,91.14165,45.21276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.938,45.38276,114.5452,45.38943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.5452,45.38943,90.79442,45.39471), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.79442,45.39471,114.5452,45.38943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.7361,45.44221,115.7019,45.4586), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.7019,45.4586,114.7361,45.44221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.67468,45.49499,115.7019,45.4586), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.0314,45.68554,116.15,45.69331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.15,45.69331,116.0314,45.68554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.71942,45.73554,116.15,45.69331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.2722,45.78943,116.2805,45.82332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.2805,45.82332,116.2722,45.78943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.2341,45.88776,116.2805,45.82332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.02109,46.00832,91.02969,46.04166), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.02969,46.04166,91.02109,46.00832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.363,46.08887,91.02969,46.04166), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.97247,46.17776,116.363,46.08887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.5855,46.29582,90.92302,46.31554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.92302,46.31554,116.5855,46.29582), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.093,46.35943,117.3708,46.36443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.3708,46.36443,117.093,46.35943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.8416,46.3936,117.3708,46.36443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.7166,46.51388,117.8441,46.53443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.8441,46.53443,117.6327,46.55109), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.6327,46.55109,117.8441,46.53443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.0722,46.57332,117.4211,46.57832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.4211,46.57832,91.0722,46.57332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.6914,46.59776,117.9161,46.61027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.9161,46.61027,117.5989,46.61193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.5989,46.61193,117.9161,46.61027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.363,46.61443,117.5989,46.61193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.64,46.62748,119.7794,46.62859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.7794,46.62859,119.64,46.62748), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.0616,46.67776,118.7733,46.68387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.7733,46.68387,119.0616,46.67776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.5536,46.69082,118.7733,46.68387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.928,46.71027,118.9177,46.72054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.9177,46.72054,119.928,46.71027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.3102,46.73693,91.03441,46.74221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.03441,46.74221,118.3102,46.73693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.9905,46.75027,91.03441,46.74221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.8441,46.76554,118.8869,46.76971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.8869,46.76971,118.8441,46.76554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.94385,46.82887,90.95886,46.88304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.95886,46.88304,119.9255,46.90887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.9255,46.90887,90.95886,46.88304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.85719,46.98859,119.8014,46.99387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.8014,46.99387,90.85719,46.98859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.76109,46.99971,119.8014,46.99387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.7352,47.15554,119.6141,47.24804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.6141,47.24804,90.48996,47.32193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.48996,47.32193,119.4791,47.32999), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.4791,47.32999,90.48996,47.32193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.3086,47.42443,119.3464,47.47387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.3464,47.47387,90.46581,47.50027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.46581,47.50027,119.3464,47.47387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.1494,47.53387,90.40775,47.54305), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.40775,47.54305,119.1494,47.53387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.35747,47.64332,117.3903,47.66054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.3903,47.66054,119.125,47.66499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((119.125,47.66499,117.3278,47.66915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.3278,47.66915,119.125,47.66499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.35692,47.67443,117.3278,47.66915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.9727,47.69193,90.35692,47.67443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.9191,47.69193,90.35692,47.67443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.13414,47.73749,117.5124,47.76331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.5124,47.76331,118.7672,47.77276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.7672,47.77276,117.5124,47.76331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.08331,47.79749,117.1111,47.8136), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.1111,47.8136,116.1155,47.82193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.1155,47.82193,89.78192,47.82832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.78192,47.82832,116.1155,47.82193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.93886,47.83637,116.4703,47.83915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.4703,47.83915,89.93886,47.83637), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.09164,47.86526,116.2583,47.87859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.2583,47.87859,116.8747,47.88804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.8747,47.88804,90.06747,47.88943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.06747,47.88943,116.8747,47.88804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.97664,47.89193,90.06747,47.88943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.6683,47.91304,115.5922,47.91943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.5922,47.91943,89.6683,47.91304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.7638,47.97471,89.2258,47.98054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.2258,47.98054,117.7638,47.97471), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((118.5577,47.99526,89.07747,47.99554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.07747,47.99554,118.5577,47.99526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.9553,48.00721,117.808,48.01193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((117.808,48.01193,117.9553,48.00721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.59081,48.02971,89.53053,48.04137), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.53053,48.04137,89.59081,48.02971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.83386,48.11027,88.9333,48.11526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.9333,48.11526,88.83386,48.11027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.54,48.13582,88.9333,48.11526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.60524,48.21776,115.8358,48.25249), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.8358,48.25249,88.60524,48.21776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.57414,48.36276,115.8358,48.25249), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.8111,48.52054,87.9733,48.57693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.9733,48.57693,87.96942,48.60749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.96942,48.60749,88.02081,48.63332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.02081,48.63332,87.96942,48.60749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.08858,48.70638,88.03914,48.74721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.03914,48.74721,87.90553,48.77304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.90553,48.77304,88.03914,48.74721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.0841,48.81582,87.90553,48.77304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.0564,48.86332,87.75941,48.88165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.75941,48.88165,116.0564,48.86332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.78136,48.92748,87.87302,48.95193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.87302,48.95193,87.78136,48.92748), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.89941,49.00221,87.87302,48.95193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.7003,49.14221,110.6161,49.1572), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.6161,49.1572,87.83997,49.17184), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.83997,49.17184,87.97942,49.17554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((87.97942,49.17554,87.83997,49.17184), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.9491,49.18887,110.3186,49.19553), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.3186,49.19553,110.9491,49.18887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.523,49.23943,110.395,49.25388), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((110.395,49.25388,109.523,49.23943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.16219,49.27221,110.395,49.25388), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((109.3686,49.32777,108.5477,49.33832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((108.5477,49.33832,88.15636,49.34165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.15636,49.34165,108.5477,49.33832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((111.3522,49.36693,88.13191,49.36832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.13191,49.36832,111.3522,49.36693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((108.455,49.39249,88.13191,49.36832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((112.135,49.43776,88.84692,49.44165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.84692,49.44165,112.135,49.43776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.90663,49.44609,88.19662,49.44942), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.19662,49.44942,88.90663,49.44609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.98941,49.46665,88.56886,49.47581), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.56886,49.47581,88.40775,49.48387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.40775,49.48387,88.56886,49.47581), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((112.7311,49.49776,88.6508,49.50054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.6508,49.50054,112.7311,49.49776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.18192,49.50694,88.6508,49.50054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.87968,49.5236,89.18192,49.50694), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((112.4833,49.54027,89.23497,49.54054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.23497,49.54054,112.4833,49.54027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.88748,49.5436,88.92885,49.54638), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((88.92885,49.54638,88.88748,49.5436), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.37219,49.57971,89.34636,49.59693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.34636,49.59693,113.0908,49.59859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.0908,49.59859,89.34636,49.59693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.19746,49.6111,113.0908,49.59859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.22664,49.63832,89.19746,49.6111), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.9475,49.68332,89.71803,49.71748), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.71803,49.71748,97.30275,49.73081), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.30275,49.73081,89.71803,49.71748), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.16942,49.76166,89.71469,49.76998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.71469,49.76998,116.6725,49.77499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.6725,49.77499,89.71469,49.76998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.1811,49.78749,89.65442,49.78999), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.48413,49.78749,89.65442,49.78999), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.65442,49.78999,113.1811,49.78749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.7088,49.82819,97.59108,49.84721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.59108,49.84721,96.59247,49.86499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.59247,49.86499,115.7311,49.88081), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.7311,49.88081,96.37914,49.8936), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.37914,49.8936,96.99692,49.89415), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.99692,49.89415,95.52081,49.89443), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.52081,49.89443,96.99692,49.89415), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.63997,49.89915,115.3955,49.90165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.3955,49.90165,89.63997,49.89915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.56441,49.91666,96.7133,49.91998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.7133,49.91998,97.5847,49.92027), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.5847,49.92027,96.7133,49.91998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.51469,49.93027,97.86386,49.93276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.86386,49.93276,116.6086,49.93387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.6086,49.93387,97.86386,49.93276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.70137,49.93498,116.6086,49.93387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.95276,49.94582,107.9782,49.94739), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.9782,49.94739,95.95276,49.94582), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.27469,49.9536,95.42247,49.95609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.42247,49.95609,96.27469,49.9536), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.06607,49.96193,95.42247,49.95609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.77776,49.96887,89.97746,49.96998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((89.97746,49.96998,97.77776,49.96887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.02109,49.99638,107.2472,50.00499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.2472,50.00499,96.06525,50.00582), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((96.06525,50.00582,107.2472,50.00499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.105,50.01221,113.5861,50.01638), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((113.5861,50.01638,116.105,50.01221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.88748,50.0211,95.8172,50.02193), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((95.8172,50.02193,95.88748,50.0211), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((116.2358,50.02804,94.62274,50.02998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.62274,50.02998,116.2358,50.02804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((115.1575,50.04054,98.10719,50.04887), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.10719,50.04887,94.96747,50.05693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.96747,50.05693,90.02803,50.06415), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.02803,50.06415,94.96747,50.05693), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.0783,50.08498,90.07191,50.09054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.07191,50.09054,107.0783,50.08498), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.6405,50.1386,104.0633,50.14165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.0633,50.14165,103.7291,50.14249), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.7291,50.14249,104.0633,50.14165), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.2503,50.17638,103.7955,50.19498), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.7955,50.19498,103.3322,50.19637), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.3322,50.19637,103.7955,50.19498), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((107.0055,50.2011,103.3322,50.19637), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.4366,50.20915,107.0055,50.2011), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.2819,50.21804,90.69858,50.21943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.69858,50.21943,103.2819,50.21804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.38387,50.2247,114.8541,50.22832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.8541,50.22832,94.38387,50.2247), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.4633,50.24332,114.8541,50.22832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.2094,50.25971,114.6655,50.26471), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.6655,50.26471,114.2094,50.25971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((114.313,50.28416,104.3922,50.28749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.3922,50.28749,114.313,50.28416), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.28969,50.29388,106.2628,50.29749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.2628,50.29749,98.28969,50.29388), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((103.2455,50.30276,106.2628,50.29749), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.7536,50.32193,103.2455,50.30276), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.0703,50.3436,106.5469,50.34609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.5469,50.34609,106.0703,50.3436), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((106.0639,50.37776,104.9603,50.38582), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((104.9603,50.38582,106.0639,50.37776), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((105.1322,50.39832,90.97969,50.40998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((90.97969,50.40998,105.9891,50.41054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((105.9891,50.41054,90.97969,50.40998), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.613,50.4136,105.9891,50.41054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.43358,50.46609,91.29413,50.47554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.29413,50.47554,105.3083,50.48081), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((105.3083,50.48081,91.29413,50.47554), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.32637,50.50416,102.5366,50.51305), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.5366,50.51305,98.32637,50.50416), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.46774,50.52693,102.5366,50.51305), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.30246,50.54415,94.29247,50.56026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.29247,50.56026,102.3278,50.56971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.3278,50.56971,94.21886,50.57777), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((94.21886,50.57777,93.72691,50.57971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.72691,50.57971,94.21886,50.57777), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.32191,50.6036,93.0197,50.61304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.0197,50.61304,98.07915,50.62026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.07915,50.62026,93.44885,50.62248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((93.44885,50.62248,98.07915,50.62026), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.99799,50.63792,102.2958,50.64804), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.2958,50.64804,92.99799,50.63792), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.97697,50.66203,92.97052,50.66943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.97052,50.66943,91.72386,50.67221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.72386,50.67221,92.97052,50.66943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.69885,50.68304,92.64053,50.68832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.64053,50.68832,92.15192,50.69221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.15192,50.69221,92.64053,50.68832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.3558,50.70971,91.8847,50.71304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((91.8847,50.71304,92.76469,50.71609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.76469,50.71609,91.8847,50.71304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.25832,50.72387,92.76469,50.71609), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.3441,50.74832,92.99913,50.75138), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.99913,50.75138,102.3441,50.74832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.96387,50.77832,92.98663,50.78526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.98663,50.78526,92.79219,50.78665), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.79219,50.78665,102.2678,50.78721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.2678,50.78721,92.79219,50.78665), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.31607,50.78832,102.2678,50.78721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.92885,50.79999,92.31607,50.78832), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.2297,50.84943,98.01581,50.85054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.01581,50.85054,102.2297,50.84943), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.32692,50.85499,98.01581,50.85054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((92.39331,50.8747,102.265,50.89332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.265,50.89332,92.39331,50.8747), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.8683,50.93748,102.265,50.89332), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.82776,51.0011,97.8683,50.93748), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.95859,51.22331,102.1541,51.24499), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.1541,51.24499,97.95859,51.22331), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.225,51.30637,97.93636,51.30721), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((97.93636,51.30721,102.225,51.30637), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.2233,51.32971,102.1844,51.34526), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((102.1844,51.34526,102.2233,51.32971), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((101.4044,51.45138,98.05692,51.45248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.22775,51.45138,98.05692,51.45248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.05692,51.45248,101.4044,51.45138), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((101.3394,51.46859,98.05692,51.45248), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((101.5433,51.48693,101.3394,51.46859), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.30969,51.6947,100.6702,51.7036), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.6702,51.7036,98.30969,51.6947), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.36942,51.73081,100.2239,51.73304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((100.2239,51.73304,98.36942,51.73081), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((99.91969,51.76054,98.63164,51.78221), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.63164,51.78221,99.91969,51.76054), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.72607,51.84332,99.72191,51.89304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((99.72191,51.89304,99.5172,51.91304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((99.5172,51.91304,99.72191,51.89304), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((99.11996,52.03387,98.86942,52.0386), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.86942,52.0386,99.11996,52.03387), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.8783,52.10915,98.93456,52.14201), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.93456,52.14201,98.8783,52.10915), mapfile, tile_dir, 0, 11, "mn-mongolia")
	render_tiles((98.93456,52.14201,98.8783,52.10915), mapfile, tile_dir, 0, 11, "mn-mongolia")