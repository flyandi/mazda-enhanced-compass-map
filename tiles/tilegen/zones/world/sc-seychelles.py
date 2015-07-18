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
    # Region: SC
    # Region Name: Seychelles

	render_tiles((55.7703,-4.3497,55.7647,-4.3483), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7703,-4.3497,55.7647,-4.3483), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7647,-4.3483,55.7703,-4.3497), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7758,-4.3483,55.7703,-4.3497), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.76,-4.3464,55.7544,-4.345), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7544,-4.345,55.7783,-4.3444), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7783,-4.3444,55.7544,-4.345), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7489,-4.3436,55.7428,-4.3431), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7428,-4.3431,55.7489,-4.3436), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7372,-4.3417,55.775,-4.3408), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.775,-4.3408,55.7319,-4.3403), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7319,-4.3403,55.775,-4.3408), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7722,-4.3367,55.7319,-4.3403), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7283,-4.3367,55.7319,-4.3403), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7283,-4.3314,55.7717,-4.3306), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7717,-4.3306,55.7283,-4.3314), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7744,-4.3267,55.7914,-4.3258), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7914,-4.3258,55.7292,-4.3253), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7847,-4.3258,55.7292,-4.3253), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7292,-4.3253,55.7914,-4.3258), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7792,-4.3244,55.7292,-4.3253), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.725,-4.3225,55.7928,-4.3217), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7928,-4.3217,55.7128,-4.3211), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7189,-4.3217,55.7128,-4.3211), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7128,-4.3211,55.7064,-4.3206), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7064,-4.3206,55.7128,-4.3211), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7894,-4.3183,55.7839,-4.3169), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7017,-4.3183,55.7839,-4.3169), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7839,-4.3169,55.7778,-4.3164), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7778,-4.3164,55.7839,-4.3169), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7717,-4.3156,55.6983,-4.315), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6983,-4.315,55.7717,-4.3156), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7667,-4.3136,55.6983,-4.315), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6947,-4.3114,55.7628,-4.3108), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7628,-4.3108,55.6947,-4.3114), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7592,-4.3075,55.6928,-4.3067), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6928,-4.3067,55.7592,-4.3075), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7558,-4.3042,55.7325,-4.3028), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7325,-4.3028,55.7386,-4.3019), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7386,-4.3019,55.7269,-4.3011), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6908,-4.3019,55.7269,-4.3011), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7269,-4.3011,55.7525,-4.3006), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7525,-4.3006,55.7269,-4.3011), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6872,-4.2986,55.7236,-4.2978), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7475,-4.2986,55.7236,-4.2978), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7422,-4.2986,55.7236,-4.2978), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7236,-4.2978,55.6872,-4.2986), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7208,-4.2936,55.6858,-4.2931), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6858,-4.2931,55.7208,-4.2936), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7189,-4.2889,55.6872,-4.2875), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6872,-4.2875,55.6936,-4.2869), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6936,-4.2869,55.6872,-4.2875), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7161,-4.2847,55.6975,-4.2842), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.6975,-4.2842,55.7161,-4.2847), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7114,-4.2828,55.6975,-4.2842), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7128,-4.2814,55.7011,-4.2808), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7011,-4.2808,55.7128,-4.2814), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.7072,-4.28,55.7011,-4.2808), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5325,-4.7892,55.5386,-4.7883), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5325,-4.7892,55.5386,-4.7883), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5386,-4.7883,55.5447,-4.7878), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5264,-4.7883,55.5447,-4.7878), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5447,-4.7878,55.5386,-4.7883), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5222,-4.7856,55.5483,-4.7842), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5483,-4.7842,55.5222,-4.7856), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5186,-4.7822,55.5483,-4.7842), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5461,-4.7794,55.5153,-4.7786), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5153,-4.7786,55.5461,-4.7794), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5428,-4.7761,55.5153,-4.7786), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5111,-4.7761,55.5153,-4.7786), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5078,-4.7725,55.54,-4.7719), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.54,-4.7719,55.5078,-4.7725), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5044,-4.7686,55.5381,-4.7672), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5381,-4.7672,55.5044,-4.7686), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5017,-4.7644,55.5353,-4.7631), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5353,-4.7631,55.5017,-4.7644), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5003,-4.7589,55.5339,-4.7575), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5339,-4.7575,55.5003,-4.7589), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4981,-4.7542,55.5331,-4.7514), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5331,-4.7514,55.4892,-4.7494), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4942,-4.7514,55.4892,-4.7494), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4892,-4.7494,55.5331,-4.7514), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4844,-4.7472,55.4892,-4.7494), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5331,-4.7447,55.4811,-4.7439), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4811,-4.7439,55.5331,-4.7447), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4775,-4.7406,55.5339,-4.7386), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5339,-4.7386,55.4775,-4.7406), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4769,-4.7344,55.5344,-4.7322), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5344,-4.7322,55.4797,-4.7303), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4797,-4.7303,55.4858,-4.7294), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4858,-4.7294,55.4906,-4.7289), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4906,-4.7289,55.4858,-4.7294), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5353,-4.7261,55.4914,-4.7242), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4914,-4.7242,55.5353,-4.7261), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5367,-4.7208,55.4892,-4.7192), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4892,-4.7192,55.5367,-4.7208), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4878,-4.7139,55.4892,-4.7192), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5367,-4.7139,55.4892,-4.7192), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4892,-4.7083,55.5372,-4.7078), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5372,-4.7078,55.4892,-4.7083), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4906,-4.7028,55.5381,-4.7017), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5381,-4.7017,55.4906,-4.7028), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4906,-4.6961,55.5386,-4.6956), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5386,-4.6956,55.4906,-4.6961), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4864,-4.6933,55.4803,-4.6928), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4803,-4.6928,55.4864,-4.6933), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4761,-4.69,55.4803,-4.6928), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.54,-4.69,55.4803,-4.6928), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4736,-4.6858,55.5406,-4.6839), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5406,-4.6839,55.47,-4.6825), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.47,-4.6825,55.5406,-4.6839), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5406,-4.6783,55.4639,-4.675), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4672,-4.6783,55.4639,-4.675), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4639,-4.675,55.54,-4.6722), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.54,-4.6722,55.4611,-4.6708), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4611,-4.6708,55.54,-4.6722), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4569,-4.6681,55.5392,-4.6661), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5392,-4.6661,55.4536,-4.6647), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4536,-4.6647,55.5392,-4.6661), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4494,-4.6619,55.5372,-4.6614), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5372,-4.6614,55.4494,-4.6619), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4453,-4.6592,55.5339,-4.6578), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5339,-4.6578,55.4453,-4.6592), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4414,-4.6564,55.5339,-4.6578), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5303,-4.6544,55.4317,-4.6525), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4364,-4.6544,55.4317,-4.6525), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4317,-4.6525,55.5264,-4.6517), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5264,-4.6517,55.4261,-4.6511), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4261,-4.6511,55.5264,-4.6517), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.42,-4.6503,55.4069,-4.6497), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5208,-4.6503,55.4069,-4.6497), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4069,-4.6497,55.4008,-4.6492), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4139,-4.6497,55.4008,-4.6492), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4008,-4.6492,55.4069,-4.6497), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5167,-4.6478,55.4008,-4.6492), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3975,-4.6456,55.5125,-4.645), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5125,-4.645,55.3975,-4.6456), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5083,-4.6422,55.3975,-4.64), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3975,-4.64,55.5044,-4.6394), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5044,-4.6394,55.3975,-4.64), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.5003,-4.6367,55.3961,-4.6347), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3961,-4.6347,55.4906,-4.6328), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4956,-4.6347,55.4906,-4.6328), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4906,-4.6328,55.3919,-4.6319), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3919,-4.6319,55.3858,-4.6311), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3858,-4.6311,55.3919,-4.6319), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4864,-4.6297,55.3858,-4.6311), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3803,-4.6297,55.3858,-4.6311), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3761,-4.6272,55.4864,-4.6297), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4825,-4.6272,55.4864,-4.6297), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4789,-4.6236,55.3747,-4.6231), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3747,-4.6231,55.4789,-4.6236), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4756,-4.6203,55.3831,-4.6189), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3789,-4.6203,55.3831,-4.6189), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3831,-4.6189,55.3892,-4.6183), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3892,-4.6183,55.3831,-4.6189), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4728,-4.6161,55.3933,-4.6156), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3933,-4.6156,55.4728,-4.6161), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.47,-4.6122,55.3953,-4.6108), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3953,-4.6108,55.47,-4.6122), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4686,-4.6067,55.3961,-4.6047), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3961,-4.6047,55.4686,-4.6067), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.3994,-4.6011,55.4214,-4.5997), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.47,-4.6011,55.4214,-4.5997), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4269,-4.6011,55.4214,-4.5997), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4214,-4.5997,55.4042,-4.5992), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4042,-4.5992,55.4214,-4.5997), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4317,-4.5992,55.4214,-4.5997), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4097,-4.5978,55.4042,-4.5992), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4167,-4.5978,55.4042,-4.5992), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4708,-4.595,55.4339,-4.5944), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4339,-4.5944,55.4708,-4.595), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4728,-4.5903,55.435,-4.5889), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.435,-4.5889,55.4728,-4.5903), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4728,-4.5847,55.4364,-4.5833), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4364,-4.5833,55.4728,-4.5847), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4708,-4.58,55.4386,-4.5786), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4386,-4.5786,55.4708,-4.58), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4708,-4.5744,55.4392,-4.5725), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4392,-4.5725,55.4708,-4.5744), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4728,-4.5697,55.4414,-4.5678), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4414,-4.5678,55.4728,-4.5697), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.475,-4.565,55.4433,-4.5628), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4433,-4.5628,55.475,-4.565), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.475,-4.5594,55.4453,-4.5581), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4453,-4.5581,55.475,-4.5594), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4722,-4.5556,55.4544,-4.5533), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4494,-4.5556,55.4544,-4.5533), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4544,-4.5533,55.4686,-4.5519), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4686,-4.5519,55.4639,-4.5514), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4639,-4.5514,55.4686,-4.5519), mapfile, tile_dir, 0, 11, "sc-seychelles")
	render_tiles((55.4592,-4.5514,55.4686,-4.5519), mapfile, tile_dir, 0, 11, "sc-seychelles")