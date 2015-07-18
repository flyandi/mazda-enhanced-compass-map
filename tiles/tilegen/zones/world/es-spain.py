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
    # Region: ES
    # Region Name: Spain

	render_tiles((-14.33306,28.04444,-14.47389,28.06861), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.33306,28.04444,-14.47389,28.06861), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.47389,28.06861,-14.33306,28.04444), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.49445,28.09972,-14.47389,28.06861), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.30917,28.14277,-14.14028,28.18361), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.14028,28.18361,-14.30917,28.14277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.95028,28.22472,-14.20972,28.22805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.20972,28.22805,-13.95028,28.22472), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.2025,28.29472,-14.20972,28.22805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.84417,28.40222,-13.85583,28.47861), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.85583,28.47861,-14.08778,28.49972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.08778,28.49972,-13.85583,28.47861), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.82056,28.57722,-14.0275,28.62305), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-14.0275,28.62305,-13.82056,28.57722), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.9725,28.72972,-13.86584,28.74777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.86584,28.74777,-13.9725,28.72972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.06361,39.2636,2.90222,39.35971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.06361,39.2636,2.90222,39.35971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.90222,39.35971,2.79306,39.36249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.79306,39.36249,3.24194,39.36443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.24194,39.36443,2.79306,39.36249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.52361,39.45304,2.72917,39.46777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.72917,39.46777,3.29194,39.47249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.29194,39.47249,2.72917,39.46777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.7225,39.52999,2.61889,39.55027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.61889,39.55027,2.35806,39.55749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.35806,39.55749,2.61889,39.55027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.37305,39.61082,3.46361,39.66138), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.46361,39.66138,2.37305,39.61082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.24778,39.73471,3.45278,39.74999), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.45278,39.74999,2.66083,39.76276), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.66083,39.76276,3.45278,39.74999), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.14722,39.77943,2.66083,39.76276), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.12389,39.82249,3.15444,39.83471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.15444,39.83471,3.12389,39.82249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.81583,39.86166,3.11194,39.86638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.11194,39.86638,3.20528,39.86749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.20528,39.86749,3.11194,39.86638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.09194,39.90137,3.13083,39.91749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.13083,39.91749,3.09194,39.90137), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.59083,27.73472,-15.78528,27.83749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.59083,27.73472,-15.78528,27.83749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.78528,27.83749,-15.37195,27.85972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.37195,27.85972,-15.78528,27.83749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.8175,27.93861,-15.37195,27.85972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.70833,28.07499,-15.46639,28.12666), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.46639,28.12666,-15.43028,28.14972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.43028,28.14972,-15.705,28.16583), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-15.705,28.16583,-15.43028,28.14972), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.6825,27.98694,-16.50139,28.04916), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.6825,27.98694,-16.50139,28.04916), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.50139,28.04916,-16.6825,27.98694), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.78139,28.12999,-16.50139,28.04916), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.34945,28.29888,-16.90417,28.35083), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.90417,28.35083,-16.35111,28.35806), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.35111,28.35806,-16.90417,28.35083), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.55833,28.3936,-16.35111,28.35806), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.11861,28.51638,-16.37611,28.53694), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.37611,28.53694,-16.11861,28.51638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.23667,28.56222,-16.12195,28.57694), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-16.12195,28.57694,-16.23667,28.56222), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.38305,38.83665,1.2225,38.87415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.38305,38.83665,1.2225,38.87415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.2225,38.87415,1.27722,38.87971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.27722,38.87971,1.2225,38.87415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.4125,38.89054,1.27722,38.87971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.2425,38.96804,1.585,39.00277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.585,39.00277,1.29222,39.00526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.29222,39.00526,1.585,39.00277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.35722,39.06971,1.60055,39.0961), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.60055,39.0961,1.51972,39.11832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.51972,39.11832,1.60055,39.0961), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.77306,28.83777,-13.86389,28.85388), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.77306,28.83777,-13.86389,28.85388), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.86389,28.85388,-13.77306,28.83777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.85222,28.90638,-13.86389,28.85388), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.48167,28.99694,-13.79361,29.05083), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.79361,29.05083,-13.48167,28.99694), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.55139,29.12027,-13.59389,29.13805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.59389,29.13805,-13.45556,29.14277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.45556,29.14277,-13.59389,29.13805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.4225,29.20805,-13.47639,29.24194), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-13.47639,29.24194,-13.4225,29.20805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.86111,28.47666,-17.81556,28.48055), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.86111,28.47666,-17.81556,28.48055), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.81556,28.48055,-17.86111,28.47666), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.78334,28.52916,-17.81556,28.48055), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.73917,28.60777,-17.75695,28.67333), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.75695,28.67333,-17.97056,28.71666), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.97056,28.71666,-17.71667,28.74055), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.71667,28.74055,-17.97056,28.71666), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.99501,28.77805,-17.71667,28.74055), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.78139,28.83888,-17.90445,28.84944), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-17.90445,28.84944,-17.78139,28.83888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.29528,39.81055,4.11639,39.86832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.29528,39.81055,4.11639,39.86832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.11639,39.86832,4.33667,39.87249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.33667,39.87249,4.11639,39.86832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.82667,39.92249,3.96555,39.9336), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.96555,39.9336,3.82667,39.92249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.2875,39.94666,3.96555,39.9336), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.84055,39.99582,4.22778,39.9961), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.22778,39.9961,3.84055,39.99582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.80194,40.00221,4.22778,39.9961), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.83139,40.05276,4.18083,40.05943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.18083,40.05943,4.13667,40.0636), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((4.13667,40.0636,4.18083,40.05943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.60556,35.99971,-5.42222,36.07582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.42222,36.07582,-5.77333,36.07721), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.77333,36.07721,-5.42222,36.07582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.34472,36.14999,-5.43278,36.17416), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.43278,36.17416,-5.38167,36.17832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.38167,36.17832,-5.43278,36.17416), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.31139,36.2336,-5.38167,36.17832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.15167,36.29694,-5.31139,36.2336), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.16556,36.41527,-6.23333,36.46138), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.23333,36.46138,-6.26306,36.48166), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.26306,36.48166,-4.67722,36.50166), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-4.67722,36.50166,-6.25028,36.50832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.25028,36.50832,-6.17278,36.51221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.17278,36.51221,-6.25028,36.50832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.23083,36.57499,-6.3925,36.62638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.3925,36.62638,-6.23083,36.57499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.69694,36.6811,-2.85972,36.69527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.85972,36.69527,-2.69694,36.6811), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.52944,36.71582,-6.44389,36.71888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.44389,36.71888,-2.17444,36.72027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.17444,36.72027,-6.44389,36.71888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-4.39833,36.72221,-2.17444,36.72027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.71389,36.73166,-4.39833,36.72221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.90611,36.74165,-2.92889,36.74971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.92889,36.74971,-3.90611,36.74165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.35556,36.78749,-6.35806,36.79471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.35806,36.79471,-6.35556,36.78749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.55556,36.81471,-2.025,36.82888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.025,36.82888,-2.29889,36.82943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.29889,36.82943,-2.025,36.82888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.42056,36.85666,-2.29889,36.82943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.99889,36.88416,-6.33972,36.88943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.33972,36.88943,-1.99889,36.88416), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.89722,36.9461,-6.33972,36.88943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.57389,37.01582,-1.89722,36.9461), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.90472,37.16554,-7.38417,37.16971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.38417,37.16971,-6.95389,37.17221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.95389,37.17221,-7.41817,37.17337), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.41817,37.17337,-6.95389,37.17221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.9375,37.19388,-7.41817,37.17337), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.17472,37.22749,-1.76917,37.24527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.76917,37.24527,-1.78669,37.24695), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.78669,37.24695,-1.76917,37.24527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.82136,37.26775,-6.97222,37.28526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.97222,37.28526,-1.82136,37.26775), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.38444,37.52583,-1.26917,37.55527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.26917,37.55527,-7.51417,37.57332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.51417,37.57332,-1.26917,37.55527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.81667,37.57332,-1.26917,37.55527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.74083,37.63026,-0.69056,37.63138), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.69056,37.63138,-0.74083,37.63026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.86,37.71665,-0.75111,37.77693), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.75111,37.77693,-0.74722,37.79027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.74722,37.79027,-0.75111,37.77693), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.7875,37.81332,-0.74722,37.79027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.2975,37.84804,-0.75944,37.86193), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.75944,37.86193,-7.2975,37.84804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.68389,37.96888,-7.25472,37.98749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.25472,37.98749,-7.14611,38.00526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.14611,38.00526,-7.25472,37.98749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.00695,38.02804,-7.14611,38.00526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.65806,38.05193,-7.00695,38.02804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.94417,38.16276,-7.08778,38.17443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.08778,38.17443,-0.61222,38.17888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.61222,38.17888,-7.08778,38.17443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.50917,38.21165,-6.94805,38.21832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.94805,38.21832,-0.50917,38.21165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.14417,38.27026,-0.5175,38.29443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.5175,38.29443,-7.14417,38.27026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.47639,38.33804,-0.5175,38.29443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.39917,38.4086,-7.30707,38.4256), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.30707,38.4256,-0.39917,38.4086), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.32972,38.4472,-7.30707,38.4256), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.30111,38.52499,-0.06528,38.54582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.06528,38.54582,-7.30111,38.52499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.29225,38.57066,-0.06528,38.54582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.05056,38.59554,-7.29225,38.57066), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.03361,38.62943,-0.05056,38.59554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.26556,38.70832,0.23611,38.74443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.23611,38.74443,-7.26556,38.70832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.15583,38.79027,0.18889,38.8086), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.18889,38.8086,-7.15583,38.79027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.00889,38.86221,0.18889,38.8086), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.10833,38.94582,-6.95389,39.02693), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.95389,39.02693,-6.96111,39.05665), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.96111,39.05665,-6.95389,39.02693), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.11583,39.10416,-7.15417,39.12276), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.15417,39.12276,-7.11583,39.10416), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.04056,39.12276,-7.11583,39.10416), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.14,39.17332,-7.24305,39.21304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.24305,39.21304,-7.14,39.17332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.275,39.2711,-7.235,39.27637), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.235,39.27637,-0.275,39.2711), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.31361,39.3447,-7.235,39.27637), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.3375,39.44054,-7.29361,39.46776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.29361,39.46776,-0.3375,39.44054), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.4425,39.55109,-7.29361,39.46776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.40778,39.64832,-7.53333,39.66888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.53333,39.66888,-7.01722,39.67499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.01722,39.67499,-7.53333,39.66888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.17944,39.7361,-7.01722,39.67499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.06194,39.86332,-0.17944,39.7361), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.86389,40.01554,0.04889,40.0361), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.04889,40.0361,-6.86389,40.01554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.13194,40.07082,0.04889,40.0361), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.01444,40.14665,0.18639,40.16527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.18639,40.16527,-7.01444,40.14665), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.26139,40.20832,-7.00611,40.23081), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.00611,40.23081,-6.96083,40.24026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.96083,40.24026,-7.00611,40.23081), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.7875,40.34165,-6.96083,40.24026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.84833,40.44331,-6.79111,40.51804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.79111,40.51804,0.54639,40.5736), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.54639,40.5736,-6.83917,40.57499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.83917,40.57499,0.54639,40.5736), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.63556,40.62388,-6.79778,40.65776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.79778,40.65776,0.85167,40.6761), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.85167,40.6761,-6.79778,40.65776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.895,40.72832,-6.82944,40.75526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.82944,40.75526,0.72306,40.77583), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.72306,40.77583,-6.82944,40.75526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.70417,40.81304,0.72306,40.77583), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.79972,40.85609,0.70417,40.81304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.89167,40.9747,-6.92444,41.03137), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.92444,41.03137,-6.80861,41.04054), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.80861,41.04054,0.99306,41.04804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.99306,41.04804,-6.80861,41.04054), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.08889,41.06304,0.99306,41.04804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.21333,41.10471,1.08889,41.06304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.66667,41.19971,-6.68139,41.21554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.68139,41.21554,1.66667,41.19971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.59833,41.24415,-6.68139,41.21554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.12694,41.29555,-6.43389,41.32249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.43389,41.32249,2.12694,41.29555), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.31861,41.38721,-6.32944,41.41526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.32944,41.41526,-6.31861,41.38721), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.24972,41.44804,-6.32944,41.41526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.21778,41.52943,2.49528,41.5486), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.49528,41.5486,-6.21778,41.52943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.19444,41.59304,2.49528,41.5486), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.49778,41.65749,-6.35583,41.67776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.35583,41.67776,-6.53944,41.67943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.53944,41.67943,-6.35583,41.67776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.81944,41.68582,-6.53944,41.67943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.56278,41.74526,2.81944,41.68582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.14083,41.80915,-7.42722,41.81248), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.42722,41.81248,-8.14083,41.80915), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.61194,41.83498,-7.52445,41.84054), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.52445,41.84054,-7.61194,41.83498), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.88,41.85277,-7.45694,41.86443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.45694,41.86443,-7.98194,41.86638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.98194,41.86638,-7.45694,41.86443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.50861,41.87387,3.17944,41.87415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.17944,41.87415,-6.50861,41.87387), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.87195,41.87582,3.17944,41.87415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.59167,41.87971,-7.20056,41.8836), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.20056,41.8836,-6.56861,41.88721), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.56861,41.88721,-7.91222,41.88971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.91222,41.88971,-6.56861,41.88721), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.70611,41.90443,-8.8124,41.90453), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.8124,41.90453,-7.70611,41.90443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.21889,41.9136,-8.8124,41.90453), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.88611,41.92332,-8.21889,41.9136), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.545,41.9372,-6.62806,41.94109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.62806,41.94109,-6.545,41.9372), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.83778,41.9472,-6.62806,41.94109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.58167,41.96748,-6.83778,41.9472), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.15222,41.98859,-6.80944,41.99026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.80944,41.99026,-7.15222,41.98859), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.08278,42.02526,-8.62111,42.0536), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.62111,42.0536,-8.18583,42.0647), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.18583,42.0647,-8.09,42.06888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.09,42.06888,-8.18583,42.0647), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.2025,42.07888,-8.09,42.06888), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.89861,42.10805,3.13389,42.11832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.13389,42.11832,-8.89861,42.10805), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.20139,42.15221,3.13389,42.11832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.11083,42.19666,-8.77833,42.20776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.77833,42.20776,3.11083,42.19666), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.22222,42.23499,-8.8575,42.24776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.8575,42.24776,3.22222,42.23499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.15861,42.26249,-8.8575,42.24776), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.68389,42.27971,-8.81361,42.27999), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.81361,42.27999,-8.68389,42.27971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.85389,42.30777,-8.61278,42.31554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.61278,42.31554,-8.85389,42.30777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.31722,42.32499,-8.61278,42.31554), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.67111,42.33832,-8.76722,42.34026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.76722,42.34026,2.67111,42.33832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.83083,42.34277,-8.76722,42.34026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.1875,42.3486,2.47667,42.35165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.47667,42.35165,-8.62528,42.3536), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.62528,42.3536,2.47667,42.35165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.08694,42.36332,-8.62528,42.3536), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.97667,42.3747,2.08694,42.36332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.84111,42.39138,2.67833,42.40165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.67833,42.40165,-8.84111,42.39138), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.65639,42.42776,3.15083,42.43332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.15083,42.43332,2.25417,42.43471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((2.25417,42.43471,3.15083,42.43332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.53333,42.4361,1.95306,42.4372), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.95306,42.4372,3.17696,42.43761), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.17696,42.43761,1.95306,42.4372), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.44833,42.45082,-8.86972,42.4536), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.86972,42.4536,1.44833,42.45082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.94222,42.46693,1.71097,42.4735), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.71097,42.4735,3.03833,42.47498), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((3.03833,42.47498,1.71097,42.4735), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.7875,42.49026,-8.87195,42.50082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.87195,42.50082,-8.8075,42.50471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.8075,42.50471,-8.87195,42.50082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.01722,42.52138,-8.8075,42.50471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.03528,42.56805,1.44639,42.57221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.44639,42.57221,-8.82778,42.57471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.82778,42.57471,-9.085,42.57638), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.085,42.57638,-8.82778,42.57471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.43525,42.59715,-8.85611,42.60777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.43525,42.59715,-8.85611,42.60777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.85611,42.60777,1.43525,42.59715), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.88639,42.63999,-8.73083,42.6611), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.73083,42.6611,-8.825,42.66582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.825,42.66582,-8.73083,42.6611), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.29111,42.67582,-8.825,42.66582), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.67611,42.68915,1.38361,42.6897), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((1.38361,42.6897,0.67611,42.68915), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.0575,42.69415,0.4125,42.69526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.4125,42.69526,-0.0575,42.69415), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.26194,42.71748,0.4125,42.69526), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.66806,42.74804,-9.06861,42.75471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.06861,42.75471,-0.12389,42.75749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.12389,42.75749,-9.06861,42.75471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.93361,42.77277,-8.95389,42.77583), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.95389,42.77583,-8.93361,42.77277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.18778,42.78582,-0.51917,42.79082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.51917,42.79082,-9.13583,42.7911), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.13583,42.7911,-0.51917,42.79082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.39222,42.79638,-9.13583,42.7911), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.85611,42.81944,-0.50083,42.82221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.50083,42.82221,-9.10167,42.82332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.10167,42.82332,-0.50083,42.82221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.90389,42.82832,-9.10167,42.82332), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.66778,42.83915,0.81583,42.84109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((0.81583,42.84109,0.66778,42.83915), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.30667,42.84915,0.81583,42.84109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.14194,42.86166,-0.65611,42.8636), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.65611,42.8636,-9.14194,42.86166), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.18139,42.91554,-9.24528,42.92194), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.24528,42.92194,-9.29333,42.92249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.29333,42.92249,-9.24528,42.92194), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.81889,42.94609,-9.185,42.95249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.185,42.95249,-0.81889,42.94609), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-0.74694,42.96554,-9.185,42.95249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.27417,43.02471,-1.36028,43.03165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.36028,43.03165,-9.27417,43.02471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.44056,43.04832,-1.36028,43.03165), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.30056,43.07166,-1.47278,43.09109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.47278,43.09109,-1.28833,43.10609), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.28833,43.10609,-1.47278,43.09109), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.14556,43.19305,-1.38195,43.19665), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.38195,43.19665,-9.14556,43.19305), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-9.02528,43.21304,-1.38195,43.19665), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.93472,43.22971,-9.02528,43.21304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.62,43.25638,-1.40917,43.27304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.40917,43.27304,-8.98195,43.2761), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.98195,43.2761,-1.40917,43.27304), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.72445,43.2911,-2.29556,43.2961), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.29556,43.2961,-8.72445,43.2911), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.65583,43.30943,-8.53945,43.30971), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.53945,43.30971,-1.65583,43.30943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.21583,43.33054,-8.38611,43.33943), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.38611,43.33943,-1.88,43.34499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.88,43.34499,-8.8375,43.34527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.8375,43.34527,-1.88,43.34499), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.11861,43.35082,-8.8375,43.34527), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.33556,43.37332,-3.00917,43.3811), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.00917,43.3811,-8.40583,43.38471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.40583,43.38471,-1.81049,43.38589), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-1.81049,43.38589,-8.40583,43.38471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-4.21695,43.39388,-8.21667,43.39804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.21667,43.39804,-3.23139,43.39832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.23139,43.39832,-8.21667,43.39804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.32972,43.40388,-3.23139,43.39832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.42972,43.4111,-3.79722,43.41221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.79722,43.41221,-3.42972,43.4111), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-4.69222,43.41749,-3.79722,43.41221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.72833,43.42582,-8.28528,43.43277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.28528,43.43277,-2.94167,43.43555), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-2.94167,43.43555,-8.28528,43.43277), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.46056,43.44221,-4.05139,43.44249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-4.05139,43.44249,-3.46056,43.44221), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.82361,43.44888,-4.05139,43.44249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.33972,43.45693,-3.43056,43.46443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.43056,43.46443,-8.33972,43.45693), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.21472,43.47971,-7.04528,43.48999), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.04528,43.48999,-8.24111,43.49026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.24111,43.49026,-7.04528,43.48999), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.51556,43.49082,-8.24111,43.49026), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-3.65417,43.49165,-3.51556,43.49082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.38139,43.5261,-5.28889,43.53387), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.28889,43.53387,-5.38139,43.5261), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.52917,43.5486,-5.39806,43.55249), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.39806,43.55249,-5.52917,43.5486), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.03889,43.55749,-8.25361,43.55804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.25361,43.55804,-7.03889,43.55749), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.34972,43.5586,-8.25361,43.55804), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.31944,43.56221,-6.34972,43.5586), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.16361,43.57221,-6.94361,43.57777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-6.94361,43.57777,-7.24889,43.57915), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.24889,43.57915,-6.94361,43.57777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.95472,43.58054,-7.24889,43.57915), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-5.85194,43.65276,-8.08945,43.66193), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-8.08945,43.66193,-7.85444,43.66832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.85444,43.66832,-7.89917,43.67027), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.89917,43.67027,-7.85444,43.66832), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.57056,43.71165,-7.60556,43.71333), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.60556,43.71333,-7.85083,43.71471), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.85083,43.71471,-7.60556,43.71333), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.48417,43.72777,-7.69361,43.73082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.69361,43.73082,-7.48417,43.72777), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.79083,43.73415,-7.69361,43.73082), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.86583,43.77221,-7.68583,43.77443), mapfile, tile_dir, 0, 11, "es-spain")
	render_tiles((-7.68583,43.77443,-7.86583,43.77221), mapfile, tile_dir, 0, 11, "es-spain")