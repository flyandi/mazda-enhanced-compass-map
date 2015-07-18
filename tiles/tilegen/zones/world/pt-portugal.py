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
    # Region: PT
    # Region Name: Portugal

	render_tiles((-25.385,37.71221,-25.16056,37.74638), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.385,37.71221,-25.16056,37.74638), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.16056,37.74638,-25.72695,37.75332), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.72695,37.75332,-25.16056,37.74638), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.13195,37.80527,-25.5775,37.82721), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.5775,37.82721,-25.68084,37.84165), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.68084,37.84165,-25.14222,37.84277), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.14222,37.84277,-25.68084,37.84165), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.865,37.85055,-25.14222,37.84277), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.32333,37.86388,-25.865,37.85055), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.72889,37.89526,-25.8425,37.90221), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-25.8425,37.90221,-25.72889,37.89526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.05639,38.39416,-28.41945,38.41055), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.05639,38.39416,-28.41945,38.41055), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.41945,38.41055,-28.03722,38.4186), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.03722,38.4186,-28.41945,38.41055), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.52639,38.44582,-28.03722,38.4186), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.54945,38.52721,-28.38,38.54916), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.38,38.54916,-28.49472,38.55471), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-28.49472,38.55471,-28.38,38.54916), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.08722,38.63138,-27.30722,38.65804), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.08722,38.63138,-27.30722,38.65804), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.30722,38.65804,-27.08722,38.63138), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.02111,38.6911,-27.38,38.72276), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.38,38.72276,-27.02111,38.6911), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.06583,38.76415,-27.36389,38.78777), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.36389,38.78777,-27.27723,38.80305), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-27.27723,38.80305,-27.36389,38.78777), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.94389,32.63749,-16.82167,32.64443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.94389,32.63749,-16.82167,32.64443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.82167,32.64443,-16.94389,32.63749), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-17.20667,32.73749,-16.67417,32.75889), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.67417,32.75889,-17.20667,32.73749), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.84667,32.79193,-17.255,32.81416), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-17.255,32.81416,-16.99695,32.82249), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.99695,32.82249,-17.10278,32.82332), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-17.10278,32.82332,-16.99695,32.82249), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-16.89972,32.83693,-17.10278,32.82332), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-17.18445,32.87193,-16.89972,32.83693), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.95028,36.99554,-7.97306,37.00832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.97306,37.00832,-8.95028,36.99554), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.79224,37.0389,-7.97306,37.00832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.96334,37.08305,-7.6725,37.0836), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.6725,37.0836,-8.96334,37.08305), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.24084,37.08582,-7.6725,37.0836), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.51111,37.10304,-8.24084,37.08582), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.62389,37.12388,-8.51111,37.10304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.90361,37.16666,-7.41817,37.17337), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.41817,37.17337,-7.45083,37.17915), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.45083,37.17915,-7.41817,37.17337), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.84778,37.35055,-7.45083,37.17915), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.78722,37.52415,-7.51417,37.57332), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.51417,37.57332,-8.78722,37.52415), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.79278,37.70165,-7.51417,37.57332), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.2975,37.84804,-8.7975,37.91277), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.7975,37.91277,-8.8825,37.95638), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.8825,37.95638,-7.25472,37.98749), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.25472,37.98749,-7.14611,38.00526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.14611,38.00526,-7.25472,37.98749), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.00695,38.02804,-7.14611,38.00526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.94417,38.16276,-7.08778,38.17443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.08778,38.17443,-6.94417,38.16276), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.77528,38.21221,-6.94805,38.21832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.94805,38.21832,-8.77528,38.21221), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.14417,38.27026,-6.94805,38.21832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.81139,38.38999,-9.20056,38.40971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.20056,38.40971,-9.22028,38.41554), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.22028,38.41554,-9.20056,38.40971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.30707,38.4256,-9.22028,38.41554), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.81778,38.44276,-8.73306,38.4461), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.73306,38.4461,-7.32972,38.4472), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.32972,38.4472,-8.73306,38.4461), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.77278,38.47694,-8.955,38.48249), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.955,38.48249,-8.77278,38.47694), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.17945,38.50665,-7.30111,38.52499), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.30111,38.52499,-9.17945,38.50665), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.74695,38.5486,-7.29225,38.57066), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.29225,38.57066,-8.74695,38.5486), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.21889,38.61721,-9.12333,38.65777), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.12333,38.65777,-9.005,38.6586), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.005,38.6586,-9.12333,38.65777), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.27889,38.6686,-9.32222,38.67666), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.32222,38.67666,-9.27889,38.6686), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.04528,38.69082,-8.96722,38.69888), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.96722,38.69888,-9.47417,38.70304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.47417,38.70304,-8.96722,38.69888), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.26556,38.70832,-9.47417,38.70304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.05111,38.71416,-9.11389,38.71749), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.11389,38.71749,-9.05111,38.71416), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.91945,38.76527,-9.495,38.78526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.495,38.78526,-7.15583,38.79027), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.15583,38.79027,-9.495,38.78526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.07667,38.83554,-7.15583,38.79027), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.99167,38.9286,-9.41445,38.94415), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.41445,38.94415,-8.99167,38.9286), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.95389,39.02693,-6.96111,39.05665), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.96111,39.05665,-6.95389,39.02693), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.11583,39.10416,-7.04056,39.12276), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.04056,39.12276,-7.11583,39.10416), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.15417,39.12276,-7.11583,39.10416), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.3975,39.12276,-7.11583,39.10416), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.14,39.17332,-7.24305,39.21304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.24305,39.21304,-9.33722,39.23444), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.33722,39.23444,-7.24305,39.21304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.235,39.27637,-9.33722,39.23444), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.31361,39.3447,-9.36195,39.34832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.36195,39.34832,-7.31361,39.3447), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.34167,39.37804,-9.18445,39.40694), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.18445,39.40694,-9.34167,39.37804), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.23,39.44027,-7.29361,39.46776), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.29361,39.46776,-9.23,39.44027), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.4425,39.55109,-7.29361,39.46776), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-9.07472,39.64499,-7.40778,39.64832), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.40778,39.64832,-9.07472,39.64499), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.53333,39.66888,-7.01722,39.67499), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.01722,39.67499,-7.53333,39.66888), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.86389,40.01554,-7.01444,40.14665), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.01444,40.14665,-8.85917,40.14971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.85917,40.14971,-7.01444,40.14665), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.90972,40.18999,-8.85917,40.14971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.00611,40.23081,-6.96083,40.24026), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.96083,40.24026,-7.00611,40.23081), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.7875,40.34165,-6.96083,40.24026), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.84833,40.44331,-6.79111,40.51804), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.79111,40.51804,-6.83917,40.57499), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.83917,40.57499,-6.79111,40.51804), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.75306,40.63943,-8.73583,40.65221), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.73583,40.65221,-6.79778,40.65776), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.79778,40.65776,-8.73583,40.65221), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.73056,40.71249,-6.82944,40.75526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.82944,40.75526,-8.73056,40.71249), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.79972,40.85609,-6.82944,40.75526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.89167,40.9747,-8.64611,40.99721), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.64611,40.99721,-6.89167,40.9747), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.92444,41.03137,-6.80861,41.04054), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.80861,41.04054,-6.92444,41.03137), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.69889,41.18193,-6.68139,41.21554), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.68139,41.21554,-6.59833,41.24415), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.59833,41.24415,-6.68139,41.21554), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.43389,41.32249,-6.31861,41.38721), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.31861,41.38721,-6.32944,41.41526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.32944,41.41526,-8.79333,41.41637), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.79333,41.41637,-6.32944,41.41526), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.21778,41.52943,-8.79972,41.56666), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.79972,41.56666,-6.19444,41.59304), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.19444,41.59304,-8.79972,41.56666), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.49778,41.65749,-6.35583,41.67776), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.35583,41.67776,-6.53944,41.67943), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.53944,41.67943,-6.35583,41.67776), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.83778,41.68748,-6.53944,41.67943), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.56278,41.74526,-8.83778,41.68748), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.14083,41.80915,-7.42722,41.81248), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.42722,41.81248,-8.14083,41.80915), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.87917,41.82888,-7.61194,41.83498), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.61194,41.83498,-7.52445,41.84054), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.52445,41.84054,-7.61194,41.83498), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.88,41.85277,-7.45694,41.86443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.45694,41.86443,-7.98194,41.86638), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.98194,41.86638,-7.45694,41.86443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.50861,41.87387,-7.59167,41.87971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.59167,41.87971,-7.20056,41.8836), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.20056,41.8836,-6.56861,41.88721), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.56861,41.88721,-7.91222,41.88971), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.91222,41.88971,-6.56861,41.88721), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.70611,41.90443,-8.8124,41.90453), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.8124,41.90453,-7.70611,41.90443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.8124,41.90453,-7.70611,41.90443), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.21889,41.9136,-8.8124,41.90453), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.88611,41.92332,-8.21889,41.9136), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.545,41.9372,-6.62806,41.94109), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.62806,41.94109,-6.545,41.9372), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.83778,41.9472,-6.62806,41.94109), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.58167,41.96748,-6.83778,41.9472), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-7.15222,41.98859,-6.80944,41.99026), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-6.80944,41.99026,-7.15222,41.98859), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.08278,42.02526,-8.62111,42.0536), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.62111,42.0536,-8.18583,42.0647), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.18583,42.0647,-8.09,42.06888), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.09,42.06888,-8.18583,42.0647), mapfile, tile_dir, 0, 11, "pt-portugal")
	render_tiles((-8.20139,42.15221,-8.09,42.06888), mapfile, tile_dir, 0, 11, "pt-portugal")