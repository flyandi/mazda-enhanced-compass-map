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
    # Region: NC
    # Region Name: New Caledonia

	render_tiles((168.11501,-21.63278,167.8564,-21.59778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((168.11501,-21.63278,167.8564,-21.59778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.8564,-21.59778,168.11501,-21.63278), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((168.12379,-21.55806,167.8564,-21.59778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((168.02831,-21.46306,168.1461,-21.44695), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((168.1461,-21.44695,168.02831,-21.46306), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.96629,-21.41028,167.8105,-21.38195), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.8105,-21.38195,167.96629,-21.41028), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.9894,-21.34667,167.8105,-21.38195), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.3452,-21.17834,167.41611,-21.16001), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.3452,-21.17834,167.41611,-21.16001), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.41611,-21.16001,167.3452,-21.17834), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.3186,-21.13556,167.41611,-21.16001), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.15691,-21.08306,167.46519,-21.05806), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.46519,-21.05806,167.11549,-21.05251), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.11549,-21.05251,167.46519,-21.05806), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.39661,-21.01973,167.11549,-21.05251), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.3877,-20.95306,167.08189,-20.91945), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.08189,-20.91945,167.02161,-20.91473), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.02161,-20.91473,167.08189,-20.91945), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.2791,-20.90639,167.02161,-20.91473), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.1711,-20.85139,167.2791,-20.90639), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.18021,-20.78917,167.03799,-20.76112), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.03799,-20.76112,167.18021,-20.78917), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.3094,-20.72862,167.07111,-20.70778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.07111,-20.70778,167.3094,-20.72862), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.22411,-20.67973,167.07111,-20.70778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.8902,-22.39473,166.94769,-22.38), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.8902,-22.39473,166.94769,-22.38), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.94769,-22.38,166.8302,-22.37001), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.8302,-22.37001,166.94769,-22.38), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.83659,-22.31945,167.0172,-22.31667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.0172,-22.31667,166.83659,-22.31945), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.4633,-22.29056,167.0172,-22.31667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((167.0352,-22.23723,166.5183,-22.23417), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.5183,-22.23417,166.39751,-22.23195), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.39751,-22.23195,166.5183,-22.23417), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.4514,-22.2164,166.39751,-22.23195), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.34331,-22.19695,166.4514,-22.2164), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.9413,-22.16251,166.34331,-22.19695), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.9583,-22.10695,166.1089,-22.09528), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.1089,-22.09528,166.9583,-22.10695), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.8708,-22.03612,166.1638,-22.0289), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.1638,-22.0289,166.8708,-22.03612), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.68719,-21.98639,166.7186,-21.96084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.7186,-21.96084,165.98331,-21.95917), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.98331,-21.95917,166.7186,-21.96084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.0338,-21.94751,165.98331,-21.95917), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.0764,-21.92056,166.04021,-21.90612), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.04021,-21.90612,166.0764,-21.92056), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.8947,-21.87862,165.81239,-21.86417), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.81239,-21.86417,165.8947,-21.87862), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.4814,-21.79028,165.743,-21.75667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.743,-21.75667,165.6386,-21.74334), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.6386,-21.74334,165.743,-21.75667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.4194,-21.71001,165.6386,-21.74334), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.33971,-21.67306,165.47189,-21.65806), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.47189,-21.65806,166.33971,-21.67306), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.483,-21.62834,165.3969,-21.62112), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.3969,-21.62112,165.483,-21.62834), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.2291,-21.52112,165.99159,-21.50584), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.99159,-21.50584,165.2291,-21.52112), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.92329,-21.46723,165.9669,-21.45889), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.9669,-21.45889,165.9494,-21.45362), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.9494,-21.45362,165.9669,-21.45889), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((166.0114,-21.44556,165.9494,-21.45362), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.85271,-21.41945,165.1252,-21.41306), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.1252,-21.41306,165.9711,-21.40751), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.9711,-21.40751,165.1252,-21.41306), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.06129,-21.39389,165.9711,-21.40751), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.88361,-21.36834,165.0444,-21.35223), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.0444,-21.35223,164.9752,-21.34778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.9752,-21.34778,165.0444,-21.35223), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.94051,-21.33584,164.9752,-21.34778), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.9814,-21.30667,165.71381,-21.29723), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.71381,-21.29723,164.9814,-21.30667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.9097,-21.28362,165.71381,-21.29723), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.5847,-21.22056,165.603,-21.18084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.603,-21.18084,165.5847,-21.22056), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.8038,-21.08417,164.75,-21.08362), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.75,-21.08362,164.8038,-21.08417), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.408,-21.06334,164.75,-21.08362), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.668,-21.02362,165.408,-21.06334), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.4124,-20.94973,165.2964,-20.89973), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.2964,-20.89973,165.4124,-20.94973), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.3591,-20.76584,164.39799,-20.76084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.39799,-20.76084,164.3591,-20.76584), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.1627,-20.75389,164.39799,-20.76084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.40581,-20.7275,165.1627,-20.75389), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((165.00661,-20.69973,164.34689,-20.68362), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.34689,-20.68362,165.00661,-20.69973), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.2289,-20.55751,164.79939,-20.55667), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.79939,-20.55667,164.2289,-20.55751), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.12019,-20.39556,164.168,-20.37112), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.168,-20.37112,164.12019,-20.39556), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.32941,-20.32445,164.13519,-20.30723), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.13519,-20.30723,164.0186,-20.30167), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.0186,-20.30167,164.13519,-20.30723), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.45081,-20.2864,164.0186,-20.30167), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.0475,-20.24195,164.298,-20.24028), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.298,-20.24028,163.99969,-20.23945), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((163.99969,-20.23945,164.298,-20.24028), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.3138,-20.235,163.99969,-20.23945), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.133,-20.2,164.0522,-20.1689), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.0522,-20.1689,163.9922,-20.15584), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((163.9922,-20.15584,164.0522,-20.1689), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((164.02361,-20.14084,163.9922,-20.15584), mapfile, tile_dir, 0, 11, "nc-new-caledonia")
	render_tiles((163.9975,-20.08723,164.02361,-20.14084), mapfile, tile_dir, 0, 11, "nc-new-caledonia")