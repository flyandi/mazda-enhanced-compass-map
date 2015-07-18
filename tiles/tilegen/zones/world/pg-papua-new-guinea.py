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
    # Region: PG
    # Region Name: Papua New Guinea

	render_tiles((150.3344,-9.52667,150.2486,-9.50167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3344,-9.52667,150.2486,-9.50167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2486,-9.50167,150.36301,-9.4875), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.36301,-9.4875,150.2486,-9.50167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3344,-9.46723,150.2438,-9.45917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2438,-9.45917,150.3344,-9.46723), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.14439,-9.40028,150.3774,-9.38945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3774,-9.38945,150.14439,-9.40028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.1033,-9.33694,150.3358,-9.29695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3358,-9.29695,150.12331,-9.26306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.12331,-9.26306,150.3358,-9.29695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2383,-9.21389,150.12331,-9.26306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.84689,-9.71806,150.8539,-9.6625), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.84689,-9.71806,150.8539,-9.6625), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8539,-9.6625,150.9325,-9.64778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9325,-9.64778,150.4386,-9.63889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4386,-9.63889,150.9325,-9.64778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4288,-9.61472,150.90021,-9.61333), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.90021,-9.61333,150.4288,-9.61472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.5152,-9.56667,150.5105,-9.52417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.5105,-9.52417,150.8858,-9.52083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8858,-9.52083,150.5105,-9.52417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.43021,-9.445,150.6714,-9.44028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.6714,-9.44028,150.43021,-9.445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.7605,-9.40445,150.6714,-9.44028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.43359,-9.36389,150.50079,-9.34167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.50079,-9.34167,150.43359,-9.36389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2291,-10.20111,151.0408,-10.11833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2291,-10.20111,151.0408,-10.11833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.0408,-10.11833,150.9539,-10.10806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9539,-10.10806,151.0408,-10.11833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.1113,-10.04473,151.1472,-10.0325), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.1472,-10.0325,150.9449,-10.02917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9449,-10.02917,151.1472,-10.0325), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2847,-10.0225,150.9449,-10.02917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9908,-10.00417,150.925,-10.00028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.925,-10.00028,150.9908,-10.00417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.1347,-9.99084,150.925,-10.00028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.0291,-9.98,151.1347,-9.99084), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8824,-9.9625,150.9464,-9.955), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9464,-9.955,150.8824,-9.9625), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.0538,-9.94472,150.9464,-9.955), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.22771,-9.93139,151.2813,-9.92334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2813,-9.92334,151.22771,-9.93139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8539,-9.85306,150.75861,-9.8025), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.75861,-9.8025,150.8539,-9.85306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.7722,-9.71195,150.75861,-9.8025), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.56799,-2.23667,147.2019,-2.19167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.56799,-2.23667,147.2019,-2.19167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2019,-2.19167,146.7258,-2.17639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.7258,-2.17639,146.79829,-2.16889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.79829,-2.16889,146.7258,-2.17639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2491,-2.15667,146.5202,-2.15472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.5202,-2.15472,147.2491,-2.15667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.5386,-2.11556,146.71049,-2.11417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.6113,-2.11556,146.71049,-2.11417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.71049,-2.11417,146.5386,-2.11556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.62019,-2.09833,146.71049,-2.11417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2863,-2.06778,147.4377,-2.06556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.4377,-2.06556,147.2863,-2.06778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.41769,-2.05056,147.4377,-2.06556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.3219,-2.03139,146.5755,-2.01972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.5755,-2.01972,146.6058,-2.01278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.6058,-2.01278,147.4458,-2.00944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.4458,-2.00944,146.6058,-2.01278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.41859,-2.00583,147.4458,-2.00944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.77161,-1.98695,146.7072,-1.97167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.7072,-1.97167,147.39191,-1.96083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.39191,-1.96083,146.84489,-1.95056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.84489,-1.95056,147.39191,-1.96083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.36771,-2.68667,150.2186,-2.67972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.36771,-2.68667,150.2186,-2.67972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2186,-2.67972,150.36771,-2.68667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4597,-2.64833,150.11771,-2.63056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.11771,-2.63056,150.4597,-2.64833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.05611,-2.52306,150.4433,-2.47583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4433,-2.47583,149.9541,-2.46556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.9541,-2.46556,150.4433,-2.47583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0533,-2.43528,149.9541,-2.46556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.19521,-2.37583,150.0533,-2.43528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0183,-6.32361,149.6347,-6.30806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0183,-6.32361,149.6347,-6.30806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.6347,-6.30806,149.8252,-6.29583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.8252,-6.29583,150.4063,-6.29306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4063,-6.29306,149.8252,-6.29583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.9841,-6.27472,150.0513,-6.2625), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0513,-6.2625,149.9841,-6.27472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.5208,-6.22833,150.0513,-6.2625), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.0714,-6.14861,150.77831,-6.135), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.77831,-6.135,149.0714,-6.14861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.0719,-6.09278,149.39079,-6.07722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.39079,-6.07722,149.0719,-6.09278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.3119,-6.05722,151.0338,-6.04306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.0338,-6.04306,149.3119,-6.05722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9722,-6.02583,150.8208,-6.01444), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8208,-6.01444,148.9769,-6.01222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.9769,-6.01222,150.8208,-6.01444), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.86301,-5.99444,151.08749,-5.98722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.08749,-5.98722,148.86301,-5.99444), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.1916,-5.96528,151.08749,-5.98722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.8513,-5.92722,151.2216,-5.90944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2216,-5.90944,148.8513,-5.92722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.55051,-5.84,148.6266,-5.81472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.35609,-5.84,148.6266,-5.81472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.6266,-5.81472,148.55051,-5.84), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.36411,-5.75528,151.4108,-5.73889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.4108,-5.73889,148.36411,-5.75528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.5233,-5.67611,148.32159,-5.675), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.32159,-5.675,151.5233,-5.67611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.5247,-5.63806,149.22079,-5.60611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.22079,-5.60611,151.8419,-5.59722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.8419,-5.59722,151.4444,-5.59167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.4444,-5.59167,151.8419,-5.59722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.7794,-5.58556,151.6144,-5.58083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6144,-5.58083,151.7794,-5.58556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.2097,-5.57195,149.7224,-5.57111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.7224,-5.57111,150.29111,-5.57056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.29111,-5.57056,149.7224,-5.57111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.1788,-5.5575,150.6241,-5.55611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.6241,-5.55611,150.1788,-5.5575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.563,-5.54806,148.8425,-5.54667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.8425,-5.54667,148.563,-5.54806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.7486,-5.54111,148.3416,-5.54), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.3416,-5.54,151.7486,-5.54111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.8752,-5.53639,151.4594,-5.53583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.4594,-5.53583,149.8752,-5.53639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6644,-5.535,151.4594,-5.53583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.49719,-5.52972,151.6644,-5.535), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.8347,-5.52,150.12331,-5.51722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.12331,-5.51722,149.6711,-5.51528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.6711,-5.51528,150.12331,-5.51722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.74609,-5.51111,149.6711,-5.51528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9025,-5.49278,148.7205,-5.49222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.7205,-5.49222,150.9025,-5.49278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.36,-5.48806,148.7205,-5.49222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.5686,-5.475,148.96159,-5.47361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.96159,-5.47361,150.5686,-5.475), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4124,-5.45861,152.0961,-5.45722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.0961,-5.45722,150.4124,-5.45861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.7886,-5.45306,148.4286,-5.45111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.4286,-5.45111,150.7886,-5.45306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.97861,-5.4375,150.99969,-5.42445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.99969,-5.42445,149.97861,-5.4375), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.14751,-5.36389,149.91769,-5.35222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.06219,-5.36389,149.91769,-5.35222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.91769,-5.35222,152.14751,-5.36389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.1297,-5.30778,149.91769,-5.35222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.07111,-5.17695,150.0724,-5.15167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0724,-5.15167,151.96941,-5.13528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.96941,-5.13528,150.0238,-5.12778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0238,-5.12778,151.96941,-5.13528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.205,-5.07056,150.0155,-5.0575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0155,-5.0575,150.205,-5.07056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.18159,-5.03889,150.0155,-5.0575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.08881,-5.00778,152.0611,-4.98917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.0611,-4.98917,152.2413,-4.98445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.2413,-4.98445,152.0611,-4.98917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.985,-4.97361,151.30611,-4.97139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.30611,-4.97139,151.6089,-4.97084), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6089,-4.97084,151.30611,-4.97139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.48219,-4.9475,151.57941,-4.93806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.57941,-4.93806,151.48219,-4.9475), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.66769,-4.92028,151.37109,-4.90806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.37109,-4.90806,151.66769,-4.92028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6972,-4.82667,151.6658,-4.78306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6658,-4.78306,151.6972,-4.82667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.3894,-4.78306,151.6972,-4.82667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.4016,-4.61083,152.3524,-4.51334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.3524,-4.51334,151.6308,-4.50639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6308,-4.50639,152.3524,-4.51334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.9749,-4.33695,152.4174,-4.33361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.4174,-4.33361,151.9749,-4.33695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.19051,-4.31445,151.8914,-4.31222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.8914,-4.31222,152.19051,-4.31445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.17081,-4.28222,152.02879,-4.27695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.02879,-4.27695,152.17081,-4.28222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.2408,-4.24667,151.5013,-4.23917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.5013,-4.23917,152.2408,-4.24667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.8419,-4.22472,152.18159,-4.21528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.18159,-4.21528,151.9966,-4.20861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.9966,-4.20861,152.2352,-4.20722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.2352,-4.20722,151.9966,-4.20861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.53,-4.18861,152.2352,-4.20722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.18111,-4.14639,151.53,-4.18861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.6291,-11.62028,153.77631,-11.5975), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.6291,-11.62028,153.77631,-11.5975), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.77631,-11.5975,153.4816,-11.59361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.4816,-11.59361,153.77631,-11.5975), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.3761,-11.56723,153.47079,-11.55306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.47079,-11.55306,153.3761,-11.56723), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.65221,-11.515,153.3877,-11.50084), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.3877,-11.50084,153.65221,-11.515), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.2719,-11.46056,153.3877,-11.50084), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.2041,-11.32334,153.2719,-11.46056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.12939,-5.45056,147.2247,-5.42944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.12939,-5.45056,147.2247,-5.42944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2247,-5.42944,147.12939,-5.45056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0099,-5.355,147.2247,-5.42944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.23911,-5.355,147.2247,-5.42944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0089,-5.25278,147.1308,-5.19389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.1308,-5.19389,147.0089,-5.25278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.9969,-5.85556,148.0372,-5.82083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.9969,-5.85556,148.0372,-5.82083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.0372,-5.82083,147.9969,-5.85556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8961,-5.77583,148.0372,-5.82083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.0791,-5.65445,147.77,-5.62222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.77,-5.62222,148.0408,-5.59861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.0408,-5.59861,147.77,-5.62222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.7619,-5.52222,147.8483,-5.49222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8483,-5.49222,147.7619,-5.52222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.9088,-4.84111,152.74361,-4.67306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.9088,-4.84111,152.74361,-4.67306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.74361,-4.67306,153.07941,-4.58917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.07941,-4.58917,152.74361,-4.67306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.0405,-4.48528,152.6636,-4.46334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.6636,-4.46334,153.0405,-4.48528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.1308,-4.39083,152.6636,-4.46334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.7025,-4.315,153.12939,-4.25556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.12939,-4.25556,152.7025,-4.315), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.9897,-4.07584,152.6286,-4.05611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.6286,-4.05611,152.9897,-4.07584), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.8669,-3.98361,152.6286,-4.05611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.7697,-3.90028,152.8197,-3.86945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.8197,-3.86945,152.7697,-3.90028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.5739,-3.82167,152.8197,-3.86945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.55521,-3.75111,152.3969,-3.74694), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.3969,-3.74694,152.55521,-3.75111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.43469,-3.6725,152.493,-3.64861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.493,-3.64861,152.4066,-3.62528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.4066,-3.62528,152.493,-3.64861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.22971,-3.53833,152.0769,-3.47611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.0769,-3.47611,152.20441,-3.45444), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.20441,-3.45444,152.0769,-3.47611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.91051,-3.42639,152.20441,-3.45444), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.1161,-3.33611,152.0558,-3.24778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.0558,-3.24778,151.6741,-3.24028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.6741,-3.24028,152.0558,-3.24778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.8952,-3.20806,151.6741,-3.24028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.7872,-3.16556,151.52,-3.1375), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.52,-3.1375,151.7872,-3.16556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.4191,-2.89806,151.13049,-2.87417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.13049,-2.87417,151.2011,-2.86306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.2011,-2.86306,151.13049,-2.87417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.188,-2.79806,151.1313,-2.77695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((151.1313,-2.77695,150.7599,-2.77222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.7599,-2.77222,150.9352,-2.77083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.9352,-2.77083,150.7599,-2.77222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.72971,-2.74056,150.7524,-2.71528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.7524,-2.71528,150.88721,-2.71111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.88721,-2.71111,150.7524,-2.71528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8811,-2.64056,150.79269,-2.61667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.79269,-2.61667,150.8811,-2.64056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8208,-2.56389,150.79269,-2.61667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.84019,-9.23473,152.7422,-9.21972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.84019,-9.23473,152.7422,-9.21972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.7422,-9.21972,152.84019,-9.23473), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.9561,-9.19084,152.6908,-9.17723), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.6908,-9.17723,152.99719,-9.17306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.99719,-9.17306,152.6908,-9.17723), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.74719,-9.16639,152.99719,-9.17306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.95,-9.14806,152.7191,-9.14667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.7191,-9.14667,152.95,-9.14806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((153.0177,-9.11778,152.7191,-9.14667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.58051,-9.025,152.56,-8.97695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.49969,-9.025,152.56,-8.97695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.56,-8.97695,152.8047,-8.96945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((152.8047,-8.96945,152.56,-8.97695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2097,-10.70056,150.3699,-10.68722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3699,-10.68722,150.2097,-10.70056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4438,-10.65889,150.3494,-10.6575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3494,-10.6575,150.4438,-10.65889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.1841,-10.64861,150.3494,-10.6575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0191,-10.6225,150.57269,-10.61972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.57269,-10.61972,150.0191,-10.6225), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.028,-10.58945,150.57269,-10.61972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.69189,-10.555,149.85271,-10.55028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.85271,-10.55028,150.69189,-10.555), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.02251,-10.51,149.9319,-10.50334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.9319,-10.50334,150.02251,-10.51), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.9258,-10.48778,150.6494,-10.47556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.6494,-10.47556,149.9258,-10.48778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0789,-10.46278,150.6494,-10.47556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.9566,-10.42973,149.8786,-10.405), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.8786,-10.405,150.3466,-10.3875), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.3466,-10.3875,149.8786,-10.405), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.64191,-10.34945,149.7533,-10.34417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.7533,-10.34417,150.64191,-10.34945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.35831,-10.33139,149.4202,-10.32806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.4202,-10.32806,150.35831,-10.33139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.3269,-10.31056,150.4158,-10.30028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.4158,-10.30028,149.3269,-10.31056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.96111,-10.28889,149.3716,-10.28278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.3716,-10.28278,150.63519,-10.28083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.63519,-10.28083,149.3716,-10.28278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.5966,-10.25111,150.8524,-10.24639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8524,-10.24639,148.8774,-10.2425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.8774,-10.2425,149.0619,-10.24111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.0619,-10.24111,148.8774,-10.2425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.8627,-10.22278,148.7299,-10.20473), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.7299,-10.20473,148.3972,-10.20111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.3972,-10.20111,150.34801,-10.19778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.34801,-10.19778,148.3972,-10.20111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.6458,-10.18306,150.34801,-10.19778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.9727,-10.16611,148.7636,-10.15278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.7636,-10.15278,147.9727,-10.16611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.2485,-10.13639,148.103,-10.12973), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.103,-10.12973,150.2485,-10.13639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.68221,-10.10806,147.7166,-10.10083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.7166,-10.10083,148.68221,-10.10806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.071,-10.08333,148.1613,-10.075), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.1613,-10.075,150.071,-10.08333), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8008,-10.05139,149.91409,-10.04889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.91409,-10.04889,147.8008,-10.05139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.70219,-10.02306,149.91409,-10.04889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.85741,-9.9875,147.70219,-10.02306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.76221,-9.90167,147.51019,-9.87861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.51019,-9.87861,149.76221,-9.90167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.71741,-9.82667,149.7697,-9.79028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.7697,-9.79028,149.92661,-9.76945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.92661,-9.76945,149.7697,-9.79028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.46719,-9.73973,149.92661,-9.76945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0569,-9.71,147.46719,-9.73973), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((150.0089,-9.63139,149.5197,-9.60306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.5197,-9.60306,150.0089,-9.63139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.40491,-9.55305,147.17799,-9.51861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.17799,-9.51861,147.2583,-9.51), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2583,-9.51,147.17799,-9.51861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.2666,-9.51,147.17799,-9.51861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.09911,-9.49111,147.2583,-9.51), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.2083,-9.4525,147.0999,-9.44972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0999,-9.44972,149.2083,-9.4525), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.008,-9.39834,149.18159,-9.34917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.18159,-9.34917,142.6497,-9.33195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.6497,-9.33195,142.57159,-9.32806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.57159,-9.32806,142.6497,-9.33195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.22411,-9.31667,142.57159,-9.32806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.978,-9.29278,142.7477,-9.27334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.7477,-9.27334,146.978,-9.29278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.9005,-9.24611,141.1763,-9.23389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.1763,-9.23389,141.11749,-9.23), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.11749,-9.23,141.1763,-9.23389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.8864,-9.22389,141.52879,-9.22194), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.52879,-9.22194,149.2419,-9.22111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.2419,-9.22111,141.52879,-9.22194), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.67329,-9.21972,149.2419,-9.22111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.3694,-9.21028,141.67329,-9.21972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.99001,-9.19,142.3694,-9.21028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.31599,-9.16778,141.31219,-9.15278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.31219,-9.15278,141.38831,-9.14417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.38831,-9.14417,146.8889,-9.13722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.8889,-9.13722,141.38831,-9.14417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0083,-9.12834,146.8889,-9.13722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.8963,-9.11222,149.32269,-9.10222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.32269,-9.10222,148.6897,-9.10083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.6897,-9.10083,149.32269,-9.10222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.8741,-9.07583,146.9772,-9.07445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.9772,-9.07445,148.8741,-9.07583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.57739,-9.05472,143.18359,-9.04473), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.18359,-9.04473,148.95,-9.0425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.95,-9.0425,149.0672,-9.04111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.0672,-9.04111,148.95,-9.0425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.9724,-9.02917,143.3316,-9.02833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.3316,-9.02833,146.9724,-9.02917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.30991,-9.01695,149.17551,-9.01139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.17551,-9.01139,149.30991,-9.01695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.59081,-9.00195,149.17551,-9.01139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((149.1358,-8.9775,143.40691,-8.96195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.40691,-8.96195,149.1358,-8.9775), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.5455,-8.9025,141.0069,-8.88889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0069,-8.88889,146.5455,-8.9025), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.4944,-8.86667,141.0069,-8.88889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.5674,-8.8075,143.3927,-8.77028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.3927,-8.77028,146.5674,-8.8075), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.32829,-8.68778,148.4388,-8.67222), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.4388,-8.67222,143.32829,-8.68778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.293,-8.60639,146.36189,-8.56778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.36189,-8.56778,148.293,-8.60639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.2216,-8.52778,146.36189,-8.56778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.0966,-8.46361,142.9061,-8.42362), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.9061,-8.42362,143.0966,-8.46361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.493,-8.37806,142.43941,-8.37139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.43941,-8.37139,146.2659,-8.36679), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.2659,-8.36679,142.43941,-8.37139), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.9799,-8.34417,142.5058,-8.33667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.5058,-8.33667,142.77161,-8.33195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.77161,-8.33195,142.5058,-8.33667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.4277,-8.32722,142.77161,-8.33195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.3824,-8.31917,142.6299,-8.31167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.6299,-8.31167,143.0699,-8.31083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.0699,-8.31083,142.6299,-8.31167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.5752,-8.29667,143.0699,-8.31083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.71629,-8.26833,142.5752,-8.29667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.6255,-8.23695,142.13519,-8.23028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.13519,-8.23028,141.0069,-8.22472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0069,-8.22472,146.1508,-8.21945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.1508,-8.21945,142.1913,-8.21611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.1913,-8.21611,146.1508,-8.21945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.3586,-8.2025,143.63049,-8.19833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.63049,-8.19833,142.3586,-8.2025), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.3839,-8.18972,142.31371,-8.18696), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.31371,-8.18696,142.3839,-8.18972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.0988,-8.17611,142.2216,-8.17334), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.2216,-8.17334,146.0988,-8.17611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.1049,-8.09111,147.9922,-8.05861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.9922,-8.05861,148.13609,-8.0425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((148.13609,-8.0425,143.7552,-8.04111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.7552,-8.04111,148.13609,-8.0425), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.8936,-8.03695,143.54829,-8.03361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.54829,-8.03361,143.8936,-8.03695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.8069,-8.0125,143.46111,-8.00028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.46111,-8.00028,143.9066,-7.99972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.9066,-7.99972,143.46111,-8.00028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.5016,-7.99611,143.9066,-7.99972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.9552,-7.98945,143.6433,-7.98889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.6433,-7.98889,143.9552,-7.98945), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.9577,-7.98528,143.6433,-7.98889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.89909,-7.97167,145.71831,-7.96778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.71831,-7.96778,143.89909,-7.97167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.95441,-7.96167,145.71831,-7.96778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.43269,-7.94806,145.7775,-7.94667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7775,-7.94667,145.43269,-7.94806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.7872,-7.93722,143.8141,-7.93528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.8141,-7.93528,147.7872,-7.93722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.4288,-7.93056,143.8141,-7.93528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7444,-7.92306,147.71049,-7.92167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.71049,-7.92167,145.7444,-7.92306), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.3605,-7.91694,147.71049,-7.92167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.4399,-7.91056,143.3605,-7.91694), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.3625,-7.89972,143.4399,-7.91056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.2388,-7.86722,145.3094,-7.86111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.3094,-7.86111,145.2388,-7.86722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.1552,-7.84611,145.3094,-7.86111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.6933,-7.82583,145.18469,-7.81417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.18469,-7.81417,147.6933,-7.82583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.6447,-7.80083,144.4789,-7.79472), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.4789,-7.79472,147.6447,-7.80083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8797,-7.7825,144.1416,-7.77889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.1416,-7.77889,144.8797,-7.7825), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.2719,-7.77361,144.1416,-7.77889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.3958,-7.75583,147.58771,-7.75195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.58771,-7.75195,144.3958,-7.75583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.3589,-7.745,147.58771,-7.75195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8597,-7.72333,144.41769,-7.72056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.41769,-7.72056,144.8597,-7.72333), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8241,-7.68806,144.33411,-7.68611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.33411,-7.68611,144.8241,-7.68806), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.3013,-7.68333,144.2641,-7.68056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.2641,-7.68056,144.3013,-7.68333), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5089,-7.67389,144.2641,-7.68056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5349,-7.66278,144.2299,-7.66028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.2299,-7.66028,144.5349,-7.66278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5883,-7.6575,144.2299,-7.66028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.8558,-7.6575,144.2299,-7.66028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.65359,-7.65278,144.5883,-7.6575), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.7186,-7.64056,144.2766,-7.63972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.2766,-7.63972,144.7186,-7.64056), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.3075,-7.63778,144.2766,-7.63972), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.31911,-7.62083,143.77161,-7.61389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.77161,-7.61389,144.64861,-7.60861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.64861,-7.60861,143.77161,-7.61389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8638,-7.59833,144.4225,-7.59639), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.4225,-7.59639,144.8638,-7.59833), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.6602,-7.5725,141.0061,-7.55944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-7.55944,144.5136,-7.55083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5136,-7.55083,141.0061,-7.55944), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.41859,-7.52583,144.52521,-7.50389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.3519,-7.52583,144.52521,-7.50389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.52521,-7.50389,147.2088,-7.48556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2088,-7.48556,143.65221,-7.4775), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.65221,-7.4775,147.2088,-7.48556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.6447,-7.44334,143.65221,-7.4775), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.1252,-7.19528,147.0627,-7.14167), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0627,-7.14167,147.1252,-7.19528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0705,-7.08389,147.0374,-7.04445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.0374,-7.04445,147.0705,-7.08389), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.9449,-6.95389,141.0062,-6.89342), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0062,-6.89342,140.9005,-6.85111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((140.9005,-6.85111,141.0062,-6.89342), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.2883,-6.74778,146.9613,-6.74718), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.9613,-6.74718,147.2883,-6.74778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8152,-6.71583,147.08881,-6.71445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.08881,-6.71445,147.8152,-6.71583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.868,-6.66611,147.08881,-6.71445), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((140.8499,-6.61694,140.89799,-6.59667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((140.89799,-6.59667,147.8586,-6.58028), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8586,-6.58028,140.89799,-6.59667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((140.9485,-6.43583,140.95129,-6.3725), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((140.95129,-6.3725,147.8264,-6.33722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.8264,-6.33722,141.0062,-6.33309), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0062,-6.33309,147.8264,-6.33722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0062,-6.33309,147.8264,-6.33722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.71609,-6.26917,141.0062,-6.33309), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.6736,-6.18139,147.71609,-6.26917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.4666,-5.97083,147.10271,-5.96611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((147.10271,-5.96611,147.4666,-5.97083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-5.955,147.10271,-5.96611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.8174,-5.84611,146.8752,-5.82556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.8752,-5.82556,146.8174,-5.84611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.78,-5.79639,146.8752,-5.82556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.28909,-5.58889,146.368,-5.58695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.368,-5.58695,146.28909,-5.58889), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((146.16299,-5.53583,146.368,-5.58695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.76221,-5.4825,146.16299,-5.53583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7238,-5.42528,145.76221,-5.4825), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7388,-5.30528,141.0061,-5.28667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-5.28667,145.7388,-5.30528), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7972,-5.2225,141.0061,-5.28667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.7972,-4.83556,145.6938,-4.77695), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.6938,-4.77695,145.7972,-4.83556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-4.6175,145.4511,-4.49417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.4511,-4.49417,145.1852,-4.39583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.1852,-4.39583,145.2991,-4.37667), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((145.2991,-4.37667,145.1852,-4.39583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8508,-4.14417,144.8763,-4.11361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.8763,-4.11361,144.8508,-4.14417), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.81799,-4.07722,144.8763,-4.11361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.59109,-4.00611,144.5439,-3.95361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5439,-3.95361,141.0061,-3.94778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-3.94778,144.5439,-3.95361), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5522,-3.88111,144.243,-3.87278), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.243,-3.87278,144.5522,-3.88111), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.2094,-3.83945,144.5116,-3.82083), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.5116,-3.82083,144.0108,-3.80722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.0108,-3.80722,144.27859,-3.80583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((144.27859,-3.80583,144.0108,-3.80722), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.9491,-3.73333,144.27859,-3.80583), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.7269,-3.60139,143.6833,-3.57611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.6833,-3.57611,143.7133,-3.555), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.7133,-3.555,143.6833,-3.57611), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.5955,-3.51111,143.7133,-3.555), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.4427,-3.41195,143.0233,-3.35861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((143.0233,-3.35861,143.4427,-3.41195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0061,-3.27778,142.5202,-3.20861), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.5202,-3.20861,141.0061,-3.27778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.0619,-3.05556,142.0769,-3.01778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((142.0769,-3.01778,142.0619,-3.05556), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.8947,-2.97195,141.998,-2.955), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.998,-2.955,141.8947,-2.97195), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.5724,-2.79667,141.998,-2.955), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.1313,-2.60917,141.0074,-2.60778), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")
	render_tiles((141.0074,-2.60778,141.1313,-2.60917), mapfile, tile_dir, 0, 11, "pg-papua-new-guinea")