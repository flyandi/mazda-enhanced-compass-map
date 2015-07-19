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
    # Region: SR
    # Region Name: Suriname

	render_tiles((-55.95667,1.84944,-56.96667,2.53194), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.95667,1.84944,-56.96667,2.53194), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.95667,1.84944,-56.96667,2.53194), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.95667,1.84944,-56.96667,2.53194), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.90166,1.89722,-55.95667,5.88722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.90166,1.89722,-55.95667,5.88722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.90166,1.89722,-55.95667,5.88722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.90166,1.89722,-55.95667,5.88722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.47091,1.94446,-55.95667,5.94778), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.47091,1.94446,-55.95667,5.94778), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.47091,1.94446,-55.95667,5.94778), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.47091,1.94446,-55.95667,5.94778), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.55778,2.02194,-55.95667,5.94778), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.55778,2.02194,-55.95667,5.94778), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.55778,2.02194,-55.95667,5.94778), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.55778,2.02194,-55.95667,5.94778), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.69189,2.02697,-56.96667,2.02194), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.69189,2.02697,-56.96667,2.02194), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.69189,2.02697,-56.96667,2.02194), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.69189,2.02697,-56.96667,2.02194), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.90166,2.04528,-55.95667,5.88722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.90166,2.04528,-55.95667,5.88722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.90166,2.04528,-55.95667,5.88722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.90166,2.04528,-55.95667,5.88722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.13194,2.25278,-56.96667,2.34389), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.13194,2.25278,-56.96667,2.34389), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.13194,2.25278,-56.96667,2.34389), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.13194,2.25278,-56.96667,2.34389), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.61536,2.31978,-56.96667,2.35472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.61536,2.31978,-56.96667,2.35472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.61536,2.31978,-56.96667,2.35472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.61536,2.31978,-56.96667,2.35472), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.67834,2.32111,-56.96667,2.45389), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.67834,2.32111,-56.96667,2.45389), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.67834,2.32111,-56.96667,2.45389), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.67834,2.32111,-56.96667,2.45389), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.52417,2.33611,-56.96667,2.42855), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.52417,2.33611,-56.96667,2.42855), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.52417,2.33611,-56.96667,2.42855), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.52417,2.33611,-56.96667,2.42855), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.69666,2.33694,-56.96667,2.45389), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.69666,2.33694,-56.96667,2.45389), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.69666,2.33694,-56.96667,2.45389), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.69666,2.33694,-56.96667,2.45389), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.09805,2.34389,-56.96667,2.25278), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.09805,2.34389,-56.96667,2.25278), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.09805,2.34389,-56.96667,2.25278), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.09805,2.34389,-56.96667,2.25278), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.03722,2.35,-55.95667,5.81611), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.03722,2.35,-55.95667,5.81611), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.03722,2.35,-55.95667,5.81611), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.03722,2.35,-55.95667,5.81611), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.57389,2.35472,-56.96667,2.31978), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.57389,2.35472,-56.96667,2.31978), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.57389,2.35472,-56.96667,2.31978), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.57389,2.35472,-56.96667,2.31978), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.72888,2.40111,-55.95667,5.9825), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.72888,2.40111,-55.95667,5.9825), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.72888,2.40111,-55.95667,5.9825), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.72888,2.40111,-55.95667,5.9825), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.42667,2.42722,-55.95667,4.70861), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.42667,2.42722,-55.95667,4.70861), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.42667,2.42722,-55.95667,4.70861), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.42667,2.42722,-55.95667,4.70861), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.38,2.42806,-56.96667,2.51139), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.38,2.42806,-56.96667,2.51139), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.38,2.42806,-56.96667,2.51139), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.38,2.42806,-56.96667,2.51139), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.48324,2.42855,-55.95667,4.73666), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.48324,2.42855,-55.95667,4.73666), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.48324,2.42855,-55.95667,4.73666), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.48324,2.42855,-55.95667,4.73666), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.80666,2.43555,-56.96667,2.47111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.80666,2.43555,-56.96667,2.47111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.80666,2.43555,-56.96667,2.47111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.80666,2.43555,-56.96667,2.47111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.86777,2.44028,-56.96667,2.43555), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.86777,2.44028,-56.96667,2.43555), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.86777,2.44028,-56.96667,2.43555), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.86777,2.44028,-56.96667,2.43555), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.68916,2.45389,-56.96667,2.33694), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.68916,2.45389,-56.96667,2.33694), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.68916,2.45389,-56.96667,2.33694), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.68916,2.45389,-56.96667,2.33694), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.76,2.47111,-56.96667,2.43555), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.76,2.47111,-56.96667,2.43555), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.76,2.47111,-56.96667,2.43555), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.76,2.47111,-56.96667,2.43555), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.98473,2.48,-56.96667,2.5225), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.98473,2.48,-56.96667,2.5225), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.98473,2.48,-56.96667,2.5225), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.98473,2.48,-56.96667,2.5225), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.2589,2.49722,-56.96667,2.52167), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.2589,2.49722,-56.96667,2.52167), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.2589,2.49722,-56.96667,2.52167), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.2589,2.49722,-56.96667,2.52167), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.33083,2.51139,-55.95667,5.94028), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.33083,2.51139,-55.95667,5.94028), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.33083,2.51139,-55.95667,5.94028), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.33083,2.51139,-55.95667,5.94028), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.28222,2.52167,-56.96667,2.49722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.28222,2.52167,-56.96667,2.49722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.28222,2.52167,-56.96667,2.49722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.28222,2.52167,-56.96667,2.49722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.98277,2.5225,-56.96667,2.48), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.98277,2.5225,-56.96667,2.48), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.98277,2.5225,-56.96667,2.48), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.98277,2.5225,-56.96667,2.48), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.11195,2.52722,-55.95667,5.90416), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.11195,2.52722,-55.95667,5.90416), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.11195,2.52722,-55.95667,5.90416), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.11195,2.52722,-55.95667,5.90416), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.94722,2.53194,-56.96667,1.84944), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.94722,2.53194,-56.96667,1.84944), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.94722,2.53194,-56.96667,1.84944), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.94722,2.53194,-56.96667,1.84944), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.9725,2.55472,-55.95667,5.86111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.9725,2.55472,-55.95667,5.86111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.9725,2.55472,-55.95667,5.86111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.9725,2.55472,-55.95667,5.86111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.15028,2.5725,-55.95667,5.97917), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.15028,2.5725,-55.95667,5.97917), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.15028,2.5725,-55.95667,5.97917), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.15028,2.5725,-55.95667,5.97917), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.95611,2.58139,-56.96667,2.61611), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.95611,2.58139,-56.96667,2.61611), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.95611,2.58139,-56.96667,2.61611), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.95611,2.58139,-56.96667,2.61611), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.95389,2.61611,-56.96667,2.58139), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.95389,2.61611,-56.96667,2.58139), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.95389,2.61611,-56.96667,2.58139), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.95389,2.61611,-56.96667,2.58139), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.02306,2.62472,-55.95667,5.94972), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.02306,2.62472,-55.95667,5.94972), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.02306,2.62472,-55.95667,5.94972), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.02306,2.62472,-55.95667,5.94972), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.09361,2.71583,-55.95667,5.94972), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.09361,2.71583,-55.95667,5.94972), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.09361,2.71583,-55.95667,5.94972), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.09361,2.71583,-55.95667,5.94972), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.12778,2.79167,-56.96667,2.71583), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.12778,2.79167,-56.96667,2.71583), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.12778,2.79167,-56.96667,2.71583), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.12778,2.79167,-56.96667,2.71583), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.20222,2.82083,-56.96667,3.03528), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.20222,2.82083,-56.96667,3.03528), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.20222,2.82083,-56.96667,3.03528), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.20222,2.82083,-56.96667,3.03528), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.18056,2.84083,-56.96667,2.90361), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.18056,2.84083,-56.96667,2.90361), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.18056,2.84083,-56.96667,2.90361), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.18056,2.84083,-56.96667,2.90361), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.18639,2.90361,-56.96667,3.17889), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.18639,2.90361,-56.96667,3.17889), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.18639,2.90361,-56.96667,3.17889), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.18639,2.90361,-56.96667,3.17889), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.17028,3.00833,-55.95667,5.34632), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.17028,3.00833,-55.95667,5.34632), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.17028,3.00833,-55.95667,5.34632), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.17028,3.00833,-55.95667,5.34632), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.21056,3.03528,-55.95667,5.23222), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.21056,3.03528,-55.95667,5.23222), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.21056,3.03528,-55.95667,5.23222), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.21056,3.03528,-55.95667,5.23222), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.19194,3.17889,-56.96667,2.90361), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.19194,3.17889,-56.96667,2.90361), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.19194,3.17889,-56.96667,2.90361), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.19194,3.17889,-56.96667,2.90361), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.29333,3.26389,-55.95667,5.16028), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.29333,3.26389,-55.95667,5.16028), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.29333,3.26389,-55.95667,5.16028), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.29333,3.26389,-55.95667,5.16028), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.09444,3.295,-56.96667,3.66444), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.09444,3.295,-56.96667,3.66444), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.09444,3.295,-56.96667,3.66444), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.09444,3.295,-56.96667,3.66444), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.64223,3.35583,-56.96667,3.46111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.29945,3.37083,-55.95667,5.16028), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.29945,3.37083,-55.95667,5.16028), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.29945,3.37083,-55.95667,5.16028), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.29945,3.37083,-55.95667,5.16028), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.66695,3.39556,-55.95667,5.01028), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.66695,3.39556,-55.95667,5.01028), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.66695,3.39556,-55.95667,5.01028), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.66695,3.39556,-55.95667,5.01028), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.00306,3.44278,-56.96667,3.63639), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.00306,3.44278,-56.96667,3.63639), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.00306,3.44278,-56.96667,3.63639), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.00306,3.44278,-56.96667,3.63639), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.63695,3.46111,-56.96667,3.35583), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.63695,3.46111,-56.96667,3.35583), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.63695,3.46111,-56.96667,3.35583), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.63695,3.46111,-56.96667,3.35583), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-53.98444,3.59944,-55.95667,5.74528), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-53.98444,3.59944,-55.95667,5.74528), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-53.98444,3.59944,-55.95667,5.74528), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-53.98444,3.59944,-55.95667,5.74528), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.735,3.61028,-55.95667,5.01028), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.735,3.61028,-55.95667,5.01028), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.735,3.61028,-55.95667,5.01028), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.735,3.61028,-55.95667,5.01028), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.00917,3.63639,-56.96667,3.44278), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.00917,3.63639,-56.96667,3.44278), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.00917,3.63639,-56.96667,3.44278), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.00917,3.63639,-56.96667,3.44278), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.06722,3.66444,-55.95667,5.61583), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.06722,3.66444,-55.95667,5.61583), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.06722,3.66444,-55.95667,5.61583), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.06722,3.66444,-55.95667,5.61583), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.83583,3.665,-55.95667,4.65472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.83583,3.665,-55.95667,4.65472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.83583,3.665,-55.95667,4.65472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.83583,3.665,-55.95667,4.65472), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.12389,3.78555,-56.96667,3.295), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.12389,3.78555,-56.96667,3.295), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.12389,3.78555,-56.96667,3.295), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.12389,3.78555,-56.96667,3.295), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.92917,3.88667,-55.95667,4.79861), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.92917,3.88667,-55.95667,4.79861), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.92917,3.88667,-55.95667,4.79861), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.92917,3.88667,-55.95667,4.79861), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.28445,3.92639,-55.95667,5.24805), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.28445,3.92639,-55.95667,5.24805), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.28445,3.92639,-55.95667,5.24805), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.28445,3.92639,-55.95667,5.24805), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-58.04056,3.99472,-55.95667,4.15222), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-58.04056,3.99472,-55.95667,4.15222), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-58.04056,3.99472,-55.95667,4.15222), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-58.04056,3.99472,-55.95667,4.15222), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.36028,4.035,-55.95667,4.14222), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.36028,4.035,-55.95667,4.14222), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.36028,4.035,-55.95667,4.14222), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.36028,4.035,-55.95667,4.14222), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.33167,4.14222,-55.95667,4.035), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.33167,4.14222,-55.95667,4.035), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.33167,4.14222,-55.95667,4.035), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.33167,4.14222,-55.95667,4.035), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-58.07167,4.15222,-55.95667,3.99472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-58.07167,4.15222,-55.95667,3.99472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-58.07167,4.15222,-55.95667,3.99472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-58.07167,4.15222,-55.95667,3.99472), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.40222,4.18139,-55.95667,5.91639), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.40222,4.18139,-55.95667,5.91639), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.40222,4.18139,-55.95667,5.91639), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.40222,4.18139,-55.95667,5.91639), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.95417,4.28778,-56.96667,3.88667), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.95417,4.28778,-56.96667,3.88667), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.95417,4.28778,-56.96667,3.88667), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.95417,4.28778,-56.96667,3.88667), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.8825,4.55916,-55.95667,4.91278), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.8825,4.55916,-55.95667,4.91278), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.8825,4.55916,-55.95667,4.91278), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.8825,4.55916,-55.95667,4.91278), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.83861,4.65472,-56.96667,3.665), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.83861,4.65472,-56.96667,3.665), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.83861,4.65472,-56.96667,3.665), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.83861,4.65472,-56.96667,3.665), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.43444,4.70861,-56.96667,2.42722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.43444,4.70861,-56.96667,2.42722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.43444,4.70861,-56.96667,2.42722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.43444,4.70861,-56.96667,2.42722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.47472,4.73666,-56.96667,2.42855), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.47472,4.73666,-56.96667,2.42855), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.47472,4.73666,-56.96667,2.42855), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.47472,4.73666,-56.96667,2.42855), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.92639,4.79861,-56.96667,3.88667), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.92639,4.79861,-56.96667,3.88667), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.92639,4.79861,-56.96667,3.88667), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.92639,4.79861,-56.96667,3.88667), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.85362,4.91278,-55.95667,4.65472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.85362,4.91278,-55.95667,4.65472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.85362,4.91278,-55.95667,4.65472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.85362,4.91278,-55.95667,4.65472), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.45583,5.00444,-55.95667,4.73666), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.45583,5.00444,-55.95667,4.73666), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.45583,5.00444,-55.95667,4.73666), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.45583,5.00444,-55.95667,4.73666), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.37583,5.00611,-55.95667,5.31333), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.37583,5.00611,-55.95667,5.31333), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.37583,5.00611,-55.95667,5.31333), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.37583,5.00611,-55.95667,5.31333), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.68833,5.01028,-56.96667,3.39556), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.68833,5.01028,-56.96667,3.39556), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.68833,5.01028,-56.96667,3.39556), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.68833,5.01028,-56.96667,3.39556), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.28917,5.01666,-55.95667,5.22944), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.28917,5.01666,-55.95667,5.22944), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.28917,5.01666,-55.95667,5.22944), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.28917,5.01666,-55.95667,5.22944), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.22888,5.14083,-55.95667,5.23222), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.22888,5.14083,-55.95667,5.23222), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.22888,5.14083,-55.95667,5.23222), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.22888,5.14083,-55.95667,5.23222), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.29723,5.16028,-56.96667,3.37083), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.29723,5.16028,-56.96667,3.37083), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.29723,5.16028,-56.96667,3.37083), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.29723,5.16028,-56.96667,3.37083), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.19055,5.16417,-56.96667,2.82083), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.19055,5.16417,-56.96667,2.82083), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.19055,5.16417,-56.96667,2.82083), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.19055,5.16417,-56.96667,2.82083), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.26611,5.17777,-55.95667,5.22055), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.26611,5.17777,-55.95667,5.22055), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.26611,5.17777,-55.95667,5.22055), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.26611,5.17777,-55.95667,5.22055), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.26112,5.22055,-55.95667,5.17777), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.26112,5.22055,-55.95667,5.17777), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.26112,5.22055,-55.95667,5.17777), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.26112,5.22055,-55.95667,5.17777), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.28667,5.22944,-55.95667,5.01666), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.28667,5.22944,-55.95667,5.01666), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.28667,5.22944,-55.95667,5.01666), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.28667,5.22944,-55.95667,5.01666), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.21278,5.23222,-56.96667,3.03528), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.21278,5.23222,-56.96667,3.03528), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.21278,5.23222,-56.96667,3.03528), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.21278,5.23222,-56.96667,3.03528), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.29056,5.24805,-55.95667,3.92639), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.29056,5.24805,-55.95667,3.92639), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.29056,5.24805,-55.95667,3.92639), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.29056,5.24805,-55.95667,3.92639), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.2525,5.26917,-55.95667,5.48581), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.2525,5.26917,-55.95667,5.48581), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.2525,5.26917,-55.95667,5.48581), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.2525,5.26917,-55.95667,5.48581), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.33556,5.31333,-56.96667,3.37083), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.33556,5.31333,-56.96667,3.37083), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.33556,5.31333,-56.96667,3.37083), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.33556,5.31333,-56.96667,3.37083), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.16897,5.34632,-56.96667,3.00833), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.16897,5.34632,-56.96667,3.00833), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.16897,5.34632,-56.96667,3.00833), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.16897,5.34632,-56.96667,3.00833), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.24814,5.48581,-55.95667,5.26917), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.24814,5.48581,-55.95667,5.26917), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.24814,5.48581,-55.95667,5.26917), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.24814,5.48581,-55.95667,5.26917), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.16639,5.54222,-55.95667,5.16417), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.16639,5.54222,-55.95667,5.16417), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.16639,5.54222,-55.95667,5.16417), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.16639,5.54222,-55.95667,5.16417), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.04889,5.61583,-56.96667,3.66444), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.04889,5.61583,-56.96667,3.66444), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.04889,5.61583,-56.96667,3.66444), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.04889,5.61583,-56.96667,3.66444), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.87778,5.72416,-56.96667,1.89722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.87778,5.72416,-56.96667,1.89722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.87778,5.72416,-56.96667,1.89722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.87778,5.72416,-56.96667,1.89722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-53.98972,5.74528,-56.96667,3.59944), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-53.98972,5.74528,-56.96667,3.59944), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-53.98972,5.74528,-56.96667,3.59944), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-53.98972,5.74528,-56.96667,3.59944), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.01306,5.81611,-56.96667,2.35), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.01306,5.81611,-56.96667,2.35), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.01306,5.81611,-56.96667,2.35), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.01306,5.81611,-56.96667,2.35), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.02112,5.81611,-56.96667,3.63639), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.02112,5.81611,-56.96667,3.63639), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.02112,5.81611,-56.96667,3.63639), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.02112,5.81611,-56.96667,3.63639), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.97278,5.86111,-56.96667,2.55472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.97278,5.86111,-56.96667,2.55472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.97278,5.86111,-56.96667,2.55472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.97278,5.86111,-56.96667,2.55472), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.09112,5.88028,-55.95667,5.90416), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.09112,5.88028,-55.95667,5.90416), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.09112,5.88028,-55.95667,5.90416), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.09112,5.88028,-55.95667,5.90416), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.92473,5.88722,-56.96667,2.53194), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.92473,5.88722,-56.96667,2.53194), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.92473,5.88722,-56.96667,2.53194), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.92473,5.88722,-56.96667,2.53194), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.10639,5.90416,-56.96667,2.52722), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.10639,5.90416,-56.96667,2.52722), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.10639,5.90416,-56.96667,2.52722), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.10639,5.90416,-56.96667,2.52722), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.41334,5.91639,-55.95667,4.18139), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.41334,5.91639,-55.95667,4.18139), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.41334,5.91639,-55.95667,4.18139), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.41334,5.91639,-55.95667,4.18139), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.31528,5.94028,-56.96667,2.51139), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.31528,5.94028,-56.96667,2.51139), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.31528,5.94028,-56.96667,2.51139), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.31528,5.94028,-56.96667,2.51139), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.54139,5.94778,-56.96667,2.02194), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.54139,5.94778,-56.96667,2.02194), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.54139,5.94778,-56.96667,2.02194), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.54139,5.94778,-56.96667,2.02194), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-57.0625,5.94972,-56.96667,2.71583), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-57.0625,5.94972,-56.96667,2.71583), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-57.0625,5.94972,-56.96667,2.71583), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-57.0625,5.94972,-56.96667,2.71583), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.8475,5.95389,-55.95667,5.72416), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.8475,5.95389,-55.95667,5.72416), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.8475,5.95389,-55.95667,5.72416), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.8475,5.95389,-55.95667,5.72416), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.16222,5.95806,-56.96667,2.5725), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.16222,5.95806,-56.96667,2.5725), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.16222,5.95806,-56.96667,2.5725), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.16222,5.95806,-56.96667,2.5725), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.14389,5.97917,-56.96667,2.5725), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.14389,5.97917,-56.96667,2.5725), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.14389,5.97917,-56.96667,2.5725), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.14389,5.97917,-56.96667,2.5725), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-55.66723,5.9825,-56.96667,2.40111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-55.66723,5.9825,-56.96667,2.40111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-55.66723,5.9825,-56.96667,2.40111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-55.66723,5.9825,-56.96667,2.40111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-54.98306,5.98861,-55.95667,5.86111), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-54.98306,5.98861,-55.95667,5.86111), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-54.98306,5.98861,-55.95667,5.86111), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-54.98306,5.98861,-55.95667,5.86111), mapfile, tile_dir, 17, 17, "sr-suriname")
	render_tiles((-56.96667,5.9975,-56.96667,2.62472), mapfile, tile_dir, 0, 11, "sr-suriname")
	render_tiles((-56.96667,5.9975,-56.96667,2.62472), mapfile, tile_dir, 13, 13, "sr-suriname")
	render_tiles((-56.96667,5.9975,-56.96667,2.62472), mapfile, tile_dir, 15, 15, "sr-suriname")
	render_tiles((-56.96667,5.9975,-56.96667,2.62472), mapfile, tile_dir, 17, 17, "sr-suriname")