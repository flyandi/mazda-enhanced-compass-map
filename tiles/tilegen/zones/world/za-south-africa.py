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
    # Region: ZA
    # Region Name: South Africa

	render_tiles((19.965,-34.815,20.05222,-34.80779), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.05222,-34.80779,19.965,-34.815), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.76138,-34.75973,20.05499,-34.7525), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.05499,-34.7525,19.76138,-34.75973), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.85778,-34.7525,19.76138,-34.75973), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.17333,-34.68,20.05499,-34.7525), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.32222,-34.59334,20.38749,-34.55946), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.38749,-34.55946,19.32222,-34.59334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.3575,-34.50028,20.46027,-34.48222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.46027,-34.48222,20.83888,-34.46529), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.83888,-34.46529,20.86472,-34.45418), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.86472,-34.45418,20.83888,-34.46529), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.66222,-34.44084,21.28722,-34.43334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.28722,-34.43334,20.66222,-34.44084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.19138,-34.42389,21.28722,-34.43334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.85027,-34.41251,19.28194,-34.40973), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.28194,-34.40973,20.85027,-34.41251), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.72222,-34.39751,18.84388,-34.38557), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.84388,-34.38557,21.72222,-34.39751), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.88944,-34.37029,21.06277,-34.36389), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.06277,-34.36389,19.08333,-34.36084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.08333,-34.36084,21.06277,-34.36389), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.80388,-34.35557,21.52833,-34.35251), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.52833,-34.35251,18.80388,-34.35557), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.4903,-34.34852,19.08333,-34.34639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.08333,-34.34639,18.4903,-34.34852), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.03888,-34.3439,19.12388,-34.34306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.12388,-34.34306,19.03888,-34.3439), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.89249,-34.34196,19.12388,-34.34306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.13166,-34.2975,18.47194,-34.25668), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.47194,-34.25668,18.84527,-34.245), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.84527,-34.245,18.47194,-34.25668), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.94555,-34.22835,18.84527,-34.245), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.83305,-34.20583,18.81527,-34.18417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.81527,-34.18417,22.14916,-34.17751), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.14916,-34.17751,24.5536,-34.17278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.5536,-34.17278,22.14916,-34.17751), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.11166,-34.16084,18.85527,-34.15611), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.85527,-34.15611,24.83694,-34.15446), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.83694,-34.15446,18.85527,-34.15611), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.30972,-34.14917,18.44027,-34.14667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.44027,-34.14667,18.30972,-34.14917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.40611,-34.11139,22.13888,-34.10056), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.13888,-34.10056,18.48805,-34.09779), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.48805,-34.09779,18.81333,-34.09695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.81333,-34.09695,18.48805,-34.09779), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.36583,-34.09278,23.04166,-34.09), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.04166,-34.09,23.36583,-34.09278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.06083,-34.08251,18.75999,-34.0789), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.75999,-34.0789,23.06083,-34.08251), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.60194,-34.07362,18.75999,-34.0789), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.4711,-34.05695,22.29527,-34.0539), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.29527,-34.0539,22.4711,-34.05695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.81277,-34.04806,25.62,-34.04779), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.62,-34.04779,22.81277,-34.04806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.37277,-34.04446,23.03888,-34.04223), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.03888,-34.04223,23.37277,-34.04446), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.76999,-34.03556,23.92555,-34.02917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.92555,-34.02917,25.7054,-34.02659), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.7054,-34.02659,23.92555,-34.02917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.78333,-34.01112,24.92027,-34.00279), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.92027,-34.00279,22.80666,-33.9964), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.80666,-33.9964,22.56583,-33.99583), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.56583,-33.99583,22.80666,-33.9964), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.53916,-33.99195,22.56583,-33.99583), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.72861,-33.98279,23.53916,-33.99195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.23666,-33.97362,22.72861,-33.98279), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.06055,-33.96251,25.23666,-33.97362), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.61749,-33.94222,25.06055,-33.96251), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.37277,-33.92085,25.61749,-33.94222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.4825,-33.89473,18.37277,-33.92085), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.6286,-33.8514,18.48722,-33.84778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.48722,-33.84778,25.6286,-33.8514), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.44805,-33.77445,25.71999,-33.77), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.71999,-33.77,26.44805,-33.77445), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.21027,-33.74029,25.9561,-33.71195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.9561,-33.71195,26.21027,-33.74029), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.99638,-33.56918,18.29472,-33.46613), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.29472,-33.46613,26.99638,-33.56918), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.14805,-33.35612,27.46666,-33.29557), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.46666,-33.29557,18.11555,-33.25834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.11555,-33.25834,27.46666,-33.29557), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.11972,-33.20444,18.12777,-33.17723), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.12777,-33.17723,18.11972,-33.20444), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.96722,-33.13473,18.01333,-33.1164), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.01333,-33.1164,18.03722,-33.10195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.03722,-33.10195,17.96611,-33.09807), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.96611,-33.09807,18.03722,-33.10195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.91388,-33.04806,18.03777,-33.03722), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.03777,-33.03722,17.89305,-33.03112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.89305,-33.03112,18.03777,-33.03722), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.91055,-33.01556,17.95777,-33.00529), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.95777,-33.00529,17.99694,-33.00251), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.99694,-33.00251,17.95777,-33.00529), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.09166,-32.88668,17.84527,-32.82029), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.84527,-32.82029,28.12166,-32.81751), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.12166,-32.81751,17.84527,-32.82029), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.90028,-32.79307,18.06499,-32.78112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.06499,-32.78112,18.13194,-32.77806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.13194,-32.77806,18.06499,-32.78112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.90249,-32.73335,17.97416,-32.69946), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.97416,-32.69946,17.90249,-32.73335), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.29222,-32.62446,28.53777,-32.57418), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.53777,-32.57418,18.29222,-32.62446), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.33361,-32.49112,28.53777,-32.57418), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.77916,-32.34196,18.34166,-32.2414), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.34166,-32.2414,28.77916,-32.34196), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.05388,-32.1014,18.34166,-32.2414), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.39722,-31.73195,18.21472,-31.72723), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.21472,-31.72723,29.39722,-31.73195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.40249,-31.68862,18.21472,-31.72723), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.62472,-31.58417,18.08388,-31.55945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.08388,-31.55945,29.62472,-31.58417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.76583,-31.44445,18.08388,-31.55945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.97916,-31.31861,17.85333,-31.25306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.85333,-31.25306,29.97916,-31.31861), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.3075,-30.94306,17.56527,-30.83583), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.56527,-30.83583,30.3075,-30.94306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.60277,-30.53028,17.31499,-30.40278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.31499,-30.40278,30.60277,-30.53028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.86111,-30.08973,17.16555,-30.01695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.16555,-30.01695,30.86111,-30.08973), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.00277,-29.90362,31.06626,-29.8821), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.06626,-29.8821,31.05555,-29.8689), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.05555,-29.8689,31.01249,-29.86806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.01249,-29.86806,31.05555,-29.8689), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.04277,-29.83028,31.01249,-29.86806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.99944,-29.51501,31.24583,-29.49167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.24583,-29.49167,16.99944,-29.51501), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.88361,-29.30167,31.58972,-29.12973), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.58972,-29.12973,16.81527,-29.08501), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.81527,-29.08501,16.75361,-29.04195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.75361,-29.04195,16.81527,-29.08501), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.72555,-28.98028,19.12944,-28.96083), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.12944,-28.96083,16.72555,-28.98028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.84249,-28.94056,19.00944,-28.93222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.00944,-28.93222,31.76749,-28.92778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.76749,-28.92778,19.00944,-28.93222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.1836,-28.90889,19.29499,-28.89361), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.29499,-28.89361,31.99666,-28.87834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.99666,-28.87834,18.95777,-28.86806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.95777,-28.86806,16.60388,-28.86667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.60388,-28.86667,18.95777,-28.86806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((18.63999,-28.84695,16.60388,-28.86667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.03249,-28.82334,31.99583,-28.82028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.99583,-28.82028,32.06888,-28.81778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.06888,-28.81778,31.99583,-28.82028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.23916,-28.79889,32.08665,-28.79834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.08665,-28.79834,19.23916,-28.79889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.99249,-28.79417,32.08665,-28.79834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.65555,-28.77584,31.99249,-28.79417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.60444,-28.75695,17.7086,-28.75167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.7086,-28.75167,17.60444,-28.75695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.27416,-28.73195,16.56833,-28.7239), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.56833,-28.7239,17.40944,-28.71639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.40944,-28.71639,16.56833,-28.7239), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.46638,-28.69972,17.59527,-28.69334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.59527,-28.69334,19.46638,-28.69972), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.46972,-28.63028,16.48302,-28.58034), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.48302,-28.58034,32.37166,-28.55612), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.37166,-28.55612,16.48302,-28.58034), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.56944,-28.52639,17.35999,-28.5164), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.35999,-28.5164,19.56944,-28.52639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.72777,-28.49611,17.35999,-28.5164), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.32194,-28.46778,16.67805,-28.46056), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.67805,-28.46056,17.32194,-28.46778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.42416,-28.44084,19.99725,-28.42636), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.99725,-28.42636,17.38805,-28.42305), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.38805,-28.42305,19.99725,-28.42636), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.80333,-28.36445,17.39916,-28.34778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.39916,-28.34778,16.80333,-28.36445), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.77888,-28.32473,32.46054,-28.31306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.46054,-28.31306,16.77888,-28.32473), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.82444,-28.26112,16.77361,-28.25945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.77361,-28.25945,16.82444,-28.26112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.22166,-28.24472,17.26138,-28.23694), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.26138,-28.23694,17.34888,-28.23667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.34888,-28.23667,17.26138,-28.23694), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.80722,-28.21833,16.85166,-28.21222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.85166,-28.21222,16.80722,-28.21833), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.18416,-28.2025,16.85166,-28.21222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.54527,-28.18417,16.88277,-28.17695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.88277,-28.17695,32.54527,-28.18417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.83861,-28.16861,16.88277,-28.17695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.88166,-28.13972,17.19416,-28.12139), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.19416,-28.12139,16.88166,-28.13972), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((16.91749,-28.06389,17.09499,-28.03639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((17.09499,-28.03639,16.91749,-28.06389), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.64332,-27.63556,19.9986,-27.47195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((19.9986,-27.47195,31.9836,-27.31667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.9836,-27.31667,31.51749,-27.31306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.51749,-27.31306,31.9836,-27.31667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.16166,-27.20305,32.82638,-27.14334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.82638,-27.14334,31.9586,-27.11278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.9586,-27.11278,32.82638,-27.14334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.98305,-27.02945,31.9586,-27.11278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.69234,-26.89468,21.14416,-26.86667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.14416,-26.86667,32.33305,-26.86028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.33305,-26.86028,21.14416,-26.86667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.52361,-26.85334,21.69305,-26.85305), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.69305,-26.85305,21.52361,-26.85334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.89309,-26.84644,32.13422,-26.84057), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.13422,-26.84057,32.89309,-26.84644), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.3486,-26.8275,32.13422,-26.84057), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.63694,-26.81278,30.81888,-26.81056), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.81888,-26.81056,32.00694,-26.80862), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.00694,-26.80862,30.81888,-26.81056), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.89721,-26.79473,32.00694,-26.80862), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.89944,-26.77195,21.77861,-26.77084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.77861,-26.77084,30.89944,-26.77195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.8036,-26.7025,21.75999,-26.69195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.75999,-26.69195,30.8036,-26.7025), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.79138,-26.67167,21.89888,-26.66833), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((21.89888,-26.66833,21.79138,-26.67167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.06721,-26.61473,21.89888,-26.66833), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.80222,-26.46222,20.61138,-26.44917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.61138,-26.44917,30.80222,-26.46222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.17944,-26.42778,20.61138,-26.44917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.69333,-26.38612,22.17944,-26.42778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.46194,-26.215,20.86082,-26.13361), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.86082,-26.13361,20.00027,-26.11945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.00027,-26.11945,20.86082,-26.13361), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.06388,-26.09139,20.00027,-26.11945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.71277,-25.99916,31.87805,-25.99556), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.87805,-25.99556,22.71277,-25.99916), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.96774,-25.95818,22.72249,-25.93723), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.72249,-25.93723,31.96774,-25.95818), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.12833,-25.91389,22.72249,-25.93723), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.80777,-25.83084,22.76916,-25.82306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.76916,-25.82306,24.80777,-25.83084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.91944,-25.81417,22.76916,-25.82306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.73027,-25.78889,20.75666,-25.78195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.75666,-25.78195,22.73027,-25.78889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.50444,-25.76306,24.39333,-25.76278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.39333,-25.76278,24.50444,-25.76306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.15221,-25.76222,24.39333,-25.76278), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.32055,-25.75528,25.15221,-25.76222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.38638,-25.74639,31.32055,-25.75528), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.4461,-25.73556,25.03694,-25.72861), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.03694,-25.72861,31.42166,-25.72834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.42166,-25.72834,25.03694,-25.72861), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.26611,-25.69667,20.67971,-25.67945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.67971,-25.67945,22.81888,-25.66806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.81888,-25.66806,24.00027,-25.65889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.00027,-25.65889,32.01998,-25.65028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.01998,-25.65028,24.00027,-25.65889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.00722,-25.62251,24.15027,-25.62167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((24.15027,-25.62167,25.58709,-25.62104), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.58709,-25.62104,24.15027,-25.62167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.68777,-25.57889,23.84416,-25.57417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.84416,-25.57417,20.68777,-25.57889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.81305,-25.56389,23.84416,-25.57417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.63194,-25.51889,31.98832,-25.51806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.98832,-25.51806,20.63194,-25.51889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.73444,-25.46222,20.67332,-25.45916), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.67332,-25.45916,23.73444,-25.46222), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((22.92638,-25.38389,20.55555,-25.38), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.55555,-25.38,22.92638,-25.38389), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.49638,-25.33389,23.10471,-25.30028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.10471,-25.30028,23.45832,-25.27834), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((23.45832,-25.27834,23.10471,-25.30028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.38999,-25.03695,32.01859,-25.035), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.01859,-25.035,20.38999,-25.03695), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.83221,-25.02806,32.01859,-25.035), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.17499,-24.89223,20.00141,-24.76363), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((20.00141,-24.76363,25.86595,-24.73866), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((25.86595,-24.73866,20.00141,-24.76363), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.40288,-24.63044,26.35999,-24.61889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.35999,-24.61889,26.40288,-24.63044), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.46471,-24.58056,26.35999,-24.61889), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((32.0161,-24.45945,26.55499,-24.43694), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.55499,-24.43694,32.0161,-24.45945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.84527,-24.26445,31.88583,-24.17112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.88583,-24.17112,26.84527,-24.26445), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.87638,-23.9275,31.76971,-23.85639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.76971,-23.85639,31.87638,-23.9275), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.95943,-23.75362,27.06721,-23.66083), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.06721,-23.66083,27.03249,-23.65472), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.03249,-23.65472,27.06721,-23.66083), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((26.99916,-23.64861,27.03249,-23.65472), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.6836,-23.61362,26.99916,-23.64861), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.10277,-23.57667,31.6836,-23.61362), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.55083,-23.47667,27.39582,-23.39084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.39582,-23.39084,27.52055,-23.38361), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.52055,-23.38361,27.39582,-23.39084), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.57277,-23.28945,27.73693,-23.23), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.73693,-23.23,27.60138,-23.22028), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.60138,-23.22028,27.73693,-23.23), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.56166,-23.18667,27.78611,-23.16528), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.78611,-23.16528,31.56166,-23.18667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.81749,-23.10917,27.92833,-23.05806), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.92833,-23.05806,27.81749,-23.10917), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.9386,-22.95556,28.02444,-22.92445), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.02444,-22.92445,27.9386,-22.95556), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.05222,-22.88139,28.04277,-22.84111), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.04277,-22.84111,28.05222,-22.88139), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.14916,-22.78,28.04277,-22.84111), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.18416,-22.68473,28.14916,-22.78), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.43971,-22.57362,28.63444,-22.56334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.63444,-22.56334,28.43971,-22.57362), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.91777,-22.45834,31.29763,-22.41614), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.29763,-22.41614,28.96582,-22.38639), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.96582,-22.38639,31.29763,-22.41614), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.29499,-22.34334,31.15583,-22.32167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((31.15583,-22.32167,28.96055,-22.31334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.96055,-22.31334,31.15583,-22.32167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.13305,-22.30056,30.86694,-22.29612), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.86694,-22.29612,30.2311,-22.29223), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((30.2311,-22.29223,30.86694,-22.29612), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.08749,-22.22417,29.37053,-22.19138), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.37053,-22.19138,29.45082,-22.16334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.37053,-22.19138,29.45082,-22.16334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.45082,-22.16334,29.76749,-22.13611), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.76749,-22.13611,29.45082,-22.16334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.09777,-30.65889,28.09194,-30.60334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.09194,-30.60334,27.73804,-30.59778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.73804,-30.59778,28.09194,-30.60334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.69083,-30.54861,27.73804,-30.59778), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.15999,-30.49833,28.14471,-30.45417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.14471,-30.45417,28.15999,-30.49833), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.56413,-30.40341,28.14471,-30.45417), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.2575,-30.32667,27.38099,-30.31601), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.38099,-30.31601,27.45971,-30.31195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.45971,-30.31195,27.38099,-30.31601), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.21749,-30.30112,27.45971,-30.31195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.24166,-30.26306,28.21749,-30.30112), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.32805,-30.13945,27.39527,-30.12833), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.39527,-30.12833,27.32805,-30.13945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.54472,-30.11445,28.78527,-30.10361), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.78527,-30.10361,28.54472,-30.11445), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.1611,-29.92112,29.15749,-29.85167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.15749,-29.85167,29.12471,-29.83223), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.12471,-29.83223,29.15749,-29.85167), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.16693,-29.6725,27.01472,-29.64173), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.01472,-29.64173,29.16693,-29.6725), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.31221,-29.57611,27.01472,-29.64173), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.29333,-29.505,27.34999,-29.48195), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.34999,-29.48195,29.29333,-29.505), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.35722,-29.45056,29.41805,-29.43333), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.41805,-29.43333,29.35722,-29.45056), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.45555,-29.35334,27.45666,-29.29111), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.45666,-29.29111,29.45555,-29.35334), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.33305,-29.0925,29.07388,-28.96667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((29.07388,-28.96667,27.73138,-28.94111), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.73138,-28.94111,29.07388,-28.96667), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.87999,-28.9125,28.9761,-28.88722), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.9761,-28.88722,27.98055,-28.88306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.98055,-28.88306,28.9761,-28.88722), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((27.92999,-28.85334,27.98055,-28.88306), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.14638,-28.72305,28.70041,-28.61367), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.70041,-28.61367,28.6511,-28.56945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.70041,-28.61367,28.6511,-28.56945), mapfile, tile_dir, 0, 11, "za-south-africa")
	render_tiles((28.6511,-28.56945,28.70041,-28.61367), mapfile, tile_dir, 0, 11, "za-south-africa")