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
    # Region: NA
    # Region Name: Namibia

	render_tiles((19.12944,-28.96083,19.00944,-28.93222), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.00944,-28.93222,18.1836,-28.90889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.1836,-28.90889,19.29499,-28.89361), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.29499,-28.89361,18.1836,-28.90889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.95777,-28.86806,18.63999,-28.84695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.63999,-28.84695,18.95777,-28.86806), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.23916,-28.79889,17.65555,-28.77584), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.65555,-28.77584,17.60444,-28.75695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.60444,-28.75695,17.7086,-28.75167), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.7086,-28.75167,17.60444,-28.75695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.27416,-28.73195,17.40944,-28.71639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.40944,-28.71639,19.27416,-28.73195), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.46638,-28.69972,17.59527,-28.69334), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.59527,-28.69334,19.46638,-28.69972), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.44917,-28.61778,16.42222,-28.6075), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.42222,-28.6075,16.44917,-28.61778), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.48302,-28.58034,16.42222,-28.6075), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.56944,-28.52639,17.35999,-28.5164), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.35999,-28.5164,19.56944,-28.52639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.72777,-28.49611,17.35999,-28.5164), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.32194,-28.46778,16.67805,-28.46056), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.67805,-28.46056,17.32194,-28.46778), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.99725,-28.42636,17.38805,-28.42305), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.38805,-28.42305,19.99725,-28.42636), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.80333,-28.36445,17.39916,-28.34778), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.39916,-28.34778,16.11361,-28.33389), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.11361,-28.33389,16.77888,-28.32473), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.77888,-28.32473,16.11361,-28.33389), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.82444,-28.26112,16.77361,-28.25945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.77361,-28.25945,16.82444,-28.26112), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.22166,-28.24472,17.26138,-28.23694), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.26138,-28.23694,17.34888,-28.23667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.34888,-28.23667,17.26138,-28.23694), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.80722,-28.21833,16.85166,-28.21222), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.85166,-28.21222,16.80722,-28.21833), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.18416,-28.2025,16.85166,-28.21222), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.88277,-28.17695,16.83861,-28.16861), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.83861,-28.16861,16.88277,-28.17695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.88166,-28.13972,17.19416,-28.12139), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.19416,-28.12139,16.88166,-28.13972), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((16.91749,-28.06389,17.09499,-28.03639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.09499,-28.03639,16.91749,-28.06389), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.73027,-28.0025,17.09499,-28.03639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.67,-27.8714,15.73027,-28.0025), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.53416,-27.73611,15.52361,-27.68862), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.52361,-27.68862,15.53416,-27.73611), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.9986,-27.47195,15.29222,-27.3175), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.29222,-27.3175,19.9986,-27.47195), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.27,-27.16223,15.29222,-27.3175), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.21416,-26.93028,15.18027,-26.92417), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.18027,-26.92417,15.21416,-26.93028), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.14194,-26.67778,15.07972,-26.65001), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.07972,-26.65001,15.13472,-26.62889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.13472,-26.62889,15.07972,-26.65001), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.17111,-26.59639,15.13472,-26.62889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.08805,-26.40139,14.96833,-26.33973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.96833,-26.33973,15.08805,-26.40139), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.93861,-26.13945,14.97805,-26.12667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.97805,-26.12667,20.00027,-26.11945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.00027,-26.11945,14.97805,-26.12667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.98,-26.0575,20.00027,-26.11945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.83417,-25.7389,14.88139,-25.54889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.88139,-25.54889,14.81278,-25.36), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.81278,-25.36,14.88139,-25.54889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.855,-25.05056,14.79055,-24.93223), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.79055,-24.93223,14.79694,-24.84834), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.79694,-24.84834,14.79055,-24.93223), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.00141,-24.76363,14.72666,-24.70973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.72666,-24.70973,20.00141,-24.76363), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.59916,-24.56556,14.61583,-24.46695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.61583,-24.46695,14.59916,-24.56556), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.48028,-24.17334,14.61583,-24.46695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.50083,-23.64278,19.99888,-23.42667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.99888,-23.42667,14.43444,-23.41778), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.43444,-23.41778,19.99888,-23.42667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.44916,-23.38278,14.48861,-23.37973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.48861,-23.37973,14.44916,-23.38278), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.49777,-23.31806,14.48861,-23.37973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.45084,-23.15116,14.44761,-23.13968), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.45084,-23.15116,14.44761,-23.13968), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.44761,-23.13968,14.45084,-23.15116), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.4675,-22.97722,14.41055,-22.96723), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.41055,-22.96723,14.4675,-22.97722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.44444,-22.87972,14.53611,-22.87723), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.53611,-22.87723,14.44444,-22.87972), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.5275,-22.68528,14.52833,-22.68195), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.52833,-22.68195,14.5275,-22.68528), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.51139,-22.55278,14.52833,-22.68195), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.27777,-22.11028,19.99666,-22.07389), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.99666,-22.07389,14.27777,-22.11028), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.99582,-22.0014,20.99194,-21.99695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.99194,-21.99695,19.99582,-22.0014), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.94916,-21.78195,13.96611,-21.71945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.96611,-21.71945,13.94916,-21.78195), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.79944,-21.41639,13.96611,-21.71945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.525,-21.02306,20.99332,-20.64722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.99332,-20.64722,13.31361,-20.56917), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.31361,-20.56917,20.99332,-20.64722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.24222,-20.4025,13.31361,-20.56917), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.15972,-20.15473,13.05694,-20.07584), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.05694,-20.07584,13.15972,-20.15473), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.025,-19.98028,13.05694,-20.07584), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.78611,-19.55834,20.9936,-19.29972), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.9936,-19.29972,12.5725,-19.11973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.5725,-19.11973,12.46416,-19.00417), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.46416,-19.00417,12.46333,-18.93251), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.46333,-18.93251,12.46416,-19.00417), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.36028,-18.79195,12.46333,-18.93251), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.61555,-18.48528,23.57083,-18.46722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.57083,-18.46722,12.01833,-18.46056), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.01833,-18.46056,23.57083,-18.46722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.82333,-18.32251,20.99544,-18.31741), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.99544,-18.31741,23.54888,-18.31723), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.54888,-18.31723,20.99544,-18.31741), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((21.46249,-18.30445,23.54888,-18.31723), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.46194,-18.22639,23.98888,-18.16445), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.98888,-18.16445,23.46194,-18.22639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.4961,-18.05945,11.79194,-18.04778), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((11.79194,-18.04778,24.4961,-18.05945), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.60194,-18.02056,20.78486,-18.01173), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.78486,-18.01173,24.46416,-18.00806), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((21.38264,-18.01173,24.46416,-18.00806), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.46416,-18.00806,21.4236,-18.00667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((21.4236,-18.00667,24.46416,-18.00806), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.29145,-17.99815,21.4236,-18.00667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.97166,-17.9625,24.38194,-17.94667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.38194,-17.94667,21.24277,-17.93834), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((21.24277,-17.93834,24.38194,-17.94667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.75555,-17.8975,20.09972,-17.89556), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.09972,-17.89556,19.75555,-17.8975), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((20.3936,-17.8875,20.09972,-17.89556), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.45971,-17.86167,19.91527,-17.85751), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.91527,-17.85751,19.45971,-17.86167), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.82333,-17.84056,19.66249,-17.83723), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((19.66249,-17.83723,24.82333,-17.84056), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.91666,-17.81501,25.26575,-17.79766), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((25.26575,-17.79766,18.91666,-17.81501), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((11.73333,-17.7675,22.83777,-17.74722), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((22.83777,-17.74722,11.73333,-17.7675), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.71971,-17.70695,25.11832,-17.69473), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((25.11832,-17.69473,18.71971,-17.70695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((23.47486,-17.62453,25.03277,-17.5825), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((25.03277,-17.5825,23.47486,-17.62453), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.76388,-17.50973,24.23567,-17.48188), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((24.23567,-17.48188,24.76388,-17.50973), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.48333,-17.44028,13.98083,-17.425), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.98083,-17.425,14.18777,-17.41639), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.18777,-17.41639,13.98083,-17.425), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((17.0225,-17.39194,15.62611,-17.38889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((15.62611,-17.38889,14.21805,-17.38695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.21805,-17.38695,18.40734,-17.38678), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((18.40734,-17.38678,14.21805,-17.38695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((14.20931,-17.38678,14.21805,-17.38695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((11.75375,-17.25786,12.45166,-17.25362), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.45166,-17.25362,11.75375,-17.25786), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.50861,-17.23889,12.24694,-17.22667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.24694,-17.22667,12.50861,-17.23889), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.42833,-17.20556,12.24694,-17.22667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((11.97694,-17.16361,12.12305,-17.14834), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.12305,-17.14834,13.54472,-17.13667), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.54472,-17.13667,12.12305,-17.14834), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.49694,-17.02695,12.93888,-17.01139), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((12.93888,-17.01139,13.49694,-17.02695), mapfile, tile_dir, 0, 11, "na-namibia")
	render_tiles((13.37416,-16.96889,12.93888,-17.01139), mapfile, tile_dir, 0, 11, "na-namibia")