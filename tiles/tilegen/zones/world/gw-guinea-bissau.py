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
    # Region: GW
    # Region Name: Guinea-Bissau

	render_tiles((-15.09083,10.92389,-15.01974,10.96388), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.01974,10.96388,-15.10389,10.96611), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.10389,10.96611,-15.01974,10.96388), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.11389,10.98555,-15.23417,10.99389), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.23417,10.99389,-15.11389,10.98555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.27444,11.03,-15.23417,10.99389), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.21028,11.09055,-15.24306,11.12305), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.24306,11.12305,-15.22028,11.14194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.22028,11.14194,-15.36083,11.14833), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.36083,11.14833,-15.22028,11.14194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.01306,11.17305,-15.36083,11.14833), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.85528,11.19777,-15.01306,11.17305), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.01445,11.22722,-14.85528,11.19777), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.42667,11.29528,-15.50917,11.33805), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.50917,11.33805,-15.45472,11.34027), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.45472,11.34027,-15.50917,11.33805), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.33778,11.36083,-15.38194,11.37555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.38194,11.37555,-15.32556,11.38), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.32556,11.38,-15.38194,11.37555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.78417,11.38527,-15.36195,11.38889), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.36195,11.38889,-14.78417,11.38527), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.34389,11.44555,-15.47083,11.48972), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.47083,11.48972,-14.69139,11.50667), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.69139,11.50667,-14.51528,11.51222), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.51528,11.51222,-15.35361,11.5175), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.35361,11.5175,-14.51528,11.51222), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.33195,11.54444,-15.05056,11.56972), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.05056,11.56972,-15.13611,11.57055), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.13611,11.57055,-15.05056,11.56972), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.24278,11.57222,-15.13611,11.57055), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.32583,11.58528,-15.37195,11.59055), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.37195,11.59055,-15.32583,11.58528), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.20222,11.59666,-15.42611,11.59778), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.42611,11.59778,-15.20222,11.59666), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.01083,11.60916,-15.09445,11.61472), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.09445,11.61472,-15.01083,11.60916), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.27972,11.62055,-15.09445,11.61472), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.18361,11.63805,-14.00222,11.64083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.00222,11.64083,-15.18361,11.63805), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.29445,11.64611,-14.00222,11.64083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.49056,11.65583,-15.13,11.66389), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.13,11.66389,-15.265,11.66722), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.265,11.66722,-13.87806,11.66916), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.87806,11.66916,-15.285,11.67055), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.285,11.67055,-15.21667,11.67194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.21667,11.67194,-15.285,11.67055), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.27667,11.67889,-15.34611,11.67972), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.34611,11.67972,-14.27667,11.67889), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.5475,11.68305,-13.79167,11.68555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.79167,11.68555,-15.42583,11.6875), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.42583,11.6875,-13.79167,11.68555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.13139,11.70222,-15.49084,11.70667), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.49084,11.70667,-15.13139,11.70222), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.28889,11.7125,-13.79944,11.71472), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.79944,11.71472,-13.70917,11.71527), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.70917,11.71527,-13.79944,11.71472), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.18583,11.72139,-15.55556,11.7275), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.55556,11.7275,-15.18583,11.72139), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.96361,11.73416,-15.55556,11.7275), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.86111,11.74416,-15.82667,11.74417), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.82667,11.74417,-13.86111,11.74416), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.95667,11.74667,-15.82667,11.74417), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.22889,11.75278,-14.95667,11.74667), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.92333,11.76305,-15.89834,11.76583), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.89834,11.76583,-14.92333,11.76305), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.05917,11.79277,-15.95667,11.80194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.95667,11.80194,-15.05917,11.79277), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.0525,11.81861,-15.82417,11.82083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.82417,11.82083,-15.0525,11.81861), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.80111,11.83333,-15.82417,11.82083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.08917,11.85416,-15.0675,11.85861), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.0675,11.85861,-15.08917,11.85416), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.18639,11.87028,-15.43833,11.88), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.43833,11.88,-15.80472,11.88083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.80472,11.88083,-15.43833,11.88), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.13306,11.88361,-15.80472,11.88083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.52528,11.90166,-15.22583,11.90528), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.22583,11.90528,-15.52528,11.90166), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.1875,11.91028,-15.98028,11.91333), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.98028,11.91333,-15.1875,11.91028), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.06278,11.92972,-15.07972,11.94), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.07972,11.94,-15.02722,11.94833), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.02722,11.94833,-15.94056,11.95083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.94056,11.95083,-15.02722,11.94833), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-14.99056,11.95333,-15.94056,11.95083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.43111,11.95889,-15.36083,11.96305), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.36083,11.96305,-15.43111,11.95889), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.00417,11.97361,-15.84667,11.98194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.84667,11.98194,-15.08584,11.98333), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.08584,11.98333,-15.84667,11.98194), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.33389,11.99667,-15.77833,11.99777), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.77833,11.99777,-16.33389,11.99667), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.70667,12.00139,-15.77833,11.99777), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.86389,12.02917,-13.70667,12.00139), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.35778,12.09972,-13.89028,12.14361), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.89028,12.14361,-13.97062,12.15323), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.97062,12.15323,-13.89028,12.14361), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.45834,12.17166,-13.97062,12.15323), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.34639,12.19611,-13.95667,12.20972), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.95667,12.20972,-16.34639,12.19611), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.87861,12.24555,-13.71472,12.2575), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.71472,12.2575,-13.87861,12.24555), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.69424,12.29309,-13.71472,12.2575), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.10528,12.3325,-16.67781,12.33458), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.67781,12.33458,-16.10528,12.3325), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.67781,12.33458,-16.10528,12.3325), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.66361,12.35166,-16.46389,12.36111), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.46389,12.36111,-13.66361,12.35166), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.68556,12.43,-16.20916,12.46083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-16.20916,12.46083,-13.64417,12.47138), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.64417,12.47138,-16.20916,12.46083), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.40722,12.55694,-13.71472,12.56889), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.71472,12.56889,-15.40722,12.55694), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.71255,12.66603,-13.92833,12.67666), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-13.92833,12.67666,-15.21833,12.68472), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")
	render_tiles((-15.21833,12.68472,-13.92833,12.67666), mapfile, tile_dir, 0, 11, "gw-guinea-bissau")