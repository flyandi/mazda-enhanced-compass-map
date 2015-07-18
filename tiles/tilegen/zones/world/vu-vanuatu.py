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
    # Region: VU
    # Region Name: Vanuatu

	render_tiles((168.1241,-16.35973,168.2988,-16.34112), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1241,-16.35973,168.2988,-16.34112), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.2988,-16.34112,168.1241,-16.35973), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.32471,-16.30973,168.2988,-16.34112), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3105,-16.27723,167.91969,-16.26528), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.91969,-16.26528,168.3105,-16.27723), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.9211,-16.21667,168.2233,-16.19806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.2233,-16.19806,167.9211,-16.21667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.0863,-16.17556,168.2233,-16.19806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1899,-16.09695,168.1433,-16.08973), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1433,-16.08973,168.1899,-16.09695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3725,-17.82806,168.52969,-17.80695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3725,-17.82806,168.52969,-17.80695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.52969,-17.80695,168.36909,-17.79667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.36909,-17.79667,168.3038,-17.79612), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3038,-17.79612,168.36909,-17.79667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.17081,-17.7514,168.30881,-17.74417), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.30881,-17.74417,168.17081,-17.7514), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.15131,-17.70889,168.2769,-17.70723), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.2769,-17.70723,168.15131,-17.70889), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.22411,-17.69806,168.58611,-17.69751), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.58611,-17.69751,168.22411,-17.69806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.5258,-17.65973,168.58611,-17.69751), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.4436,-17.54306,168.3111,-17.53139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3111,-17.53139,168.4436,-17.54306), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2711,-19,169.3244,-18.96251), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2711,-19,169.3244,-18.96251), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.3244,-18.96251,169.0891,-18.92889), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.0891,-18.92889,169.3286,-18.89834), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.3286,-18.89834,169.0891,-18.92889), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.97971,-18.8564,169.26801,-18.85389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.26801,-18.85389,168.97971,-18.8564), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.0016,-18.81639,169.19859,-18.80112), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.19859,-18.80112,169.0016,-18.81639), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.20911,-18.78028,169.2641,-18.77667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2641,-18.77667,169.20911,-18.78028), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.9805,-18.76362,169.2641,-18.77667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2438,-18.73639,169.17191,-18.72501), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.17191,-18.72501,169.2438,-18.73639), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.1508,-18.64445,169.0186,-18.64001), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.0186,-18.64001,169.1508,-18.64445), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.0966,-18.61973,169.0186,-18.64001), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.4744,-16.84695,168.1902,-16.8175), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.4744,-16.84695,168.1902,-16.8175), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1902,-16.8175,168.34,-16.78806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.34,-16.78806,168.45329,-16.77167), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.45329,-16.77167,168.3605,-16.76945), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3605,-16.76945,168.45329,-16.77167), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1188,-16.71223,168.3058,-16.68945), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.3058,-16.68945,168.2319,-16.68556), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.2319,-16.68556,168.3058,-16.68945), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.14191,-16.57945,168.1841,-16.57612), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1841,-16.57612,168.14191,-16.57945), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.44769,-19.64028,169.3497,-19.63695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.44769,-19.64028,169.3497,-19.63695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.3497,-19.63695,169.44769,-19.64028), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.46159,-19.58751,169.3497,-19.63695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.4839,-19.52695,169.46159,-19.58751), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2027,-19.46167,169.34689,-19.44501), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.34689,-19.44501,169.2027,-19.46167), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2325,-19.35028,169.35361,-19.33139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.35361,-19.33139,169.2805,-19.32778), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((169.2805,-19.32778,169.35361,-19.33139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1994,-15.99722,168.2491,-15.9875), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1994,-15.99722,168.2491,-15.9875), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.2491,-15.9875,168.1994,-15.99722), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1767,-15.91916,168.168,-15.88806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.168,-15.88806,168.1767,-15.91916), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1537,-15.82423,168.168,-15.88806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1152,-15.65222,168.1777,-15.48445), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1777,-15.48445,168.1505,-15.46861), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.1505,-15.46861,168.1777,-15.48445), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.495,-16.59334,167.7711,-16.54834), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.495,-16.59334,167.7711,-16.54834), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.7711,-16.54834,167.4174,-16.53223), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.4174,-16.53223,167.4519,-16.51723), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.4519,-16.51723,167.4174,-16.53223), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.6002,-16.49778,167.4519,-16.51723), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.8372,-16.4575,167.42191,-16.44973), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.42191,-16.44973,167.8372,-16.4575), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.7899,-16.41389,167.42191,-16.44973), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.7636,-16.33529,167.7899,-16.41389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.3761,-16.22612,167.18111,-16.14834), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.18111,-16.14834,167.47189,-16.14028), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.47189,-16.14028,167.18111,-16.14834), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.3266,-16.12528,167.47189,-16.14028), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.41299,-16.1064,167.3266,-16.12528), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.14439,-16.07445,167.41299,-16.1064), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.3275,-15.91167,167.1752,-15.90389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.1752,-15.90389,167.3275,-15.91167), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.2222,-15.87306,167.1752,-15.90389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.8633,-15.47945,167.7097,-15.47917), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.8633,-15.47945,167.7097,-15.47917), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.7097,-15.47917,167.8633,-15.47945), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.6725,-15.46334,167.7097,-15.47917), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.7283,-15.38639,167.6725,-15.46334), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((168.0047,-15.29445,167.8947,-15.29334), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.8947,-15.29334,168.0047,-15.29445), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.9747,-15.28084,167.8947,-15.29334), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.883,-10.87361,165.80969,-10.85417), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.883,-10.87361,165.80969,-10.85417), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.80969,-10.85417,165.883,-10.87361), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.8611,-10.82111,165.7775,-10.805), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.7775,-10.805,166.0638,-10.79473), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.0638,-10.79473,165.7775,-10.805), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.8363,-10.77389,165.96719,-10.77139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.96719,-10.77139,165.8363,-10.77389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.7939,-10.75389,165.96719,-10.77139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.8569,-10.70667,166.1658,-10.69917), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.1658,-10.69917,165.8569,-10.70667), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.15359,-10.675,165.95,-10.67167), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((165.95,-10.67167,166.15359,-10.675), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.8147,-15.66306,166.7652,-15.64639), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.8147,-15.66306,166.7652,-15.64639), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.7652,-15.64639,166.8147,-15.66306), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.07159,-15.59806,166.90269,-15.58278), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.90269,-15.58278,167.07159,-15.59806), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.2386,-15.52306,166.90269,-15.58278), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.6347,-15.42861,167.2386,-15.52306), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.63969,-15.31973,166.6347,-15.42861), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.80769,-15.15834,166.9333,-15.14695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.9333,-15.14695,167.0925,-15.14389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.0925,-15.14389,166.9333,-15.14695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.1219,-15.12334,167.0925,-15.14389), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.77161,-14.99889,167.0452,-14.96695), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.0452,-14.96695,166.96581,-14.94861), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.96581,-14.94861,167.0811,-14.93361), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.0811,-14.93361,167.0047,-14.92445), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((167.0047,-14.92445,167.0811,-14.93361), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.52161,-14.82139,166.7408,-14.81417), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.7408,-14.81417,166.52161,-14.82139), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.67551,-14.76861,166.7408,-14.81417), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.5519,-14.65389,166.6024,-14.62778), mapfile, tile_dir, 0, 11, "vu-vanuatu")
	render_tiles((166.6024,-14.62778,166.5519,-14.65389), mapfile, tile_dir, 0, 11, "vu-vanuatu")