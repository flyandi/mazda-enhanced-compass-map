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
    # Region: California
    # Region Name: CA

	render_tiles((-118.32524,33.29908,-118.37477,33.32007), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.37477,33.32007,-118.46537,33.32606), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.46537,33.32606,-118.37477,33.32007), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.28626,33.35146,-118.48261,33.36991), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.48261,33.36991,-118.28626,33.35146), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.37032,33.40929,-118.56344,33.43438), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.56344,33.43438,-118.37032,33.40929), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.59397,33.4672,-118.48479,33.48748), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.59397,33.4672,-118.48479,33.48748), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.48479,33.48748,-118.59397,33.4672), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.42563,32.8006,-118.3535,32.82196), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.3535,32.82196,-118.42563,32.8006), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.48791,32.84459,-118.3535,32.82196), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.44677,32.89542,-118.58151,32.93167), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.58151,32.93167,-118.44677,32.89542), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.54007,32.98093,-118.64158,33.01713), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.64158,33.01713,-118.59403,33.03595), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.59403,33.03595,-118.64158,33.01713), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.59403,33.03595,-118.64158,33.01713), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.46473,33.21543,-119.42956,33.22817), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.42956,33.22817,-119.54587,33.23341), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.54587,33.23341,-119.42956,33.22817), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.42717,33.26602,-119.57894,33.27863), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.57894,33.27863,-119.42717,33.26602), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.57894,33.27863,-119.42717,33.26602), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.51049,33.30727,-119.57894,33.27863), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.72121,33.95958,-119.79594,33.96293), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.79594,33.96293,-119.72121,33.95958), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.87336,33.98038,-119.66283,33.98589), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.66283,33.98589,-119.87336,33.98038), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.39159,33.99464,-119.48772,33.99652), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.48772,33.99652,-119.55447,33.99782), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.55447,33.99782,-119.48772,33.99652), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.36307,34.00055,-119.55447,33.99782), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.87692,34.02353,-119.36307,34.00055), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.73947,34.0493,-119.36421,34.05079), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.36421,34.05079,-119.73947,34.0493), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.5667,34.05345,-119.47074,34.054), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.47074,34.054,-119.44265,34.05416), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.44265,34.05416,-119.47074,34.054), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.91622,34.05835,-119.44265,34.05416), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.91622,34.05835,-119.44265,34.05416), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.8573,34.0713,-119.91622,34.05835), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.12182,33.89571,-120.04968,33.91456), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.04968,33.91456,-120.17905,33.92799), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.17905,33.92799,-120.04968,33.91456), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.97369,33.94248,-120.20009,33.9569), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.20009,33.9569,-119.97369,33.94248), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.98432,33.98395,-120.36484,33.99178), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.36484,33.99178,-119.98432,33.98395), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.13585,34.02609,-120.45413,34.02808), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.45413,34.02808,-120.13585,34.02609), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.05511,34.03773,-120.45413,34.02808), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.24248,34.05717,-120.36828,34.07647), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.36828,34.07647,-120.24248,34.05717), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.36828,34.07647,-120.24248,34.05717), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.12486,32.53416,-116.85715,32.55746), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.85715,32.55746,-116.62705,32.57626), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.62705,32.57626,-116.54064,32.58375), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.54064,32.58375,-117.13204,32.5856), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.13204,32.5856,-116.54064,32.58375), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.10618,32.61858,-117.13666,32.61875), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.13666,32.61875,-116.10618,32.61858), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.04662,32.62335,-117.13666,32.61875), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.46516,32.6671,-117.24607,32.66935), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.24607,32.66935,-115.46516,32.6671), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.16887,32.67195,-117.24607,32.66935), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.19677,32.68885,-115.0008,32.69968), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.0008,32.69968,-117.25517,32.70005), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.25517,32.70005,-115.0008,32.69968), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.71963,32.71876,-114.66749,32.73423), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.66749,32.73423,-114.61739,32.74105), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.61739,32.74105,-114.70572,32.74158), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.70572,32.74158,-114.61739,32.74105), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.57068,32.74742,-114.70572,32.74158), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.53175,32.7825,-117.25497,32.78695), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.25497,32.78695,-114.53175,32.7825), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.28097,32.82225,-117.28217,32.83955), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.28217,32.83955,-114.46897,32.84516), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.46897,32.84516,-117.26291,32.84935), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.26291,32.84935,-117.27387,32.85145), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.27387,32.85145,-117.26291,32.84935), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.25617,32.85945,-117.25616,32.85967), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.25616,32.85967,-117.25617,32.85945), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.25447,32.90015,-114.46313,32.90188), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.46313,32.90188,-117.25447,32.90015), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.47664,32.92363,-114.46313,32.90188), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.48132,32.97206,-117.27214,32.97552), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.27214,32.97552,-114.48132,32.97206), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.28077,33.01234,-114.51134,33.02346), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.51134,33.02346,-114.51707,33.02463), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.51707,33.02463,-114.51134,33.02346), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.62829,33.03105,-114.57516,33.03654), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.57516,33.03654,-114.6708,33.03798), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.6708,33.03798,-114.57516,33.03654), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.31528,33.0935,-114.70618,33.10534), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.70618,33.10534,-117.31528,33.0935), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.67936,33.15952,-117.36257,33.16844), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.36257,33.16844,-114.67936,33.15952), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.6781,33.2303,-114.67449,33.2556), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.67449,33.2556,-117.44558,33.26852), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.44558,33.26852,-114.67449,33.2556), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.72326,33.28808,-117.44558,33.26852), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.70796,33.32342,-114.72326,33.28808), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.54769,33.36549,-114.70735,33.37663), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.70735,33.37663,-117.59588,33.38663), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.59588,33.38663,-117.59619,33.38696), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.59619,33.38696,-117.59588,33.38663), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.72528,33.40505,-114.6739,33.4183), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.6739,33.4183,-114.63518,33.42273), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.63518,33.42273,-114.6739,33.4183), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.62915,33.43355,-117.64558,33.44073), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.64558,33.44073,-114.62915,33.43355), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.71535,33.46056,-117.64558,33.44073), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.72649,33.48343,-117.73226,33.48796), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.73226,33.48796,-114.59728,33.49065), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.59728,33.49065,-117.73226,33.48796), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.81419,33.55222,-114.5246,33.55223), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.5246,33.55223,-117.81419,33.55222), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.84029,33.57352,-114.5246,33.55223), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.92709,33.60552,-114.52919,33.60665), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.52919,33.60665,-117.92709,33.60552), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.00059,33.65432,-114.5252,33.66158), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.5252,33.66158,-118.00059,33.65432), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.50499,33.69302,-118.25869,33.70374), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.25869,33.70374,-118.31721,33.71282), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.31721,33.71282,-118.23193,33.7153), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.23193,33.7153,-118.31721,33.71282), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.49657,33.71916,-118.3333,33.72118), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.3333,33.72118,-114.49657,33.71916), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.0889,33.72982,-118.35471,33.73232), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.35471,33.73232,-118.0889,33.72982), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.39661,33.73592,-118.1837,33.73612), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.1837,33.73612,-118.39661,33.73592), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.1161,33.74435,-118.1837,33.73612), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.1327,33.75322,-114.50486,33.76047), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.50486,33.76047,-118.1327,33.75322), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.42841,33.77472,-114.50486,33.76047), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.39431,33.80432,-114.52047,33.82778), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.52047,33.82778,-118.39431,33.80432), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.50564,33.86428,-118.41271,33.88391), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.41271,33.88391,-114.50871,33.90064), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.50871,33.90064,-118.41271,33.88391), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.53499,33.9285,-114.50871,33.90064), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.50957,33.95726,-118.46061,33.96911), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.46061,33.96911,-114.50957,33.95726), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.80511,34.00124,-114.45481,34.01097), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.45481,34.01097,-118.80511,34.00124), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.51951,34.02751,-118.74495,34.0321), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.74495,34.0321,-118.67937,34.03326), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.67937,34.03326,-118.85465,34.03422), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.85465,34.03422,-118.67937,34.03326), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.60357,34.03905,-114.4355,34.04262), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.4355,34.04262,-118.60357,34.03905), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.94448,34.04674,-118.95472,34.04817), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.95472,34.04817,-118.94448,34.04674), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.43009,34.07893,-119.06996,34.09047), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.06996,34.09047,-114.42803,34.09279), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.42803,34.09279,-119.10978,34.09457), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.10978,34.09457,-114.42803,34.09279), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.40594,34.11154,-119.10978,34.09457), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.34805,34.13446,-114.40594,34.11154), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.22774,34.16173,-114.29281,34.16673), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.29281,34.16673,-119.22774,34.16173), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.22972,34.18693,-114.29281,34.16673), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.25704,34.2133,-114.22972,34.18693), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.17805,34.23997,-119.27014,34.2529), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.27014,34.2529,-119.27661,34.25634), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.27661,34.25634,-114.13906,34.25954), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.13906,34.25954,-119.27661,34.25634), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.31303,34.27569,-114.13906,34.25954), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.14082,34.30313,-114.14093,34.30592), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.14093,34.30592,-114.14082,34.30313), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.37578,34.32112,-114.14093,34.30592), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.17285,34.34498,-119.37578,34.32112), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.46104,34.37406,-119.47795,34.37884), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.47795,34.37884,-119.46104,34.37406), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.70907,34.3954,-119.53696,34.3955), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.53696,34.3955,-119.70907,34.3954), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.26432,34.40133,-119.53696,34.3955), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.68467,34.4083,-119.87397,34.4088), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.87397,34.4088,-119.68467,34.4083), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.83577,34.4158,-119.78587,34.416), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.78587,34.416,-119.83577,34.4158), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.61686,34.421,-119.78587,34.416), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.97195,34.44464,-120.45143,34.44709), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.45143,34.44709,-119.97195,34.44464), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.33537,34.45004,-114.37885,34.45038), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.37885,34.45038,-114.33537,34.45004), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.05068,34.46165,-120.29505,34.47062), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.29505,34.47062,-120.14117,34.47341), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.14117,34.47341,-120.29505,34.47062), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.37822,34.51652,-120.51142,34.52295), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.51142,34.52295,-114.37822,34.51652), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.55009,34.54279,-120.62258,34.55402), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.62258,34.55402,-120.58129,34.55696), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.58129,34.55696,-120.62258,34.55402), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.42238,34.58071,-120.64574,34.58104), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.64574,34.58104,-114.42238,34.58071), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.46525,34.6912,-120.60197,34.6921), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.60197,34.6921,-114.46525,34.6912), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.49097,34.72485,-120.61485,34.73071), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.61485,34.73071,-114.49097,34.72485), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.62632,34.73807,-120.61485,34.73071), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.57645,34.8153,-120.61027,34.85818), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.61027,34.85818,-114.63438,34.87289), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.63438,34.87289,-120.61027,34.85818), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.67084,34.90412,-114.63438,34.87289), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.62977,34.94304,-120.65031,34.97517), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.65031,34.97517,-114.63349,35.00186), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.63349,35.00186,-120.65031,34.97517), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.63357,35.03309,-114.63349,35.00186), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.62958,35.07836,-120.63357,35.03309), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.63579,35.12381,-114.80425,35.13969), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-114.80425,35.13969,-120.67507,35.15306), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.67507,35.15306,-120.75609,35.16046), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.75609,35.16046,-120.67507,35.15306), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.71419,35.176,-120.75609,35.16046), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.84667,35.20443,-120.71419,35.176), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.89679,35.24788,-120.84667,35.20443), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.87957,35.29418,-115.04381,35.33201), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.04381,35.33201,-120.86213,35.36076), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.86213,35.36076,-115.04381,35.33201), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.16007,35.42413,-120.88476,35.4302), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.88476,35.4302,-115.16007,35.42413), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.95586,35.45374,-121.00336,35.46071), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.00336,35.46071,-120.95586,35.45374), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.30374,35.53821,-121.11424,35.57172), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.11424,35.57172,-115.30374,35.53821), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.40454,35.61761,-121.16671,35.6354), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.16671,35.6354,-115.40454,35.61761), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.27232,35.66671,-121.16671,35.6354), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.31463,35.71331,-121.27232,35.66671), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.33245,35.78311,-121.34705,35.79519), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.34705,35.79519,-121.33245,35.78311), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.64768,35.80936,-115.64803,35.80963), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.64803,35.80963,-115.64768,35.80936), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.40682,35.84462,-115.64803,35.80963), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.46226,35.88562,-121.40682,35.84462), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.84611,35.96355,-121.4862,35.97035), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.4862,35.97035,-115.84611,35.96355), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-115.89298,35.99997,-121.53188,36.01437), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.53188,36.01437,-121.5746,36.02516), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.5746,36.02516,-121.53188,36.01437), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.62201,36.0997,-116.0936,36.15581), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.0936,36.15581,-121.68015,36.16582), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.68015,36.16582,-116.0936,36.15581), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.77985,36.22741,-121.82643,36.24186), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.82643,36.24186,-121.77985,36.22741), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.88849,36.30281,-121.82643,36.24186), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.37588,36.37256,-121.9032,36.3936), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.9032,36.3936,-116.37588,36.37256), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-116.48823,36.4591,-121.9416,36.4856), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.9416,36.4856,-116.48823,36.4591), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.97043,36.58275,-121.8606,36.61114), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.8606,36.61114,-121.92387,36.63456), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.92387,36.63456,-121.8606,36.61114), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.81446,36.68286,-121.92387,36.63456), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.79683,36.77754,-121.79154,36.81519), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.79154,36.81519,-117.0009,36.84769), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.0009,36.84769,-121.81273,36.85005), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.81273,36.85005,-117.0009,36.84769), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.86227,36.93155,-122.02717,36.95115), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.02717,36.95115,-122.06732,36.9536), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.06732,36.9536,-122.10598,36.95595), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.10598,36.95595,-122.06732,36.9536), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.90647,36.96895,-117.166,36.97121), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.166,36.97121,-121.95167,36.97145), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.95167,36.97145,-117.166,36.97121), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.20618,37.01395,-117.24492,37.03024), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.24492,37.03024,-122.20618,37.01395), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.26048,37.07255,-122.28488,37.10175), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.28488,37.10175,-122.29431,37.10514), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.29431,37.10514,-122.28488,37.10175), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.32297,37.11546,-122.29431,37.10514), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.34403,37.1441,-122.32297,37.11546), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.39707,37.18725,-122.34403,37.1441), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.41845,37.24852,-122.39707,37.18725), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.40559,37.31497,-122.40132,37.33701), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.40132,37.33701,-117.68061,37.3534), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.68061,37.3534,-122.40132,37.33701), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.40926,37.37481,-117.68061,37.3534), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.44369,37.43594,-122.44599,37.46154), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.44599,37.46154,-117.8335,37.46494), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-117.8335,37.46494,-122.44599,37.46154), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.49379,37.49234,-122.16845,37.50414), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.16845,37.50414,-122.49379,37.49234), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.1292,37.52132,-122.51669,37.52134), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.51669,37.52134,-122.1292,37.52132), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.112,37.52885,-122.51669,37.52134), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.24437,37.55814,-122.51809,37.57614), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.51809,37.57614,-122.1444,37.58187), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.1444,37.58187,-122.51809,37.57614), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.36022,37.5925,-118.02218,37.60258), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.02218,37.60258,-122.49679,37.61214), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.49679,37.61214,-118.02218,37.60258), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.15291,37.64077,-122.1628,37.66727), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.1628,37.66727,-122.16305,37.66793), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.16305,37.66793,-122.1628,37.66727), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.49678,37.68643,-122.21377,37.6987), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.21377,37.6987,-122.39319,37.70753), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.39319,37.70753,-122.50068,37.70813), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.50068,37.70813,-122.38983,37.70833), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.38983,37.70833,-122.50068,37.70813), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.36175,37.71501,-122.38983,37.70833), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.24981,37.72641,-122.35678,37.72951), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.35678,37.72951,-122.24981,37.72641), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.5056,37.73557,-122.37646,37.73856), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.37646,37.73856,-122.5056,37.73557), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.25245,37.75513,-122.51198,37.77113), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.51198,37.77113,-122.31297,37.77724), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.31297,37.77724,-122.51198,37.77113), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.38532,37.79072,-122.4654,37.80088), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.4654,37.80088,-122.39814,37.80563), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.39814,37.80563,-122.33371,37.8098), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.33371,37.8098,-122.39814,37.80563), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.48348,37.82673,-122.30393,37.83009), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.30393,37.83009,-122.53729,37.83033), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.53729,37.83033,-122.30393,37.83009), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.41847,37.85272,-122.53729,37.83033), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.60129,37.87513,-122.44841,37.89341), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.44841,37.89341,-122.70264,37.89382), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.70264,37.89382,-122.32871,37.89383), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.32871,37.89383,-122.70264,37.89382), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.428,37.89622,-122.32871,37.89383), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.37871,37.90519,-122.67847,37.9066), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.67847,37.9066,-122.37871,37.90519), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.33453,37.90879,-122.67847,37.9066), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.48638,37.92188,-122.33453,37.90879), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.75461,37.93553,-118.50096,37.94902), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.50096,37.94902,-122.42526,37.95567), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.42526,37.95567,-118.50096,37.94902), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.48867,37.96671,-122.79741,37.97666), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.79741,37.97666,-122.36758,37.97817), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.36758,37.97817,-122.79741,37.97666), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.97439,37.99243,-122.453,37.99617), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.453,37.99617,-122.97439,37.99243), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.01153,38.00344,-122.36889,38.00795), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.36889,38.00795,-122.3428,38.00925), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.3428,38.00925,-122.32171,38.01031), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.32171,38.01031,-122.3428,38.00925), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.85657,38.01672,-122.32171,38.01031), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.93971,38.03191,-122.49947,38.03217), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.49947,38.03217,-122.93971,38.03191), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.26286,38.05147,-122.26932,38.06037), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.26932,38.06037,-122.26286,38.05147), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.3018,38.10514,-122.49128,38.10809), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.49128,38.10809,-122.4885,38.10909), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.4885,38.10909,-122.49128,38.10809), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.96089,38.11296,-122.4885,38.10909), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.39758,38.142,-122.39359,38.14345), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.39359,38.14345,-122.39758,38.142), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.95363,38.17567,-122.39359,38.14345), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.98715,38.23754,-118.94967,38.26894), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-118.94967,38.26894,-122.98632,38.27316), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.98632,38.27316,-118.94967,38.26894), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.00315,38.29571,-123.00412,38.29701), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.00412,38.29701,-123.00315,38.29571), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.0535,38.29939,-123.00412,38.29701), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.06844,38.33521,-123.0535,38.29939), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.08557,38.39053,-119.15723,38.41439), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.15723,38.41439,-123.08557,38.39053), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.16643,38.47495,-119.27926,38.49991), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.27926,38.49991,-123.2498,38.51105), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.2498,38.51105,-119.27926,38.49991), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.3287,38.53435,-123.2498,38.51105), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.3319,38.56554,-119.3287,38.53435), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.34961,38.59681,-123.3319,38.56554), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.44177,38.69974,-119.58541,38.71315), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.58541,38.71315,-119.58768,38.71473), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.58768,38.71473,-119.58541,38.71315), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.51478,38.74197,-123.54092,38.76766), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.54092,38.76766,-123.51478,38.74197), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.57199,38.79819,-123.54092,38.76766), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.63864,38.84387,-123.65985,38.87253), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.65985,38.87253,-123.63864,38.84387), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.71054,38.91323,-119.90432,38.93332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.90432,38.93332,-123.71054,38.91323), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.73289,38.95499,-119.90432,38.93332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00101,38.99957,-123.69074,39.02129), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.69074,39.02129,-120.00101,38.99957), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00198,39.0675,-120.00261,39.11269), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00261,39.11269,-123.72151,39.12533), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.72151,39.12533,-120.00261,39.11269), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00336,39.16563,-123.76589,39.19366), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.76589,39.19366,-120.00336,39.16563), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.79899,39.27136,-120.00514,39.29126), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00514,39.29126,-123.79899,39.27136), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.0048,39.31648,-120.00514,39.29126), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.82533,39.36081,-120.0048,39.31648), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00303,39.44505,-123.81469,39.44654), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.81469,39.44654,-120.00303,39.44505), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.00174,39.53885,-123.76648,39.5528), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.76648,39.5528,-120.00174,39.53885), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.78232,39.62149,-123.79266,39.68412), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.79266,39.68412,-119.99994,39.72241), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99994,39.72241,-123.82955,39.72307), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.82955,39.72307,-119.99994,39.72241), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.85171,39.83204,-123.90766,39.86303), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.90766,39.86303,-123.85171,39.83204), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.95495,39.92237,-119.99763,39.95651), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99763,39.95651,-123.95495,39.92237), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.02521,40.0013,-124.0359,40.01332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.0359,40.01332,-124.06891,40.02131), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.06891,40.02131,-124.0359,40.01332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.08709,40.07844,-124.13995,40.11635), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.13995,40.11635,-119.99712,40.12636), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99712,40.12636,-124.18787,40.13054), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.18787,40.13054,-119.99712,40.12636), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.25841,40.18428,-124.18787,40.13054), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.34307,40.24398,-124.36341,40.26097), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.36341,40.26097,-124.34307,40.24398), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99616,40.32125,-124.35312,40.33143), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.35312,40.33143,-119.99616,40.32125), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.36536,40.37486,-124.35312,40.33143), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.40959,40.43808,-124.36536,40.37486), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.38702,40.50495,-124.40959,40.43808), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.38702,40.50495,-124.40959,40.43808), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.30136,40.65964,-119.99753,40.72099), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99753,40.72099,-124.30136,40.65964), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.17672,40.84362,-119.99923,40.8659), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99923,40.8659,-124.17672,40.84362), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.11815,40.98926,-124.12545,41.0485), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.12545,41.0485,-124.15451,41.08716), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.15451,41.08716,-124.12545,41.0485), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.16399,41.13868,-119.99987,41.18397), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99987,41.18397,-124.12268,41.18973), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.12268,41.18973,-119.99987,41.18397), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.09228,41.2877,-124.12268,41.18973), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.06308,41.43958,-124.06747,41.46474), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.06747,41.46474,-124.06308,41.43958), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.08199,41.54776,-119.99828,41.61877), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99828,41.61877,-124.11604,41.62885), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.11604,41.62885,-119.99828,41.61877), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.14348,41.70928,-124.15425,41.7288), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.15425,41.7288,-124.19104,41.73608), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.19104,41.73608,-124.15425,41.7288), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.24503,41.7923,-124.21959,41.84643), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.21959,41.84643,-119.99928,41.87489), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99928,41.87489,-124.21959,41.84643), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.2034,41.94096,-121.0352,41.99332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.0352,41.99332,-120.87948,41.99348), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.87948,41.99348,-121.0352,41.99332), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.69222,41.99368,-120.50107,41.99379), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.50107,41.99379,-120.69222,41.99368), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-119.99917,41.99454,-120.18156,41.99459), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-120.18156,41.99459,-119.99917,41.99454), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.657,41.99514,-123.82144,41.99562), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.82144,41.99562,-123.657,41.99514), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.00119,41.99615,-123.82144,41.99562), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.43961,41.99708,-121.44754,41.99719), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.44754,41.99719,-121.43961,41.99708), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.2511,41.99757,-121.44754,41.99719), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-124.21161,41.99846,-123.34756,41.99911), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.34756,41.99911,-123.51911,41.99917), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.51911,41.99917,-123.34756,41.99911), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.67535,42.00035,-123.51911,41.99917), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.43477,42.00164,-121.67535,42.00035), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.04525,42.00305,-121.84671,42.00307), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-121.84671,42.00307,-123.04525,42.00305), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.80008,42.00407,-123.23073,42.00498), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.23073,42.00498,-122.10192,42.00577), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.10192,42.00577,-123.23073,42.00498), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.28953,42.00776,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.28953,42.00776,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-122.50114,42.00846,-122.28953,42.00776), mapfile, tile_dir, 0, 11, "california-ca")
	render_tiles((-123.14596,42.00925,-122.50114,42.00846), mapfile, tile_dir, 0, 11, "california-ca")