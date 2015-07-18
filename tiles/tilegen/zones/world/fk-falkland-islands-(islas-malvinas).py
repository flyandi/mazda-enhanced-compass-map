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
    # Region: FK
    # Region Name: Falkland Islands (Islas Malvinas)

	render_tiles((-59.36389,-52.33945,-59.32973,-52.33334), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.36389,-52.33945,-59.32973,-52.33334), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.32973,-52.33334,-59.36389,-52.33945), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.34334,-52.25001,-59.06722,-52.23139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.06722,-52.23139,-59.43028,-52.21501), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.43028,-52.21501,-59.04835,-52.20945), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.04835,-52.20945,-59.43028,-52.21501), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.09584,-52.17667,-59.2925,-52.1575), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.2925,-52.1575,-59.1375,-52.15417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.1375,-52.15417,-59.2925,-52.1575), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.45278,-52.14528,-59.15417,-52.13863), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.15417,-52.13863,-59.03722,-52.13778), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.03722,-52.13778,-59.15417,-52.13863), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.38473,-52.11639,-58.67889,-52.11611), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.67889,-52.11611,-59.38473,-52.11639), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.63473,-52.11139,-58.67889,-52.11611), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.07861,-52.10056,-59.71416,-52.10052), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.71416,-52.10052,-59.07861,-52.10056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.93056,-52.10029,-59.71416,-52.10052), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.89973,-52.09306,-58.93056,-52.10029), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.58612,-52.07751,-58.89973,-52.09306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.73251,-52.04862,-58.84778,-52.04139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.84778,-52.04139,-58.73251,-52.04862), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.99695,-52.03223,-58.66862,-52.03196), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.66862,-52.03196,-58.99695,-52.03223), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.19501,-52.02806,-59.29333,-52.02723), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.29333,-52.02723,-59.19501,-52.02806), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.55917,-52.01611,-59.63668,-52.0089), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.63668,-52.0089,-59.29584,-52.00751), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.29584,-52.00751,-59.63668,-52.0089), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.60389,-52.0014,-59.29584,-52.00751), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.24361,-51.99112,-59.58806,-51.98556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.58806,-51.98556,-59.24361,-51.99112), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.52112,-51.96973,-58.63889,-51.9625), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.63889,-51.9625,-58.78722,-51.96056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.78722,-51.96056,-58.63889,-51.9625), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.60389,-51.94167,-59.48917,-51.93584), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.48917,-51.93584,-59.60389,-51.94167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.59612,-51.92167,-59.48889,-51.90862), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.48889,-51.90862,-58.59556,-51.89944), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.59556,-51.89944,-59.48889,-51.90862), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.52168,-51.87972,-58.30306,-51.86195), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.30306,-51.86195,-58.97528,-51.85307), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.97528,-51.85307,-58.86806,-51.85139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.86806,-51.85139,-58.97528,-51.85307), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.96639,-51.82056,-59.04306,-51.81779), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.04306,-51.81779,-58.96639,-51.82056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.18917,-51.8125,-59.04306,-51.81779), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.93056,-51.80057,-58.985,-51.79889), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.985,-51.79889,-58.93056,-51.80057), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.14278,-51.78806,-58.985,-51.79889), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.10139,-51.76222,-58.14278,-51.78806), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.05222,-51.69306,-57.73139,-51.69222), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.73139,-51.69222,-59.05222,-51.69306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.08112,-51.69028,-57.73139,-51.69222), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.89973,-51.67778,-59.08112,-51.69028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.22695,-51.65445,-58.985,-51.65278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.985,-51.65278,-58.22695,-51.65445), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.76417,-51.635,-58.25945,-51.63279), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.25945,-51.63279,-57.76417,-51.635), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.79362,-51.61251,-58.16862,-51.60722), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.16862,-51.60722,-57.79362,-51.61251), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.24722,-51.60056,-59.16028,-51.59418), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.16028,-51.59418,-58.0475,-51.58834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.0475,-51.58834,-59.04389,-51.58417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.04389,-51.58417,-58.0475,-51.58834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.17278,-51.57557,-59.00917,-51.56917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.00917,-51.56917,-58.28722,-51.56417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.28722,-51.56417,-58.375,-51.56139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.375,-51.56139,-58.28722,-51.56417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.79111,-51.54917,-58.14334,-51.5475), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.14334,-51.5475,-57.79111,-51.54917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.76417,-51.53223,-58.07944,-51.52306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.07944,-51.52306,-59.0475,-51.52056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.0475,-51.52056,-58.07944,-51.52306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.34167,-51.51362,-58.98584,-51.50834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.98584,-51.50834,-58.34167,-51.51362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.10889,-51.50168,-58.51779,-51.50111), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.51779,-51.50111,-59.10889,-51.50168), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.35667,-51.49751,-58.51779,-51.50111), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.29195,-51.48195,-58.43111,-51.47778), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.43111,-51.47778,-57.82584,-51.47472), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.82584,-51.47472,-58.43111,-51.47778), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.12083,-51.46695,-57.82584,-51.47472), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.42084,-51.45195,-58.50223,-51.44723), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.50223,-51.44723,-58.42084,-51.45195), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.55029,-51.43362,-58.32028,-51.42028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.32028,-51.42028,-59.09972,-51.41417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.09972,-51.41417,-58.32028,-51.42028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.26528,-51.40611,-57.87389,-51.40167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.87389,-51.40167,-58.26528,-51.40611), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.485,-51.395,-57.87389,-51.40167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.87528,-51.37945,-57.94278,-51.37139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-57.94278,-51.37139,-58.34778,-51.36417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.34778,-51.36417,-58.86584,-51.35917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.86584,-51.35917,-58.34778,-51.36417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.62222,-51.34695,-58.86584,-51.35917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.57973,-51.30945,-58.47139,-51.30668), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.47139,-51.30668,-58.57973,-51.30945), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-58.94862,-51.25861,-58.47139,-51.30668), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.08751,-54.88972,-35.95612,-54.86056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.08751,-54.88972,-35.95612,-54.86056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.95612,-54.86056,-36.08751,-54.88972), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.99001,-54.83056,-35.90974,-54.81334), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.90974,-54.81334,-35.99001,-54.83056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.78279,-54.76556,-36.09973,-54.76362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.09973,-54.76362,-35.78279,-54.76556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.28751,-54.75389,-36.09973,-54.76362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.93779,-54.69584,-36.34279,-54.65362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.34279,-54.65362,-35.93779,-54.69584), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.48251,-54.5714,-36.0689,-54.57084), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.0689,-54.57084,-36.48251,-54.5714), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-35.89334,-54.54945,-36.09557,-54.54224), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.09557,-54.54224,-35.89334,-54.54945), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.46501,-54.53362,-36.09557,-54.54224), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.61612,-54.5164,-36.46501,-54.53362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.00778,-54.49834,-36.52724,-54.49557), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.52724,-54.49557,-36.00778,-54.49834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.80918,-54.44972,-36.16029,-54.44473), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.16029,-54.44473,-36.80918,-54.44972), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.83612,-54.39306,-36.25529,-54.37695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.25529,-54.37695,-36.1664,-54.37222), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.1664,-54.37222,-36.25529,-54.37695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.90362,-54.36501,-36.34612,-54.36167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.34612,-54.36167,-36.90362,-54.36501), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.49696,-54.35028,-36.8639,-54.34584), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.8639,-54.34584,-36.49696,-54.35028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.03529,-54.3364,-36.8639,-54.34584), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.43056,-54.30806,-36.25723,-54.28667), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.25723,-54.28667,-36.6764,-54.27695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.6764,-54.27695,-36.49807,-54.275), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.49807,-54.275,-36.6764,-54.27695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.40417,-54.26973,-36.49807,-54.275), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.19001,-54.2525,-36.39779,-54.24695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.39779,-54.24695,-37.39223,-54.2439), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.39223,-54.2439,-36.39779,-54.24695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.32112,-54.23361,-37.39223,-54.2439), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.40723,-54.19167,-36.56196,-54.19028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.56196,-54.19028,-37.40723,-54.19167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.70863,-54.18306,-37.65529,-54.18167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.65529,-54.18167,-36.70863,-54.18306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.70863,-54.16945,-37.52946,-54.16251), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.52946,-54.16251,-36.70863,-54.16945), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.24445,-54.15167,-36.63612,-54.14861), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.63612,-54.14861,-37.24445,-54.15167), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.81612,-54.14445,-37.71779,-54.14306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.71779,-54.14306,-37.56779,-54.1425), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.56779,-54.1425,-37.71779,-54.14306), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.48501,-54.12917,-37.01945,-54.12251), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.01945,-54.12251,-36.62612,-54.11861), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.62612,-54.11861,-37.01945,-54.12251), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.14723,-54.11362,-37.03279,-54.11084), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.03279,-54.11084,-37.14723,-54.11362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.65835,-54.10417,-36.73501,-54.0989), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.73501,-54.0989,-37.65835,-54.10417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.71806,-54.09222,-36.81667,-54.08556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.81667,-54.08556,-36.98029,-54.08028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.98029,-54.08028,-36.81667,-54.08556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.42307,-54.06028,-38.01834,-54.05917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-38.01834,-54.05917,-37.42307,-54.06028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.17473,-54.05334,-36.98918,-54.05139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-36.98918,-54.05139,-37.28112,-54.04973), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.28112,-54.04973,-37.1164,-54.04917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.1164,-54.04917,-37.28112,-54.04973), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.61696,-54.04834,-37.1164,-54.04917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.83418,-54.03139,-37.44057,-54.03084), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.44057,-54.03084,-37.83418,-54.03139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-38.0289,-54.00973,-37.58363,-54.00333), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-37.58363,-54.00333,-38.0289,-54.00973), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.58973,-52.24029,-60.65,-52.23361), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.58973,-52.24029,-60.65,-52.23361), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.65,-52.23361,-60.58973,-52.24029), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.49695,-52.18639,-60.77584,-52.17751), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.77584,-52.17751,-60.49695,-52.18639), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.72112,-52.16723,-60.36806,-52.15918), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.36806,-52.15918,-60.72112,-52.16723), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.69083,-52.11556,-60.55056,-52.11279), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.55056,-52.11279,-60.69083,-52.11556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.81834,-52.10806,-60.55056,-52.11279), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.26389,-52.07445,-60.81834,-52.10806), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.81945,-52.03473,-60.89001,-52.03056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.89001,-52.03056,-60.81945,-52.03473), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-61.03081,-52.02385,-60.89001,-52.03056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.74722,-52.01472,-61.03081,-52.02385), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.28695,-52.00528,-60.07861,-51.9989), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.07861,-51.9989,-59.98278,-51.9939), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.98278,-51.9939,-60.07861,-51.9989), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.56195,-51.97861,-60.20084,-51.97278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.20084,-51.97278,-60.46001,-51.97084), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.46001,-51.97084,-60.20084,-51.97278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.84668,-51.96028,-59.86806,-51.95028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.86806,-51.95028,-60.52,-51.94556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.52,-51.94556,-59.86806,-51.95028), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.06028,-51.94029,-60.52,-51.94556), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.40639,-51.92889,-60.6039,-51.92722), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.6039,-51.92722,-60.40639,-51.92889), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.49556,-51.9225,-60.6039,-51.92722), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.40834,-51.9075,-60.49556,-51.9225), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.34556,-51.86085,-60.44417,-51.84807), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.44417,-51.84807,-60.34556,-51.86085), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.54223,-51.80029,-60.45222,-51.7975), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.45222,-51.7975,-60.54223,-51.80029), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.22278,-51.78779,-60.45222,-51.7975), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.38807,-51.7664,-60.30139,-51.75472), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.30139,-51.75472,-60.41584,-51.75223), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.41584,-51.75223,-60.30139,-51.75472), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.6425,-51.71779,-60.17834,-51.715), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.17834,-51.715,-60.6425,-51.71779), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.34584,-51.70695,-60.46861,-51.70278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.46861,-51.70278,-60.34584,-51.70695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.07806,-51.6975,-60.46861,-51.70278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.63528,-51.67362,-60.1375,-51.66473), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.1375,-51.66473,-60.63528,-51.67362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.22695,-51.65167,-60.1375,-51.66473), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.43224,-51.62334,-59.5239,-51.62139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.5239,-51.62139,-59.43224,-51.62334), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.38945,-51.59473,-59.5239,-51.62139), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.49056,-51.53195,-60.51306,-51.50278), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.51306,-51.50278,-59.31639,-51.50001), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.31639,-51.50001,-59.37222,-51.49834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.37222,-51.49834,-59.31639,-51.50001), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.39194,-51.49529,-59.37222,-51.49834), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.14834,-51.49168,-60.39194,-51.49529), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.50112,-51.48334,-60.14834,-51.49168), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.98501,-51.46779,-59.52778,-51.46417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.52778,-51.46417,-59.98501,-51.46779), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.41778,-51.45806,-59.52778,-51.46417), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.75111,-51.44917,-59.2564,-51.4464), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.2564,-51.4464,-59.75111,-51.44917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.09361,-51.44307,-59.2564,-51.4464), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.56278,-51.43835,-59.40028,-51.43723), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.40028,-51.43723,-59.56278,-51.43835), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.39778,-51.4225,-59.2814,-51.41917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.2814,-51.41917,-60.61389,-51.41695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.61389,-51.41695,-59.2814,-51.41917), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.21278,-51.40585,-60.61389,-51.41695), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.495,-51.39195,-60.49167,-51.3839), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.49167,-51.3839,-60.02723,-51.38251), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.02723,-51.38251,-60.49167,-51.3839), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.44833,-51.36362,-60.645,-51.36056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-60.645,-51.36056,-59.44833,-51.36362), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")
	render_tiles((-59.40306,-51.3364,-60.645,-51.36056), mapfile, tile_dir, 0, 11, "fk-falkland-islands-(islas-malvinas)")