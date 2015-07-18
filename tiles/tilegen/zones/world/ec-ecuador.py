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
    # Region: EC
    # Region Name: Ecuador

	render_tiles((-91.17307,-1.03333,-91.42834,-1.01139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.17307,-1.03333,-91.42834,-1.01139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.42834,-1.01139,-91.17307,-1.03333), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.86168,-0.91611,-91.5014,-0.8875), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.5014,-0.8875,-90.86168,-0.91611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.47639,-0.82639,-90.83057,-0.77861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.83057,-0.77861,-90.79001,-0.75722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.79001,-0.75722,-90.83057,-0.77861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.30612,-0.68389,-91.15834,-0.6825), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.15834,-0.6825,-91.30612,-0.68389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.91278,-0.61139,-90.96028,-0.60917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.96028,-0.60917,-90.91278,-0.61139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.08307,-0.5825,-90.96028,-0.60917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.15779,-0.54611,-91.08307,-0.5825), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.95251,-0.43194,-91.23862,-0.43111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.23862,-0.43111,-90.95251,-0.43194), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.22696,-0.38944,-91.23862,-0.43111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.34167,-0.31944,-91.22696,-0.38944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.17973,-0.22222,-91.40834,-0.20917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.40834,-0.20917,-91.17973,-0.22222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.37807,-0.19139,-91.40834,-0.20917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.55,-0.05611,-91.57973,-0.04611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.57973,-0.04611,-91.55,-0.05611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.20473,-0.02417,-91.43195,-0.01861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.43195,-0.01861,-91.47751,-0.01361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.47751,-0.01361,-91.43195,-0.01861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.6039,0,-91.47751,-0.01361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.50307,0.05694,-91.31029,0.08417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.31029,0.08417,-91.49501,0.09944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.49501,0.09944,-91.31029,0.08417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.31639,0.13694,-91.37001,0.15083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.37001,0.15083,-91.31639,0.13694), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.50446,-0.49611,-91.40695,-0.46056), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.50446,-0.49611,-91.40695,-0.46056), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.40695,-0.46056,-91.61389,-0.45361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.61389,-0.45361,-91.40695,-0.46056), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.39557,-0.32972,-91.66612,-0.28917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.66612,-0.28917,-91.46695,-0.25083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-91.46695,-0.25083,-91.66612,-0.28917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.61168,-0.37556,-90.77167,-0.34417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.61168,-0.37556,-90.77167,-0.34417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.77167,-0.34417,-90.61168,-0.37556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.55029,-0.30917,-90.77167,-0.34417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.87222,-0.26528,-90.58556,-0.24556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.58556,-0.24556,-90.87222,-0.26528), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.77362,-0.155,-90.79333,-0.14944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.79333,-0.14944,-90.77362,-0.155), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.3439,-0.78,-90.54333,-0.68889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.3439,-0.78,-90.54333,-0.68889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.54333,-0.68889,-90.18918,-0.65667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.18918,-0.65667,-90.55029,-0.63056), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.55029,-0.63056,-90.18918,-0.65667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.18195,-0.55444,-90.48306,-0.52639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.48306,-0.52639,-90.18195,-0.55444), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-90.26083,-0.48861,-90.48306,-0.52639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.47307,-0.94361,-89.62973,-0.92722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.47307,-0.94361,-89.62973,-0.92722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.62973,-0.92722,-89.47307,-0.94361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.61418,-0.89444,-89.38196,-0.87528), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.38196,-0.87528,-89.61418,-0.89444), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.24501,-0.70806,-89.36195,-0.69083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-89.36195,-0.69083,-89.24501,-0.70806), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.21695,-3.03639,-80.27112,-3.02111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.21695,-3.03639,-80.27112,-3.02111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.27112,-3.02111,-80.11806,-3.01139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.11806,-3.01139,-80.27112,-3.02111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.10001,-2.9525,-80.11806,-3.01139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.26501,-2.85083,-80.02112,-2.84972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.02112,-2.84972,-80.26501,-2.85083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.14445,-2.84472,-80.02112,-2.84972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.08723,-2.82861,-80.14445,-2.84472), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.91237,-2.74839,-80.20668,-2.72417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.20668,-2.72417,-79.90334,-2.71972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.90334,-2.71972,-80.20668,-2.72417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.03835,-2.66361,-79.90334,-2.71972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.04944,-5.00333,-79.35666,-4.89111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.35666,-4.89111,-78.89139,-4.84417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.89139,-4.84417,-79.35666,-4.89111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.91833,-4.73917,-79.48083,-4.71334), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.48083,-4.71334,-78.91833,-4.73917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.86583,-4.66639,-79.48083,-4.71334), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.50307,-4.56861,-78.66833,-4.55917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.66833,-4.55917,-79.50307,-4.56861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.495,-4.53306,-78.66833,-4.55917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.78944,-4.48306,-80.39417,-4.47695), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.39417,-4.47695,-79.78944,-4.48306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.85722,-4.4475,-80.46777,-4.43889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.46777,-4.43889,-78.62471,-4.43667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.62471,-4.43667,-80.46777,-4.43889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.64999,-4.43278,-78.62471,-4.43667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.48277,-4.39972,-79.89917,-4.39333), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.89917,-4.39333,-80.48277,-4.39972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.98083,-4.38389,-78.67027,-4.37917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.67027,-4.37917,-79.98083,-4.38389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.66055,-4.30222,-80.13072,-4.28127), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.13072,-4.28127,-78.66055,-4.30222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.44777,-4.22195,-80.33556,-4.19944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.33556,-4.19944,-80.48083,-4.17917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.48083,-4.17917,-80.33556,-4.19944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.31639,-4.01306,-80.47472,-4.00722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.47472,-4.00722,-80.31639,-4.01306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.42332,-3.97833,-80.47472,-4.00722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.53168,-3.91278,-80.15639,-3.88778), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.15639,-3.88778,-78.53168,-3.91278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.4025,-3.75361,-80.22,-3.68833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.22,-3.68833,-78.4025,-3.75361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.21278,-3.59583,-78.25612,-3.51694), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.25612,-3.51694,-78.22112,-3.50861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.22112,-3.50861,-78.25612,-3.51694), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.27112,-3.47083,-78.22112,-3.50861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.17027,-3.43139,-78.26056,-3.42556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.26056,-3.42556,-78.33778,-3.42278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.33778,-3.42278,-80.25389,-3.42083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.25389,-3.42083,-78.33778,-3.42278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.28473,-3.40806,-80.25389,-3.42083), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.34039,-3.38227,-80.33749,-3.37489), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.33749,-3.37489,-78.21584,-3.37389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.21584,-3.37389,-80.33749,-3.37489), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.13556,-3.32889,-78.21584,-3.37389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.03056,-3.28056,-80.13556,-3.32889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.04333,-3.20944,-80.06111,-3.20889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.06111,-3.20889,-80.04333,-3.20944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.90334,-3.02695,-79.87807,-3.01861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.87807,-3.01861,-77.90334,-3.02695), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.88278,-2.95944,-79.87807,-3.01861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.34222,-2.8075,-80.25667,-2.73639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.25667,-2.73639,-80.32806,-2.71), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.32806,-2.71,-80.24306,-2.69389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.24306,-2.69389,-80.32806,-2.71), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.28001,-2.61889,-80.00279,-2.6125), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.00279,-2.6125,-80.28001,-2.61889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.18251,-2.59306,-80.05556,-2.58889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.05556,-2.58889,-80.18251,-2.59306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.71826,-2.58465,-80.05556,-2.58889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.91945,-2.57306,-76.66139,-2.57278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.66139,-2.57278,-79.91945,-2.57306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.06361,-2.55361,-76.66139,-2.57278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.00162,-2.50033,-79.93556,-2.49944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.93556,-2.49944,-80.00162,-2.50033), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.03723,-2.48278,-79.93279,-2.46944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.93279,-2.46944,-79.77751,-2.46842), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.77751,-2.46842,-79.73573,-2.46766), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.73573,-2.46766,-79.77751,-2.46842), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.07918,-2.46306,-79.73573,-2.46766), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.87195,-2.43889,-80.02917,-2.42639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.02917,-2.42639,-79.87195,-2.43889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.68695,-2.39417,-80.02917,-2.42639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.02945,-2.345,-80.91528,-2.32), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.91528,-2.32,-79.95168,-2.31028), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.95168,-2.31028,-80.91528,-2.32), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.86446,-2.26861,-79.95168,-2.31028), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.90279,-2.21139,-80.93056,-2.20806), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.93056,-2.20806,-79.90279,-2.21139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.14417,-2.18389,-80.85474,-2.17528), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.85474,-2.17528,-76.14417,-2.18389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-81.01112,-2.16417,-79.86974,-2.15389), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.86974,-2.15389,-81.01112,-2.16417), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.89001,-2.10306,-80.75584,-2.08222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.75584,-2.08222,-79.89001,-2.10306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.76363,-2.00917,-80.75584,-2.08222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.73083,-1.91945,-79.76363,-2.00917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.79991,-1.71168,-80.85556,-1.59278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.85556,-1.59278,-75.59029,-1.55333), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.59029,-1.55333,-80.82362,-1.54139), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.82362,-1.54139,-75.59029,-1.55333), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.75945,-1.31945,-80.89612,-1.12278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.89612,-1.12278,-80.91112,-1.03111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.91112,-1.03111,-75.21861,-0.97722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.21861,-0.97722,-75.34389,-0.97611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.34389,-0.97611,-75.21861,-0.97722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.61584,-0.92667,-80.83,-0.92639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.83,-0.92639,-80.61584,-0.92667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.40222,-0.92278,-80.83,-0.92639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.56973,-0.89,-75.40222,-0.92278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.29138,-0.71833,-80.33917,-0.63639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.33917,-0.63639,-80.3925,-0.62944), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.3925,-0.62944,-80.33917,-0.63639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.245,-0.61972,-80.26973,-0.61861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.26973,-0.61861,-75.245,-0.61972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.37473,-0.61167,-80.26973,-0.61861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.44139,-0.57472,-80.40195,-0.57028), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.40195,-0.57028,-80.41862,-0.56833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.41862,-0.56833,-80.40195,-0.57028), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.26279,-0.52306,-80.41862,-0.56833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.36389,-0.46667,-75.26279,-0.52306), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.49973,-0.36278,-80.42473,-0.31583), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.42473,-0.31583,-80.49973,-0.36278), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.48555,-0.22667,-80.37973,-0.22556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.37973,-0.22556,-75.48555,-0.22667), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.62469,-0.17681,-80.29556,-0.16556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.29556,-0.16556,-75.62469,-0.17681), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.36,-0.15333,-80.29556,-0.16556), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.28525,-0.11912,-75.62805,-0.10833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.62805,-0.10833,-75.28525,-0.11912), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.43443,-0.06,-75.62805,-0.10833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.10806,0.00639,-75.62805,0.02889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.62805,0.02889,-80.10806,0.00639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.59778,0.05583,-75.77528,0.05639), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-75.77528,0.05639,-75.59778,0.05583), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.04056,0.16722,-76.73306,0.2325), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.73306,0.2325,-76.88028,0.24222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.88028,0.24222,-76.40889,0.24833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.40889,0.24833,-76.88028,0.24222), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.72139,0.28056,-76.40889,0.24833), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.10611,0.32167,-77.19666,0.33472), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.19666,0.33472,-76.04778,0.34), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.04778,0.34,-77.19666,0.33472), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.08084,0.35917,-76.33168,0.37361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.33168,0.37361,-76.11583,0.37917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.11583,0.37917,-77.37971,0.38472), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.37971,0.38472,-76.11583,0.37917), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.40834,0.39694,-77.37971,0.38472), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-76.30305,0.41889,-77.42722,0.42444), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.42722,0.42444,-76.30305,0.41889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.02335,0.59111,-77.44444,0.62722), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.44444,0.62722,-77.54167,0.65611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.54167,0.65611,-77.47639,0.66361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.47639,0.66361,-77.54167,0.65611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.10945,0.69528,-77.47639,0.66361), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.65834,0.745,-80.10945,0.69528), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.8094,0.80549,-77.65594,0.81525), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.65594,0.81525,-77.8094,0.80549), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.9689,0.82778,-77.89,0.82972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.89,0.82972,-80.05556,0.83111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-80.05556,0.83111,-77.89,0.82972), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.61696,0.84111,-77.71472,0.84583), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.71472,0.84583,-79.61696,0.84111), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-77.91637,0.88166,-79.6664,0.90444), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.6664,0.90444,-77.91637,0.88166), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.63972,0.96583,-79.73889,0.96694), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.73889,0.96694,-79.63972,0.96583), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.65112,0.99556,-79.73889,0.96694), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.35194,1.06722,-79.16223,1.09889), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.16223,1.09889,-78.98918,1.11611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.98918,1.11611,-78.96751,1.1325), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.96751,1.1325,-78.98918,1.11611), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.00974,1.18889,-78.47333,1.19778), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.47333,1.19778,-78.56027,1.19861), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.56027,1.19861,-78.47333,1.19778), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.95251,1.2175,-79.05917,1.22194), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-79.05917,1.22194,-78.95251,1.2175), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.8889,1.23972,-79.05917,1.22194), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.63417,1.26444,-78.59593,1.26929), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.63417,1.26444,-78.59593,1.26929), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.59593,1.26929,-78.63417,1.26444), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.87946,1.32222,-78.59593,1.26929), mapfile, tile_dir, 0, 11, "ec-ecuador")
	render_tiles((-78.80987,1.43793,-78.87946,1.32222), mapfile, tile_dir, 0, 11, "ec-ecuador")