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
    # Region: OM
    # Region Name: Oman

	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.65499,20.16833,58.90193,20.34555), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.72804,20.21805,58.65499,20.43361), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.72804,20.21805,58.65499,20.43361), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.72804,20.21805,58.65499,20.43361), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.72804,20.21805,58.65499,20.43361), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.63471,20.23499,58.90193,20.34555), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.63471,20.23499,58.90193,20.34555), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.63471,20.23499,58.90193,20.34555), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.63471,20.23499,58.90193,20.34555), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.63943,20.34555,58.90193,20.23499), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.63943,20.34555,58.90193,20.23499), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.63943,20.34555,58.90193,20.23499), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.63943,20.34555,58.90193,20.23499), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.8061,20.37083,58.65499,20.43055), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.8061,20.37083,58.65499,20.43055), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.8061,20.37083,58.65499,20.43055), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.8061,20.37083,58.65499,20.43055), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.75054,20.43055,58.90193,20.21805), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.75054,20.43055,58.90193,20.21805), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.75054,20.43055,58.90193,20.21805), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.75054,20.43055,58.90193,20.21805), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.7111,20.43361,58.90193,20.21805), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.7111,20.43361,58.90193,20.21805), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.7111,20.43361,58.90193,20.21805), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.7111,20.43361,58.90193,20.21805), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.95221,20.51694,58.65499,20.6925), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.95221,20.51694,58.65499,20.6925), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.95221,20.51694,58.65499,20.6925), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.95221,20.51694,58.65499,20.6925), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.90193,20.6925,58.65499,20.51694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.90193,20.6925,58.65499,20.51694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.90193,20.6925,58.65499,20.51694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.90193,20.6925,58.65499,20.51694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.07825,16.64386,56.02462,16.67694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.07825,16.64386,56.02462,16.67694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.07825,16.64386,56.02462,16.67694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.07825,16.64386,56.02462,16.67694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.17138,16.67694,56.02462,16.64386), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.17138,16.67694,56.02462,16.64386), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.17138,16.67694,56.02462,16.64386), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.17138,16.67694,56.02462,16.64386), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.55332,16.74861,56.02462,16.77527), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.55332,16.74861,56.02462,16.77527), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.55332,16.74861,56.02462,16.77527), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.55332,16.74861,56.02462,16.77527), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.65471,16.77527,56.02462,16.74861), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.65471,16.77527,56.02462,16.74861), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.65471,16.77527,56.02462,16.74861), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.65471,16.77527,56.02462,16.74861), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.82221,16.88472,56.02462,16.90333), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.82221,16.88472,56.02462,16.90333), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.82221,16.88472,56.02462,16.90333), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.82221,16.88472,56.02462,16.90333), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((53.97137,16.90333,56.02462,17.01139), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((53.97137,16.90333,56.02462,17.01139), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((53.97137,16.90333,56.02462,17.01139), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((53.97137,16.90333,56.02462,17.01139), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((54.79582,16.94555,56.02462,17.025), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((54.79582,16.94555,56.02462,17.025), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((54.79582,16.94555,56.02462,17.025), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((54.79582,16.94555,56.02462,17.025), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((54.08554,17.01139,56.02462,16.90333), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((54.08554,17.01139,56.02462,16.90333), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((54.08554,17.01139,56.02462,16.90333), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((54.08554,17.01139,56.02462,16.90333), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((54.6511,17.025,56.02462,16.94555), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((54.6511,17.025,56.02462,16.94555), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((54.6511,17.025,56.02462,16.94555), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((54.6511,17.025,56.02462,16.94555), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.06527,17.03666,56.02462,17.17361), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.06527,17.03666,56.02462,17.17361), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.06527,17.03666,56.02462,17.17361), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.06527,17.03666,56.02462,17.17361), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((52.71027,17.08027,56.02462,17.33694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((52.71027,17.08027,56.02462,17.33694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((52.71027,17.08027,56.02462,17.33694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((52.71027,17.08027,56.02462,17.33694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.20415,17.17361,56.02462,17.47528), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.20415,17.17361,56.02462,17.47528), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.20415,17.17361,56.02462,17.47528), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.20415,17.17361,56.02462,17.47528), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((52.74638,17.33694,56.02462,17.08027), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((52.74638,17.33694,56.02462,17.08027), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((52.74638,17.33694,56.02462,17.08027), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((52.74638,17.33694,56.02462,17.08027), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.30305,17.37583,56.02462,17.44277), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.30305,17.37583,56.02462,17.44277), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.30305,17.37583,56.02462,17.44277), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.30305,17.37583,56.02462,17.44277), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.28526,17.44277,56.02462,17.37583), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.28526,17.44277,56.02462,17.37583), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.28526,17.44277,56.02462,17.37583), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.28526,17.44277,56.02462,17.37583), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.22887,17.47528,56.02462,17.17361), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.22887,17.47528,56.02462,17.17361), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.22887,17.47528,56.02462,17.17361), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.22887,17.47528,56.02462,17.17361), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((52.61166,17.53222,56.02462,17.08027), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((52.61166,17.53222,56.02462,17.08027), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((52.61166,17.53222,56.02462,17.08027), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((52.61166,17.53222,56.02462,17.08027), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.26276,17.60999,56.02462,17.44277), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.26276,17.60999,56.02462,17.44277), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.26276,17.60999,56.02462,17.44277), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.26276,17.60999,56.02462,17.44277), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.36526,17.68305,56.02462,17.37583), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.36526,17.68305,56.02462,17.37583), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.36526,17.68305,56.02462,17.37583), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.36526,17.68305,56.02462,17.37583), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.43027,17.82166,56.02462,17.68305), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.43027,17.82166,56.02462,17.68305), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.43027,17.82166,56.02462,17.68305), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.43027,17.82166,56.02462,17.68305), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.62777,17.88694,53.07825,22.28859), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.62777,17.88694,53.07825,22.28859), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.62777,17.88694,53.07825,22.28859), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.62777,17.88694,53.07825,22.28859), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.35332,17.93417,53.07825,24.96444), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.35332,17.93417,53.07825,24.96444), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.35332,17.93417,53.07825,24.96444), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.35332,17.93417,53.07825,24.96444), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.19832,17.95388,56.02462,17.93417), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.19832,17.95388,56.02462,17.93417), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.19832,17.95388,56.02462,17.93417), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.19832,17.95388,56.02462,17.93417), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.46138,18.08027,53.07825,24.96444), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.46138,18.08027,53.07825,24.96444), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.46138,18.08027,53.07825,24.96444), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.46138,18.08027,53.07825,24.96444), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.55499,18.12805,53.07825,24.51833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.55499,18.12805,53.07825,24.51833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.55499,18.12805,53.07825,24.51833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.55499,18.12805,53.07825,24.51833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((51.90415,18.54528,56.02462,18.99888), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((51.90415,18.54528,56.02462,18.99888), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((51.90415,18.54528,56.02462,18.99888), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((51.90415,18.54528,56.02462,18.99888), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.64193,18.57388,53.07825,24.51833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.64193,18.57388,53.07825,24.51833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.64193,18.57388,53.07825,24.51833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.64193,18.57388,53.07825,24.51833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.88443,18.78833,53.07825,24.14055), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.88443,18.78833,53.07825,24.14055), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.88443,18.78833,53.07825,24.14055), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.88443,18.78833,53.07825,24.14055), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.24721,18.91416,53.07825,23.88694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.24721,18.91416,53.07825,23.88694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.24721,18.91416,53.07825,23.88694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.24721,18.91416,53.07825,23.88694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.81332,18.98194,56.02462,20.00272), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.81332,18.98194,56.02462,20.00272), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.81332,18.98194,56.02462,20.00272), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.81332,18.98194,56.02462,20.00272), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((51.99915,18.99888,56.02462,18.54528), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((51.99915,18.99888,56.02462,18.54528), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((51.99915,18.99888,56.02462,18.54528), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((51.99915,18.99888,56.02462,18.54528), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.84165,19.01944,56.02462,20.13833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.84165,19.01944,56.02462,20.13833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.84165,19.01944,56.02462,20.13833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.84165,19.01944,56.02462,20.13833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.74443,19.27638,56.02462,19.39055), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.74443,19.27638,56.02462,19.39055), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.74443,19.27638,56.02462,19.39055), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.74443,19.27638,56.02462,19.39055), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.77388,19.39055,56.02462,19.27638), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.77388,19.39055,56.02462,19.27638), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.77388,19.39055,56.02462,19.27638), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.77388,19.39055,56.02462,19.27638), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.68777,19.70555,56.02462,19.27638), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.68777,19.70555,56.02462,19.27638), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.68777,19.70555,56.02462,19.27638), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.68777,19.70555,56.02462,19.27638), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.81476,20.00272,56.02462,18.98194), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.81476,20.00272,56.02462,18.98194), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.81476,20.00272,56.02462,18.98194), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.81476,20.00272,56.02462,18.98194), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.84499,20.13833,56.02462,19.01944), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.84499,20.13833,56.02462,19.01944), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.84499,20.13833,56.02462,19.01944), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.84499,20.13833,56.02462,19.01944), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.8211,20.18222,56.02462,20.00272), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.8211,20.18222,56.02462,20.00272), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.8211,20.18222,56.02462,20.00272), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.8211,20.18222,56.02462,20.00272), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.85555,20.25944,56.02462,20.13833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.85555,20.25944,56.02462,20.13833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.85555,20.25944,56.02462,20.13833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.85555,20.25944,56.02462,20.13833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.9636,20.32222,56.02462,20.40778), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.9636,20.32222,56.02462,20.40778), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.9636,20.32222,56.02462,20.40778), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.9636,20.32222,56.02462,20.40778), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.27443,20.36638,56.02462,20.60111), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.27443,20.36638,56.02462,20.60111), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.27443,20.36638,56.02462,20.60111), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.27443,20.36638,56.02462,20.60111), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.47638,20.39111,53.07825,23.6225), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.47638,20.39111,53.07825,23.6225), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.47638,20.39111,53.07825,23.6225), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.47638,20.39111,53.07825,23.6225), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.96082,20.40778,56.02462,20.32222), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.96082,20.40778,56.02462,20.32222), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.96082,20.40778,56.02462,20.32222), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.96082,20.40778,56.02462,20.32222), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.19777,20.41499,56.02462,20.61027), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.19777,20.41499,56.02462,20.61027), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.19777,20.41499,56.02462,20.61027), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.19777,20.41499,56.02462,20.61027), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.23832,20.42916,56.02462,20.60111), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.23832,20.42916,56.02462,20.60111), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.23832,20.42916,56.02462,20.60111), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.23832,20.42916,56.02462,20.60111), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.21277,20.45861,56.02462,20.61027), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.21277,20.45861,56.02462,20.61027), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.21277,20.45861,56.02462,20.61027), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.21277,20.45861,56.02462,20.61027), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.53082,20.46027,53.07825,23.65694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.53082,20.46027,53.07825,23.65694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.53082,20.46027,53.07825,23.65694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.53082,20.46027,53.07825,23.65694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.25804,20.60111,56.02462,20.36638), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.25804,20.60111,56.02462,20.36638), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.25804,20.60111,56.02462,20.36638), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.25804,20.60111,56.02462,20.36638), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.20582,20.61027,56.02462,20.45861), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.20582,20.61027,56.02462,20.45861), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.20582,20.61027,56.02462,20.45861), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.20582,20.61027,56.02462,20.45861), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.59444,20.64999,53.07825,23.63305), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.59444,20.64999,53.07825,23.63305), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.59444,20.64999,53.07825,23.63305), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.59444,20.64999,53.07825,23.63305), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.66999,20.75861,56.02462,20.82444), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.66999,20.75861,56.02462,20.82444), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.66999,20.75861,56.02462,20.82444), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.66999,20.75861,56.02462,20.82444), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.70832,20.76056,56.02462,20.82444), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.70832,20.76056,56.02462,20.82444), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.70832,20.76056,56.02462,20.82444), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.70832,20.76056,56.02462,20.82444), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.67693,20.82444,56.02462,20.75861), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.67693,20.82444,56.02462,20.75861), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.67693,20.82444,56.02462,20.75861), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.67693,20.82444,56.02462,20.75861), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.79276,20.92749,53.07825,23.42444), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.79276,20.92749,53.07825,23.42444), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.79276,20.92749,53.07825,23.42444), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.79276,20.92749,53.07825,23.42444), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.89832,21.11528,53.07825,23.37), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.89832,21.11528,53.07825,23.37), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.89832,21.11528,53.07825,23.37), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.89832,21.11528,53.07825,23.37), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.01139,21.23065,53.07825,24.2061), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.01139,21.23065,53.07825,24.2061), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.01139,21.23065,53.07825,24.2061), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.01139,21.23065,53.07825,24.2061), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.15999,21.35194,53.07825,22.92999), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.15999,21.35194,53.07825,22.92999), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.15999,21.35194,53.07825,22.92999), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.15999,21.35194,53.07825,22.92999), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.3461,21.44167,53.07825,22.79556), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.3461,21.44167,53.07825,22.79556), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.3461,21.44167,53.07825,22.79556), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.3461,21.44167,53.07825,22.79556), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.59554,21.88888,53.07825,22.55999), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.59554,21.88888,53.07825,22.55999), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.59554,21.88888,53.07825,22.55999), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.59554,21.88888,53.07825,22.55999), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.67554,22.01694,53.07825,22.55999), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.67554,22.01694,53.07825,22.55999), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.67554,22.01694,53.07825,22.55999), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.67554,22.01694,53.07825,22.55999), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.78387,22.18833,53.07825,22.49416), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.78387,22.18833,53.07825,22.49416), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.78387,22.18833,53.07825,22.49416), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.78387,22.18833,53.07825,22.49416), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.69401,22.28859,56.02462,17.88694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.01139,22.28859,53.07825,24.2061), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.01139,22.28859,53.07825,24.2061), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.01139,22.28859,53.07825,24.2061), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.01139,22.28859,53.07825,24.2061), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.83499,22.355,53.07825,22.47916), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.83499,22.355,53.07825,22.47916), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.83499,22.355,53.07825,22.47916), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.83499,22.355,53.07825,22.47916), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.8361,22.47916,53.07825,22.355), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.8361,22.47916,53.07825,22.355), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.8361,22.47916,53.07825,22.355), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.8361,22.47916,53.07825,22.355), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.76388,22.49416,53.07825,22.53527), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.76388,22.49416,53.07825,22.53527), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.76388,22.49416,53.07825,22.53527), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.76388,22.49416,53.07825,22.53527), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.72276,22.50055,53.07825,22.53527), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.72276,22.50055,53.07825,22.53527), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.72276,22.50055,53.07825,22.53527), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.72276,22.50055,53.07825,22.53527), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.74666,22.53527,53.07825,22.49416), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.74666,22.53527,53.07825,22.49416), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.74666,22.53527,53.07825,22.49416), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.74666,22.53527,53.07825,22.49416), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.80499,22.53583,53.07825,22.18833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.80499,22.53583,53.07825,22.18833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.80499,22.53583,53.07825,22.18833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.80499,22.53583,53.07825,22.18833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.66554,22.55999,53.07825,22.01694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.66554,22.55999,53.07825,22.01694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.66554,22.55999,53.07825,22.01694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.66554,22.55999,53.07825,22.01694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.52443,22.56666,53.07825,22.59416), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.52443,22.56666,53.07825,22.59416), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.52443,22.56666,53.07825,22.59416), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.52443,22.56666,53.07825,22.59416), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.50777,22.59416,53.07825,22.56666), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.50777,22.59416,53.07825,22.56666), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.50777,22.59416,53.07825,22.56666), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.50777,22.59416,53.07825,22.56666), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.29527,22.79556,53.07825,21.44167), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.29527,22.79556,53.07825,21.44167), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.29527,22.79556,53.07825,21.44167), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.29527,22.79556,53.07825,21.44167), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.24249,22.92999,53.07825,22.79556), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.24249,22.92999,53.07825,22.79556), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.24249,22.92999,53.07825,22.79556), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.24249,22.92999,53.07825,22.79556), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((59.03277,23.11861,53.07825,21.35194), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((59.03277,23.11861,53.07825,21.35194), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((59.03277,23.11861,53.07825,21.35194), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((59.03277,23.11861,53.07825,21.35194), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.86082,23.37,53.07825,21.11528), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.86082,23.37,53.07825,21.11528), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.86082,23.37,53.07825,21.11528), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.86082,23.37,53.07825,21.11528), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.79971,23.42444,53.07825,20.92749), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.79971,23.42444,53.07825,20.92749), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.79971,23.42444,53.07825,20.92749), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.79971,23.42444,53.07825,20.92749), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.77332,23.52777,53.07825,20.92749), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.77332,23.52777,53.07825,20.92749), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.77332,23.52777,53.07825,20.92749), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.77332,23.52777,53.07825,20.92749), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.63554,23.57833,53.07825,23.63305), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.63554,23.57833,53.07825,23.63305), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.63554,23.57833,53.07825,23.63305), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.63554,23.57833,53.07825,23.63305), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.33388,23.61666,56.02462,20.36638), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.33388,23.61666,56.02462,20.36638), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.33388,23.61666,56.02462,20.36638), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.33388,23.61666,56.02462,20.36638), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.45582,23.6225,56.02462,20.39111), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.45582,23.6225,56.02462,20.39111), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.45582,23.6225,56.02462,20.39111), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.45582,23.6225,56.02462,20.39111), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.60944,23.63305,56.02462,20.64999), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.60944,23.63305,56.02462,20.64999), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.60944,23.63305,56.02462,20.64999), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.60944,23.63305,56.02462,20.64999), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.50082,23.65694,56.02462,20.39111), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.50082,23.65694,56.02462,20.39111), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.50082,23.65694,56.02462,20.39111), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.50082,23.65694,56.02462,20.39111), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((58.10027,23.71722,56.02462,20.41499), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((58.10027,23.71722,56.02462,20.41499), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((58.10027,23.71722,56.02462,20.41499), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((58.10027,23.71722,56.02462,20.41499), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.88721,23.71805,56.02462,20.25944), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.88721,23.71805,56.02462,20.25944), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.88721,23.71805,56.02462,20.25944), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.88721,23.71805,56.02462,20.25944), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((55.69401,23.79615,56.02462,17.88694), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((55.69401,23.79615,56.02462,17.88694), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((55.69401,23.79615,56.02462,17.88694), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((55.69401,23.79615,56.02462,17.88694), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.32416,23.88694,56.02462,18.91416), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.32416,23.88694,56.02462,18.91416), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.32416,23.88694,56.02462,18.91416), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.32416,23.88694,56.02462,18.91416), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((57.11721,23.97027,56.02462,18.91416), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((57.11721,23.97027,56.02462,18.91416), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((57.11721,23.97027,56.02462,18.91416), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((57.11721,23.97027,56.02462,18.91416), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.90749,24.14055,56.02462,18.78833), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.90749,24.14055,56.02462,18.78833), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.90749,24.14055,56.02462,18.78833), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.90749,24.14055,56.02462,18.78833), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.02462,24.2061,53.07825,21.23065), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.02462,24.2061,53.07825,21.23065), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.02462,24.2061,53.07825,21.23065), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.02462,24.2061,53.07825,21.23065), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.59471,24.51833,56.02462,18.12805), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.59471,24.51833,56.02462,18.12805), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.59471,24.51833,56.02462,18.12805), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.59471,24.51833,56.02462,18.12805), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.37582,24.96444,56.02462,17.93417), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.37582,24.96444,56.02462,17.93417), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.37582,24.96444,56.02462,17.93417), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.37582,24.96444,56.02462,17.93417), mapfile, tile_dir, 17, 17, "om-oman")
	render_tiles((56.02462,25.03922,53.07825,21.23065), mapfile, tile_dir, 0, 11, "om-oman")
	render_tiles((56.02462,25.03922,53.07825,21.23065), mapfile, tile_dir, 13, 13, "om-oman")
	render_tiles((56.02462,25.03922,53.07825,21.23065), mapfile, tile_dir, 15, 15, "om-oman")
	render_tiles((56.02462,25.03922,53.07825,21.23065), mapfile, tile_dir, 17, 17, "om-oman")