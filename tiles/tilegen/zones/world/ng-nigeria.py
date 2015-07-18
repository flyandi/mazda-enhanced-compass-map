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
    # Region: NG
    # Region Name: Nigeria

	render_tiles((6.22139,4.28194,6.08611,4.2825), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.08611,4.2825,6.22139,4.28194), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.05778,4.28805,6.08611,4.2825), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.25278,4.29472,6.05778,4.28805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.41055,4.31333,5.99555,4.31444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.99555,4.31444,6.41055,4.31333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.24555,4.31694,5.99555,4.31444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.61278,4.32639,6.57444,4.32666), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.57444,4.32666,6.61278,4.32639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.69417,4.33,6.57444,4.32666), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.96361,4.33639,6.78055,4.3375), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.78055,4.3375,5.96361,4.33639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.93111,4.34055,6.22139,4.34166), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.22139,4.34166,5.93111,4.34055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.87083,4.35278,6.71444,4.35417), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.71444,4.35417,6.87083,4.35278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.99861,4.36972,7.02472,4.38416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.02472,4.38416,6.2625,4.39083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.2625,4.39083,6.2325,4.39222), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.2325,4.39222,6.2625,4.39083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.08667,4.39889,6.86555,4.4), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.86555,4.4,6.08667,4.39889), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.305,4.40861,5.83944,4.41333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.83944,4.41333,5.985,4.41583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.985,4.41583,5.83944,4.41333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.71694,4.42278,5.985,4.41583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.04444,4.43639,7.07583,4.4375), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.07583,4.4375,7.04444,4.43639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.29972,4.44167,7.07583,4.4375), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.58917,4.465,7.16778,4.47305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.16778,4.47305,6.58917,4.465), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.23722,4.49361,7.74667,4.49694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.74667,4.49694,6.69333,4.49889), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.69333,4.49889,7.74667,4.49694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.01833,4.50305,6.69333,4.49889), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.3275,4.5075,7.27885,4.51172), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.27885,4.51172,7.3275,4.5075), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.76339,4.51761,7.27885,4.51172), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.79916,4.52583,7.17889,4.52833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.17889,4.52833,7.55889,4.52972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.55889,4.52972,7.17889,4.52833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.49305,4.56444,6.74333,4.57), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.74333,4.57,6.81917,4.57111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.81917,4.57111,6.74333,4.57), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.5575,4.58028,8.31139,4.58555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.31139,4.58555,7.5575,4.58028), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.27444,4.59444,7.52944,4.59611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.52944,4.59611,7.27444,4.59444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.85417,4.60305,6.97194,4.60472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.97194,4.60472,6.72722,4.605), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.72722,4.605,6.97194,4.60472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.61472,4.62,8.3575,4.62528), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.3575,4.62528,5.61472,4.62), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.0275,4.63472,8.3575,4.62528), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.135,4.66361,7.17417,4.66778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.17417,4.66778,7.135,4.66361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.5175,4.67972,7.17417,4.66778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.51,4.69944,6.99639,4.71), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.99639,4.71,8.53944,4.71083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.53944,4.71083,6.99639,4.71), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.40167,4.75028,6.75139,4.76472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.75139,4.76472,8.53611,4.77555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.53611,4.77555,6.75139,4.76472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.36444,4.79361,8.39889,4.79889), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.39889,4.79889,8.36444,4.79361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.72194,4.80694,8.59048,4.81051), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.59048,4.81051,6.72194,4.80694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.63611,4.82694,6.745,4.83306), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.745,4.83306,8.63611,4.82694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.62194,4.89472,5.45194,4.92305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.45194,4.92305,8.62194,4.89472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.19917,4.96972,5.45194,4.92305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.46417,5.09778,5.39111,5.13416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.39111,5.13416,5.43305,5.13444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.43305,5.13444,5.39111,5.13416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.44083,5.15805,5.36528,5.1675), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.36528,5.1675,5.44083,5.15805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.82401,5.188,5.36528,5.1675), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.82805,5.23416,8.82401,5.188), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.34778,5.35583,5.43722,5.35972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.43722,5.35972,5.34778,5.35583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.50028,5.37611,5.57861,5.38083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.57861,5.38083,5.50028,5.37611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.43222,5.39111,5.57861,5.38083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.5375,5.42555,5.25389,5.43917), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.25389,5.43917,5.5375,5.42555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.55636,5.46294,5.25389,5.43917), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.63444,5.53,5.18833,5.54555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.18833,5.54555,5.63444,5.53), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.39917,5.56417,5.17528,5.57555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.17528,5.57555,5.49722,5.57639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.49722,5.57639,5.17528,5.57555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.21666,5.57916,5.49722,5.57639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.37083,5.59333,5.21666,5.57916), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.92028,5.6075,5.28861,5.6125), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.28861,5.6125,8.92028,5.6075), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.315,5.61805,5.50111,5.62055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.50111,5.62055,5.46472,5.62139), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.46472,5.62139,5.50111,5.62055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.43055,5.62278,5.46472,5.62139), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.43944,5.65333,5.43055,5.62278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.08639,5.69722,8.83361,5.71361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.83361,5.71361,5.33389,5.72055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.33389,5.72055,8.83361,5.71361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.05028,5.75472,5.09,5.77028), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.09,5.77028,5.15278,5.77528), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.15278,5.77528,5.09,5.77028), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.88888,5.78611,5.11739,5.79597), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.11739,5.79597,8.88888,5.78611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.15722,5.81611,8.86067,5.81982), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.86067,5.81982,5.15722,5.81611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.88,5.85472,5.25444,5.85639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.25444,5.85639,8.88,5.85472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.26805,5.90444,9.00639,5.91), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.00639,5.91,5.26805,5.90444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.00333,5.94472,9.00639,5.91), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.88389,6.005,9.00333,5.94472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.295,6.20778,4.62889,6.21666), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.62889,6.21666,9.295,6.20778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.36333,6.32583,9.435,6.32778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.435,6.32778,9.36333,6.32583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.39667,6.36389,2.7187,6.36446), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.7187,6.36446,4.39667,6.36389), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.88139,6.385,9.46861,6.40444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.46861,6.40444,3.42611,6.41194), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.42611,6.41194,3.38778,6.41333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.38778,6.41333,4.03972,6.41361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.03972,6.41361,3.38778,6.41333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.15472,6.43333,11.11694,6.44416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.11694,6.44416,9.57666,6.44694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.57666,6.44694,11.11694,6.44416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.39528,6.45055,9.57666,6.44694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.38889,6.46028,3.39528,6.45055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.38556,6.475,11.38889,6.46028), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.66694,6.51861,9.61222,6.52167), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.61222,6.52167,9.71139,6.52278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.71139,6.52278,9.61222,6.52167), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.47583,6.53611,3.63,6.54805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.63,6.54805,3.47583,6.53611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.77056,6.58972,11.07333,6.59111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.07333,6.59111,3.83889,6.59194), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.83889,6.59194,11.07333,6.59111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.4675,6.59472,11.445,6.59694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.445,6.59694,3.4675,6.59472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.51472,6.60417,3.84972,6.60722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.84972,6.60722,11.51472,6.60417), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.76167,6.61166,3.84972,6.60722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.74167,6.62194,3.76167,6.61166), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.5625,6.6675,2.79889,6.68861), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.79889,6.68861,11.08222,6.69778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.08222,6.69778,2.79889,6.68861), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.87472,6.77583,10.94333,6.77805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.94333,6.77805,11.01916,6.77861), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.01916,6.77861,10.94333,6.77805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.58777,6.78527,11.01916,6.77861), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.79555,6.80166,11.58777,6.78527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.55472,6.82111,10.88277,6.82778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.88277,6.82778,11.55472,6.82111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.25639,6.87583,10.51333,6.87805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.51333,6.87805,10.25639,6.87583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.5875,6.89222,10.205,6.90055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.205,6.90055,11.5875,6.89222), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.85416,6.94694,2.72528,6.95444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.72528,6.95444,10.85416,6.94694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.16722,7.01917,2.79833,7.04), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.79833,7.04,11.79139,7.05083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.79139,7.05083,10.62,7.05361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.62,7.05361,11.79139,7.05083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.88666,7.07805,2.74778,7.09916), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.74778,7.09916,11.88666,7.07805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.89416,7.12305,10.59611,7.13416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.59611,7.13416,2.77861,7.13444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.77861,7.13444,10.59611,7.13416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.74972,7.27055,11.86,7.40222), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.86,7.40222,2.80722,7.41722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.80722,7.41722,2.7575,7.41944), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.7575,7.41944,2.80722,7.41722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.76278,7.5475,12.04361,7.57778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.04361,7.57778,2.76278,7.5475), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.73333,7.63972,12.04361,7.57778), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.04361,7.73972,2.74222,7.80861), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.74222,7.80861,12.04361,7.73972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.68213,7.89365,12.22277,7.97472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.22277,7.97472,2.68213,7.89365), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.72583,8.08305,12.22277,7.97472), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.24722,8.39389,12.27527,8.42833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.27527,8.42833,12.35333,8.43027), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.35333,8.43027,12.27527,8.42833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.42166,8.51861,2.75583,8.58805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.75583,8.58805,12.56889,8.60055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.56889,8.60055,2.75583,8.58805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.38639,8.61305,12.56889,8.60055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.49249,8.62888,12.38639,8.61305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.6875,8.66083,12.49249,8.62888), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.72833,8.75944,12.79416,8.76583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.79416,8.76583,12.72833,8.75944), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.82416,8.8475,12.79416,8.76583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.78833,9.05416,2.9875,9.06111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((2.9875,9.06111,2.78833,9.05416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.84138,9.08555,3.095,9.09055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.095,9.09055,12.84138,9.08555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.90889,9.23527,3.17028,9.27444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.17028,9.27444,12.90889,9.23527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.91361,9.34361,12.84893,9.36008), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.84893,9.36008,12.91361,9.34361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.14167,9.44111,13.05278,9.50833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.05278,9.50833,13.15639,9.51583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.15639,9.51583,13.05278,9.50833), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.22111,9.55527,13.15639,9.51583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.26333,9.63583,3.31722,9.63611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.31722,9.63611,3.26333,9.63583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.25139,9.67694,3.36333,9.68194), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.36333,9.68194,13.25139,9.67694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.33111,9.75583,3.34667,9.80916), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.34667,9.80916,3.52528,9.84416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.52528,9.84416,3.34667,9.80916), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.22833,9.90972,3.60944,9.94805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.60944,9.94805,13.26555,9.98499), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.26555,9.98499,3.60944,9.94805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.24389,10.03166,13.26555,9.98499), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.26639,10.08611,13.39805,10.11111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.39805,10.11111,13.26639,10.08611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.68444,10.15111,13.39805,10.11111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.47055,10.19166,3.68444,10.15111), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.45861,10.23888,3.58194,10.27527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.58194,10.27527,13.45861,10.23888), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.78944,10.40277,3.63722,10.41166), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.63722,10.41166,3.78944,10.40277), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.68889,10.44972,3.63722,10.41166), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.855,10.585,13.57833,10.6825), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.57833,10.6825,3.84667,10.70305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.84667,10.70305,13.57833,10.6825), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.6375,10.75583,3.75389,10.79444), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.75389,10.79444,13.6375,10.75583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.73917,11.11666,3.69417,11.135), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.69417,11.135,3.73917,11.11666), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.88666,11.17055,3.69417,11.135), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.15805,11.23361,14.19583,11.25083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.19583,11.25083,14.15805,11.23361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.00916,11.28333,14.19583,11.25083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.475,11.42972,14.61777,11.50555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.61777,11.50555,14.64639,11.57583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.64639,11.57583,14.61777,11.50555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.64111,11.64694,3.60549,11.69169), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.60549,11.69169,14.55833,11.71527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.55833,11.71527,3.60549,11.69169), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.68833,11.74972,14.55833,11.71527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.64944,11.915,3.61694,11.91972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.61694,11.91972,14.64944,11.915), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.67167,11.97555,3.61694,11.91972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.61916,12.03555,3.67167,11.97555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.67416,12.15083,14.65722,12.18722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.65722,12.18722,14.67416,12.15083), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.55277,12.23222,14.65722,12.18722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.49083,12.33583,14.17389,12.38416), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.17389,12.38416,14.49083,12.33583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.18805,12.44389,14.18351,12.46948), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.18351,12.46948,14.18805,12.44389), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.65778,12.52888,14.18351,12.46948), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((3.95278,12.74888,9.63527,12.80277), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.63527,12.80277,8.985,12.84666), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.985,12.84666,9.63527,12.80277), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.66361,12.94333,7.09056,12.99527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.09056,12.99527,4.105,12.99638), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.105,12.99638,6.93333,12.99722), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.93333,12.99722,4.105,12.99638), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.47805,13.05666,8.43303,13.06478), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.43303,13.06478,8.55444,13.06667), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.55444,13.06667,8.43303,13.06478), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.37361,13.07333,8.55444,13.06667), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((14.07499,13.08159,8.4975,13.08583), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.4975,13.08583,14.07499,13.08159), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.37972,13.09972,12.14693,13.10119), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.14693,13.10119,7.37972,13.09972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.80639,13.10805,12.245,13.11027), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.245,13.11027,6.80639,13.10805), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.21889,13.12555,9.93,13.13305), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((9.93,13.13305,7.21889,13.12555), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.86722,13.24972,10.14833,13.25972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.14833,13.25972,11.86722,13.24972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.67055,13.27806,10.14833,13.25972), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.62916,13.30055,8.12222,13.30361), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((8.12222,13.30361,12.62916,13.30055), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.90318,13.32478,6.67917,13.34389), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.67917,13.34389,7.815,13.35278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((7.815,13.35278,11.03833,13.36027), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.03833,13.36027,7.815,13.35278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((11.44638,13.37639,10.84111,13.38611), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((10.84111,13.38611,11.44638,13.37639), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.8625,13.45028,4.1425,13.47694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.1425,13.47694,4.24778,13.48138), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.24778,13.48138,4.1425,13.47694), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((12.96861,13.51278,13.76189,13.52478), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.76189,13.52478,12.96861,13.51278), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.25139,13.58888,6.42306,13.60527), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.42306,13.60527,13.25139,13.58888), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.16083,13.64305,13.32361,13.67944), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.32361,13.67944,6.23805,13.68333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.23805,13.68333,6.285,13.68389), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((6.285,13.68389,6.23805,13.68333), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.47,13.68694,6.285,13.68389), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.62524,13.71821,13.34778,13.72), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.34778,13.72,13.62524,13.71821), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((13.34778,13.72,13.62524,13.71821), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.92361,13.73638,5.28611,13.75222), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.28611,13.75222,4.92361,13.73638), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((4.88555,13.78139,5.28611,13.75222), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.365,13.8475,5.54747,13.89313), mapfile, tile_dir, 0, 11, "ng-nigeria")
	render_tiles((5.54747,13.89313,5.365,13.8475), mapfile, tile_dir, 0, 11, "ng-nigeria")