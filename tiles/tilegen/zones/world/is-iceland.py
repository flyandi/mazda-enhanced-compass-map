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
    # Region: IS
    # Region Name: Iceland

	render_tiles((-18.86806,63.40276,-18.26222,63.45721), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.26222,63.45721,-18.22,63.46999), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.22,63.46999,-18.16445,63.47083), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.16445,63.47083,-18.22,63.46999), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.52167,63.48499,-18.16445,63.47083), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.17889,63.50582,-19.52167,63.48499), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.92834,63.53082,-20.17056,63.53638), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.17056,63.53638,-19.89333,63.53805), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.89333,63.53805,-20.17056,63.53638), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.3075,63.5461,-19.89333,63.53805), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.85722,63.60526,-20.33889,63.61388), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.33889,63.61388,-17.85722,63.60526), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.96278,63.62471,-20.33889,63.61388), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.82167,63.63888,-20.43639,63.64082), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.43639,63.64082,-17.82167,63.63888), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.93056,63.66165,-20.44611,63.66776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.44611,63.66776,-17.79806,63.66971), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.79806,63.66971,-20.44611,63.66776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.50195,63.70221,-20.56472,63.70805), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.56472,63.70805,-20.50195,63.70221), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.31111,63.72971,-20.53902,63.73158), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.53902,63.73158,-17.87,63.73193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.87,63.73193,-20.53902,63.73158), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.62611,63.74554,-17.72306,63.74693), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.72306,63.74693,-17.62611,63.74554), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.56139,63.7511,-17.72306,63.74693), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.56583,63.76082,-20.76917,63.76777), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.76917,63.76777,-17.71667,63.77388), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.71667,63.77388,-20.49358,63.77702), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.49358,63.77702,-17.71667,63.77388), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.44361,63.78333,-16.96695,63.78471), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.96695,63.78471,-20.44361,63.78333), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.98611,63.79471,-16.91139,63.79665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.91139,63.79665,-16.98611,63.79471), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.74639,63.79887,-16.91139,63.79665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.68639,63.79887,-16.91139,63.79665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.78361,63.80276,-22.68361,63.8036), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.68361,63.8036,-16.78361,63.80276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.63972,63.82277,-20.40223,63.82304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.40223,63.82304,-21.63972,63.82277), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.97889,63.82665,-17.01722,63.82721), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.01722,63.82721,-20.97889,63.82665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.71473,63.82943,-17.01722,63.82721), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.78195,63.84165,-20.67278,63.84249), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.67278,63.84249,-16.78195,63.84165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.36556,63.84915,-22.2825,63.85027), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.2825,63.85027,-21.36556,63.84915), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.69861,63.85471,-22.2825,63.85027), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.88528,63.86499,-21.76278,63.86971), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.76278,63.86971,-16.88528,63.86499), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.17472,63.87527,-21.76278,63.86971), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.20695,63.8861,-16.94056,63.8911), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.94056,63.8911,-21.20695,63.8861), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.70306,63.91082,-16.44361,63.91277), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.44361,63.91277,-22.70306,63.91082), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.96667,63.91721,-21.30056,63.91805), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.30056,63.91805,-16.96667,63.91721), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.19445,63.92277,-21.30056,63.91805), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.04639,63.93971,-22.63139,63.94804), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.63139,63.94804,-21.195,63.95471), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.195,63.95471,-22.63139,63.94804), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.72834,63.96832,-22.5475,63.97777), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.5475,63.97777,-22.72834,63.96832), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.58973,64.04442,-16.14917,64.05637), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.14917,64.05637,-22.72583,64.06387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.72583,64.06387,-21.97667,64.06665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.97667,64.06665,-22.72583,64.06387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.68861,64.08414,-22.0475,64.09747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.0475,64.09747,-21.9475,64.09831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.9475,64.09831,-22.0475,64.09747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.97306,64.11136,-21.91972,64.12387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.91972,64.12387,-15.99084,64.12608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.99084,64.12608,-21.91972,64.12387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.99111,64.14108,-22.03917,64.15221), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.03917,64.15221,-21.93028,64.15692), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.93028,64.15692,-22.03917,64.15221), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.71611,64.17636,-15.70972,64.18303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.70972,64.18303,-21.71611,64.17636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.775,64.21275,-21.92611,64.22887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.92611,64.22887,-21.775,64.21275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.00167,64.2572,-15.22083,64.26137), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.22083,64.26137,-15.00167,64.2572), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.91333,64.27248,-15.38389,64.27748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.38389,64.27748,-14.91333,64.27248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.16639,64.28276,-15.38389,64.27748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.27833,64.29553,-14.89167,64.30165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.89167,64.30165,-14.95278,64.30193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.95278,64.30193,-14.89167,64.30165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.33083,64.30359,-14.95278,64.30193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.93195,64.30609,-15.33083,64.30359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.0925,64.30942,-21.93195,64.30609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.90639,64.31526,-22.0925,64.30942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.94389,64.32303,-15.27306,64.32637), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.27306,64.32637,-14.94389,64.32303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.75472,64.34276,-22.03334,64.3447), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.03334,64.3447,-21.75472,64.34276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.37195,64.36304,-21.98445,64.37804), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.98445,64.37804,-14.74611,64.38303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.74611,64.38303,-21.73278,64.38692), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.73278,64.38692,-14.74611,64.38303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.45417,64.39415,-22.04111,64.39636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.04111,64.39636,-21.45417,64.39415), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.53945,64.40608,-14.56833,64.41193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.56833,64.41193,-14.53945,64.40608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.70195,64.42943,-14.56833,64.41193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.08778,64.46858,-22.25056,64.48387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.25056,64.48387,-22.08778,64.46858), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.97889,64.50081,-22.07361,64.50914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.07361,64.50914,-21.97889,64.50081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.02889,64.52109,-21.98861,64.52664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.98861,64.52664,-22.02889,64.52109), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.49695,64.54665,-22.34167,64.55275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.34167,64.55275,-14.49695,64.54665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.5575,64.5672,-22.24306,64.56831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.24306,64.56831,-14.5575,64.5672), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.43306,64.59387,-22.36167,64.59998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.36167,64.59998,-14.34528,64.60553), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.34528,64.60553,-21.7025,64.60803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.7025,64.60803,-14.34528,64.60553), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.405,64.6272,-14.44945,64.64276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.44945,64.64276,-14.28083,64.64581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.28083,64.64581,-21.51417,64.64748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.51417,64.64748,-14.28083,64.64581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.67028,64.65886,-22.44083,64.66025), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.44083,64.66025,-21.67028,64.65886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.40389,64.66859,-22.32361,64.6722), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.32361,64.6722,-14.27111,64.67247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.27111,64.67247,-22.32361,64.6722), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.34111,64.68359,-14.24306,64.68887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.24306,64.68887,-14.34111,64.68359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.39222,64.69443,-14.24306,64.68887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.01417,64.72636,-22.19,64.73358), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.19,64.73358,-23.66389,64.73776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.66389,64.73776,-22.19,64.73358), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.32278,64.74387,-23.9325,64.74942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.9325,64.74942,-22.32278,64.74387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.01917,64.75748,-14.38806,64.75859), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.38806,64.75859,-22.3275,64.75914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.3275,64.75914,-14.38806,64.75859), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.65028,64.77164,-22.43972,64.77969), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.43972,64.77969,-22.65028,64.77164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.03,64.78998,-14.48,64.79164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.48,64.79164,-14.03,64.78998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.6675,64.79498,-22.77723,64.79665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.77723,64.79665,-23.13195,64.79803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.13195,64.79803,-22.77723,64.79665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.83361,64.7997,-23.13195,64.79803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.45056,64.80942,-23.56556,64.81415), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.56556,64.81415,-13.90056,64.81859), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.90056,64.81859,-23.56556,64.81415), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.82695,64.82359,-23.29139,64.82637), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.29139,64.82637,-13.82695,64.82359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.955,64.83971,-23.29139,64.82637), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.76528,64.86136,-13.80278,64.8822), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.80278,64.8822,-24.05783,64.88922), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.05783,64.88922,-23.68778,64.89221), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.68778,64.89221,-24.05783,64.88922), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.94222,64.90387,-13.74195,64.91248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.74195,64.91248,-23.11,64.91803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.11,64.91803,-23.26334,64.9222), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.26334,64.9222,-23.11,64.91803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.83389,64.9272,-23.26334,64.9222), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.70806,64.93275,-14.05111,64.93387), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.05111,64.93387,-13.70806,64.93275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.22278,64.94331,-23.31361,64.94775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.31361,64.94775,-23.16028,64.95192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.16028,64.95192,-23.31361,64.94775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.11195,64.95886,-23.16028,64.95192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.5775,64.97581,-13.77917,64.98776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.77917,64.98776,-23.23917,64.99054), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.23917,64.99054,-13.61611,64.99248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.61611,64.99248,-23.23917,64.99054), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.67417,64.99887,-23.08361,65.00525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.08361,65.00525,-22.62556,65.00775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.62556,65.00775,-23.08361,65.00525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.9225,65.01276,-23.19028,65.01581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.19028,65.01581,-13.9225,65.01276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.21722,65.02193,-23.19028,65.01581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.09778,65.03053,-14.21722,65.02193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.79111,65.04747,-22.52945,65.05081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.52945,65.05081,-21.79111,65.04747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.00945,65.05748,-22.69861,65.05998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.69861,65.05998,-14.00945,65.05748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.845,65.06747,-14.01028,65.07303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.01028,65.07303,-21.845,65.06747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.49778,65.07303,-21.845,65.06747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.625,65.09665,-22.07084,65.10471), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.07084,65.10471,-13.625,65.09665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.68472,65.11525,-22.07084,65.10471), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.96473,65.12637,-13.68472,65.11525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.66528,65.15137,-21.72945,65.15747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.72945,65.15747,-13.51222,65.1597), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.51222,65.1597,-21.72945,65.15747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.09889,65.16248,-13.53528,65.16275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.53528,65.16275,-21.09889,65.16248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.53139,65.18581,-13.62222,65.19247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.62222,65.19247,-14.035,65.19304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.035,65.19304,-13.62222,65.19247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.06973,65.1947,-21.73417,65.19609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.73417,65.19609,-21.06973,65.1947), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.80861,65.20331,-21.73417,65.19609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.63945,65.21942,-21.80861,65.20331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.14417,65.23804,-22.44167,65.24664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.44167,65.24664,-21.14417,65.23804), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.56861,65.26665,-13.96806,65.27664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.96806,65.27664,-21.10583,65.28525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.10583,65.28525,-13.96806,65.27664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.69861,65.30304,-13.745,65.31219), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.745,65.31219,-22.19417,65.32109), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.19417,65.32109,-13.745,65.31219), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.72695,65.33443,-21.06889,65.33887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.06889,65.33887,-20.91639,65.34164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.91639,65.34164,-21.06889,65.33887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.83361,65.36359,-13.67556,65.37303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.67556,65.37303,-13.83361,65.36359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.9075,65.40303,-23.72278,65.41721), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.72278,65.41721,-23.9075,65.40303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.815,65.43359,-21.09723,65.43665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.09723,65.43665,-21.21222,65.43719), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.21222,65.43719,-21.09723,65.43665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.27972,65.44026,-22.17806,65.44247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.17806,65.44247,-21.47889,65.44414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.47889,65.44414,-24,65.44443), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24,65.44443,-21.47889,65.44414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.08167,65.45609,-24,65.44443), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.65223,65.46776,-20.98889,65.46831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.98889,65.46831,-23.65223,65.46776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.05445,65.47136,-20.98889,65.46831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.38417,65.47693,-24.05445,65.47136), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.22917,65.48276,-20.49306,65.48747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.49306,65.48747,-24.50917,65.48943), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.50917,65.48943,-20.49306,65.48747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.33361,65.49303,-20.44667,65.49498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.44667,65.49498,-24.33361,65.49303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.72,65.50108,-22.36695,65.50331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.36695,65.50331,-22.495,65.50415), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.495,65.50415,-22.36695,65.50331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.19445,65.50415,-22.36695,65.50331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.61167,65.50998,-20.5475,65.5108), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.5475,65.5108,-13.61167,65.50998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.515,65.5136,-20.5475,65.5108), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.89222,65.51804,-24.515,65.5136), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.39528,65.52414,-13.81083,65.52525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.81083,65.52525,-20.39528,65.52414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.10778,65.52525,-20.39528,65.52414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.67834,65.52664,-13.81083,65.52525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.30306,65.53081,-22.57389,65.53108), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.57389,65.53108,-21.30306,65.53081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.65833,65.53165,-22.57389,65.53108), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.80667,65.53247,-13.65833,65.53165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.1775,65.53525,-22.32556,65.5372), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.32556,65.5372,-22.1775,65.53525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.53111,65.54082,-22.88722,65.54137), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.88722,65.54137,-14.53111,65.54082), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.01417,65.54221,-20.43056,65.54276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.43056,65.54276,-23.01417,65.54221), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.64556,65.54692,-23.15028,65.54776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.15028,65.54776,-20.64556,65.54692), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.46306,65.55414,-21.475,65.55692), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.475,65.55692,-24.37611,65.55942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.37611,65.55942,-21.475,65.55692), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.19667,65.56219,-22.9125,65.5647), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.9125,65.5647,-23.19667,65.56219), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.52723,65.57025,-22.53611,65.57164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.53611,65.57164,-20.52723,65.57025), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.94584,65.57414,-20.56945,65.5761), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.56945,65.5761,-20.94584,65.57414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.16084,65.57942,-22.71695,65.58192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.71695,65.58192,-21.29945,65.5822), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.29945,65.5822,-22.71695,65.58192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.86,65.5847,-20.61306,65.58498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.61306,65.58498,-22.86,65.5847), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.79528,65.58914,-20.41084,65.59248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.41084,65.59248,-21.43639,65.59276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.43639,65.59276,-20.41084,65.59248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.01306,65.5947,-22.10889,65.59497), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.10889,65.59497,-24.01306,65.5947), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.00028,65.59886,-21.33334,65.59998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.33334,65.59998,-14.00028,65.59886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.56195,65.6058,-22.95111,65.60748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.95111,65.60748,-23.80334,65.60776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.80334,65.60776,-24.1575,65.60803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.1575,65.60803,-23.80334,65.60776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-13.87028,65.6122,-22.85083,65.61304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.85083,65.61304,-13.87028,65.6122), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.555,65.61664,-22.85083,65.61304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.68361,65.62248,-22.76334,65.62581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.76334,65.62581,-22.68361,65.62248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.33306,65.62997,-21.40639,65.6322), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.40639,65.6322,-14.32432,65.63278), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.32432,65.63278,-21.40639,65.6322), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.46695,65.64053,-23.55334,65.64165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.55334,65.64165,-23.46695,65.64053), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.06945,65.64331,-23.55334,65.64165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.08361,65.6472,-20.32361,65.64886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.32361,65.64886,-24.08361,65.6472), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.64833,65.65109,-20.32361,65.64886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.24195,65.65831,-21.64833,65.65109), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.29695,65.66942,-14.27472,65.67137), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.27472,65.67137,-23.29695,65.66942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.62611,65.67636,-21.66472,65.67693), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.575,65.67636,-21.66472,65.67693), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.66472,65.67693,-20.62611,65.67636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.06278,65.68608,-21.58945,65.68803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.58945,65.68803,-18.06278,65.68608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.43806,65.68803,-18.06278,65.68608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.6825,65.69165,-21.58945,65.68803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.52528,65.6972,-20.6825,65.69165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.13361,65.7047,-23.52528,65.6972), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.39806,65.71692,-14.81778,65.72136), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.81778,65.72136,-20.26306,65.72359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.26306,65.72359,-23.51611,65.72498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.51611,65.72498,-19.45195,65.72581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.45195,65.72581,-23.51611,65.72498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.55945,65.73331,-21.33973,65.7397), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.33973,65.7397,-19.55945,65.73331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.52195,65.75081,-19.64778,65.75609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.64778,65.75609,-18.10389,65.75887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.10389,65.75887,-14.89222,65.75914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.89222,65.75914,-18.10389,65.75887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.67028,65.76053,-14.89222,65.75914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.54889,65.76248,-21.67028,65.76053), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.475,65.76498,-21.77084,65.76747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.77084,65.76747,-21.475,65.76498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.23389,65.77248,-24.12611,65.77582), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.12611,65.77582,-23.74361,65.77609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.74361,65.77609,-24.12611,65.77582), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.33694,65.78304,-18.18056,65.78442), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.18056,65.78442,-19.39806,65.78525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.39806,65.78525,-18.18056,65.78442), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.35028,65.78998,-14.38194,65.79082), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.38194,65.79082,-21.35028,65.78998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-24.10583,65.80664,-20.31611,65.80887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.31611,65.80887,-24.10583,65.80664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.46806,65.83414,-19.39111,65.83636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.39111,65.83636,-18.07028,65.83832), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.07028,65.83832,-22.43584,65.83887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.43584,65.83887,-18.07028,65.83832), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.28861,65.83971,-22.43584,65.83887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.30917,65.85609,-23.31556,65.85915), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.31556,65.85915,-20.30917,65.85609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.87,65.86304,-19.705,65.86443), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.705,65.86443,-23.87,65.86304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.62139,65.86443,-23.87,65.86304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.43,65.88719,-14.64695,65.88942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.64695,65.88942,-22.43,65.88719), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.86945,65.89192,-23.86584,65.89247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.86584,65.89247,-19.86945,65.89192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.27917,65.89497,-23.86584,65.89247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.35861,65.90804,-18.28945,65.91081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.28945,65.91081,-22.82972,65.9122), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.82972,65.9122,-18.28945,65.91081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.82167,65.91887,-18.20556,65.92192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.20556,65.92192,-18.14528,65.92276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.14528,65.92276,-18.20556,65.92192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.45861,65.94165,-17.55972,65.95081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.55972,65.95081,-19.51306,65.95192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.51306,65.95192,-21.54139,65.95219), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.54139,65.95219,-19.51306,65.95192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.38027,65.95601,-17.41222,65.95775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.38027,65.95601,-17.41222,65.95775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.41222,65.95775,-14.60583,65.95804), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.60583,65.95804,-17.41222,65.95775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.74028,65.96471,-22.53639,65.96997), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.53639,65.96997,-22.78334,65.97165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.78334,65.97165,-18.54028,65.97275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.54028,65.97275,-17.55194,65.97304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.55194,65.97304,-22.56945,65.97331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.56945,65.97331,-17.55194,65.97304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.87806,65.9747,-22.56945,65.97331), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.32167,65.98553,-22.87806,65.9747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.43834,65.99637,-22.81361,65.99776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.81361,65.99776,-21.325,65.99803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.325,65.99803,-22.81361,65.99776), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.41833,65.99915,-21.325,65.99803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.81611,66.00941,-22.96528,66.01442), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.96528,66.01442,-21.52778,66.01915), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.52778,66.01915,-21.66861,66.02025), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.84556,66.01915,-21.66861,66.02025), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.66861,66.02025,-21.52778,66.01915), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.98083,66.02275,-20.44695,66.02414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.44695,66.02414,-22.98083,66.02275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.39167,66.02609,-18.52111,66.02803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.52111,66.02803,-14.89972,66.02887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.89972,66.02887,-18.52111,66.02803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.62195,66.03386,-14.89972,66.02887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.66333,66.04359,-21.55917,66.04469), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.55917,66.04469,-14.66333,66.04359), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.93056,66.0472,-23.52389,66.04831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.76528,66.0472,-23.52389,66.04831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.52389,66.04831,-19.45639,66.04886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.45639,66.04886,-23.52389,66.04831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.47278,66.05331,-22.68028,66.05525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.68028,66.05525,-18.31556,66.05664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.31556,66.05664,-22.68028,66.05525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.80111,66.05664,-22.68028,66.05525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.68667,66.06081,-21.51083,66.06137), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.51083,66.06137,-23.68667,66.06081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.73083,66.06609,-21.64417,66.06665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.64417,66.06665,-14.73083,66.06609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.05556,66.06665,-14.73083,66.06609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.0525,66.06998,-21.72167,66.07248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.72167,66.07248,-15.0525,66.06998), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.10778,66.0761,-22.495,66.07637), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.495,66.07637,-23.10778,66.0761), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.98139,66.07858,-21.64028,66.08054), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.64028,66.08054,-22.98139,66.07858), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.34472,66.08386,-20.42083,66.08582), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.42083,66.08582,-18.66222,66.08664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.66222,66.08664,-20.42083,66.08582), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.38334,66.09081,-18.56778,66.09192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.56778,66.09192,-21.55,66.09276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.55,66.09276,-18.56778,66.09192), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.35833,66.0947,-15.06556,66.09608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.39361,66.0947,-15.06556,66.09608), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.06556,66.09608,-17.35833,66.0947), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.15417,66.09914,-16.63834,66.10025), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.63834,66.10025,-15.15417,66.09914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.65834,66.10414,-19.10833,66.10664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.10833,66.10664,-23.65834,66.10414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.03889,66.10664,-23.65834,66.10414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.82084,66.1122,-23.66222,66.11748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.66222,66.11748,-16.91833,66.11887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.91833,66.11887,-23.66222,66.11748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-20.095,66.12469,-17.25917,66.12747), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.25917,66.12747,-20.095,66.12469), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.62611,66.13248,-15.14917,66.13609), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.14917,66.13609,-23.59584,66.13831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.59584,66.13831,-18.80556,66.13942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.14222,66.13831,-18.80556,66.13942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.80556,66.13942,-23.59584,66.13831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.38194,66.14276,-16.42834,66.14304), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.42834,66.14304,-15.38194,66.14276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.91695,66.14525,-16.72417,66.14636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.72417,66.14636,-18.91695,66.14525), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.33306,66.15192,-16.72417,66.14636), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.58389,66.15886,-16.68861,66.15942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.68861,66.15942,-19.06973,66.15997), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-19.06973,66.15997,-16.68861,66.15942), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.27139,66.16054,-19.06973,66.15997), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.745,66.16748,-14.97695,66.16969), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.69861,66.16748,-14.97695,66.16969), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.97695,66.16969,-21.745,66.16748), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.21056,66.17303,-22.91222,66.17358), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.91222,66.17358,-18.21056,66.17303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.34083,66.17554,-22.91222,66.17358), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.48639,66.17886,-15.34083,66.17554), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.91028,66.18248,-16.52056,66.18303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.52056,66.18303,-18.91028,66.18248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.96889,66.18553,-16.52056,66.18303), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.45861,66.18831,-18.95861,66.18915), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.95861,66.18915,-15.45861,66.18831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.78445,66.19109,-23.37139,66.19193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.37139,66.19193,-21.86806,66.19247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.86806,66.19247,-23.37139,66.19193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-18.86584,66.19998,-21.86806,66.19247), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.93639,66.21164,-17.10361,66.21275), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-17.10361,66.21275,-14.93639,66.21164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.62528,66.22386,-22.98889,66.22664), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.98889,66.22664,-15.62528,66.22386), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-21.92834,66.24164,-22.53056,66.2458), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.53056,66.2458,-21.92834,66.24164), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.34222,66.25276,-22.53056,66.2458), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.82195,66.26109,-22.36834,66.26831), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.36834,66.26831,-16.41695,66.27081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.41695,66.27081,-22.56084,66.27193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.56084,66.27193,-16.41695,66.27081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.23195,66.27441,-22.56084,66.27193), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.00111,66.27721,-22.23195,66.27441), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.77306,66.28775,-22.70056,66.29248), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.70056,66.29248,-15.77306,66.28775), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.93861,66.29858,-15.07167,66.29886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.07167,66.29886,-22.93861,66.29858), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.50639,66.30054,-15.07167,66.29886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.70917,66.3047,-22.50639,66.30054), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.80972,66.30942,-22.70917,66.3047), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.14611,66.3297,-15.68611,66.33414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.68611,66.33414,-22.20444,66.33498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.20444,66.33498,-15.68611,66.33414), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.82445,66.34526,-23.19028,66.35332), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.19028,66.35332,-22.82445,66.34526), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.03945,66.36359,-15.69333,66.36581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.69333,66.36581,-22.61,66.3672), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.61,66.3672,-15.69333,66.36581), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.94389,66.37886,-14.55753,66.38333), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-14.55753,66.38333,-14.94389,66.37886), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.04972,66.39108,-22.35806,66.39276), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.35806,66.39276,-23.04972,66.39108), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.87417,66.41582,-22.45167,66.41914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.45167,66.41914,-15.93778,66.42053), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.93778,66.42053,-22.45167,66.41914), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.13223,66.4272,-15.93778,66.42053), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-23.09389,66.43887,-22.88,66.44165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.88,66.44165,-23.09389,66.43887), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.4325,66.44803,-22.88,66.44165), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.40778,66.45665,-22.94639,66.46498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-22.94639,66.46498,-22.40778,66.45665), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.5775,66.47498,-22.94639,66.46498), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-15.93528,66.49803,-16.55556,66.50081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.55556,66.50081,-15.93528,66.49803), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.20306,66.50443,-16.55556,66.50081), mapfile, tile_dir, 0, 11, "is-iceland")
	render_tiles((-16.17834,66.53442,-16.20306,66.50443), mapfile, tile_dir, 0, 11, "is-iceland")