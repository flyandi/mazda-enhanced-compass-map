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
    # Region: PE
    # Region Name: Peru

	render_tiles((-70.40332,-18.34799,-70.17833,-18.32889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.17833,-18.32889,-70.40332,-18.34799), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.96584,-18.25611,-70.17833,-18.32889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.84167,-18.12862,-70.77306,-18.08278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.77306,-18.08278,-69.84167,-18.12862), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.76917,-17.94695,-70.92223,-17.94334), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.92223,-17.94334,-69.76917,-17.94695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.05862,-17.87917,-70.92223,-17.94334), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.20113,-17.76723,-69.84834,-17.71667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.84834,-17.71667,-71.38556,-17.69556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.38556,-17.69556,-69.84834,-17.71667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.68416,-17.66362,-69.815,-17.65223), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.815,-17.65223,-69.68416,-17.66362), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.35612,-17.62806,-69.815,-17.65223), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.49973,-17.50528,-71.3989,-17.39917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.3989,-17.39917,-69.50111,-17.37889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.50111,-17.37889,-71.3989,-17.39917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.59085,-17.29528,-69.6525,-17.28584), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.6525,-17.28584,-69.59085,-17.29528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.5439,-17.27362,-69.6525,-17.28584), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.82918,-17.18945,-69.61806,-17.18667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.61806,-17.18667,-71.82918,-17.18945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.38583,-17.05084,-72.00696,-17.04556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.00696,-17.04556,-69.38583,-17.05084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.41112,-17.02056,-72.00696,-17.04556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.34361,-16.9825,-72.15472,-16.95806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.15472,-16.95806,-69.34361,-16.9825), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.36473,-16.75834,-69.1664,-16.72056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.1664,-16.72056,-69.09447,-16.70723), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.09447,-16.70723,-69.1664,-16.72056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.51889,-16.68362,-69.09447,-16.70723), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.72945,-16.65084,-69.0025,-16.64473), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.0025,-16.64473,-72.72945,-16.65084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.03778,-16.60306,-69.03751,-16.57139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.03751,-16.57139,-69.02812,-16.54087), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.02812,-16.54087,-69.03751,-16.57139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.99083,-16.41972,-73.25307,-16.41056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.25307,-16.41056,-73.18056,-16.40834), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.18056,-16.40834,-73.25307,-16.41056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.82611,-16.35139,-73.32028,-16.33501), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.32028,-16.33501,-68.82611,-16.35139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.83363,-16.3025,-68.87933,-16.27074), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.87933,-16.27074,-73.55139,-16.25306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.55139,-16.25306,-68.91916,-16.24305), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.91916,-16.24305,-73.55139,-16.25306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.70418,-16.2314,-68.91916,-16.24305), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.15056,-16.21861,-69.10077,-16.21555), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.10077,-16.21555,-68.95879,-16.2155), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.95879,-16.2155,-69.10077,-16.21555), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.97028,-16.2075,-68.95879,-16.2155), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.21472,-16.15639,-73.88112,-16.12112), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.88112,-16.12112,-69.21472,-16.15639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.05751,-15.95306,-74.39862,-15.8275), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.39862,-15.8275,-74.48361,-15.72084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.48361,-15.72084,-69.42223,-15.61806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.42223,-15.61806,-74.76112,-15.59278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.76112,-15.59278,-69.42223,-15.61806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.36858,-15.51216,-74.76112,-15.59278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.36858,-15.51216,-74.76112,-15.59278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.32722,-15.43056,-69.27583,-15.40417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.27583,-15.40417,-69.32722,-15.43056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.20251,-15.36945,-69.28555,-15.35722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.28555,-15.35722,-75.20251,-15.36945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.15723,-15.32028,-69.28555,-15.35722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.2614,-15.26056,-69.14056,-15.25473), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.14056,-15.25473,-75.2614,-15.26056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.22501,-15.23111,-69.13806,-15.22528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.13806,-15.22528,-75.22501,-15.23111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.29279,-15.15778,-75.37167,-15.14695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.37167,-15.14695,-75.29279,-15.15778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.27806,-15.11111,-75.37167,-15.14695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.41057,-15.06334,-69.27806,-15.11111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.50778,-14.96361,-69.38417,-14.95889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.38417,-14.95889,-75.50778,-14.96361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.50528,-14.92584,-69.38417,-14.95889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.36166,-14.79361,-69.23972,-14.72528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.23972,-14.72528,-75.93333,-14.65806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.93333,-14.65806,-69.245,-14.61639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.245,-14.61639,-75.93333,-14.65806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.15372,-14.57234,-69.22084,-14.57028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.22084,-14.57028,-69.15372,-14.57234), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.98779,-14.4675,-68.97917,-14.3675), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.97917,-14.3675,-69.00696,-14.33361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.00696,-14.33361,-76.15224,-14.33056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.15224,-14.33056,-69.00696,-14.33361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.11584,-14.30139,-76.15224,-14.33056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.98444,-14.23028,-68.85333,-14.19917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.85333,-14.19917,-76.20418,-14.17389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.20418,-14.17389,-76.2914,-14.17362), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.2914,-14.17362,-76.20418,-14.17389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.86777,-14.11,-76.30223,-14.10306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.30223,-14.10306,-68.86777,-14.11), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.27917,-14.06278,-76.30223,-14.10306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.97749,-13.96167,-76.37056,-13.91056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.37056,-13.91056,-76.30168,-13.89445), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.30168,-13.89445,-76.39528,-13.87945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.39528,-13.87945,-76.30168,-13.89445), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.98222,-13.86167,-76.27287,-13.85485), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.27287,-13.85485,-68.98222,-13.86167), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.36835,-13.80695,-76.30196,-13.79834), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.30196,-13.79834,-76.36835,-13.80695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.08556,-13.67445,-69.08,-13.64361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.08,-13.64361,-69.01167,-13.63417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.01167,-13.63417,-76.20113,-13.63195), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.20113,-13.63195,-69.01167,-13.63417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.96417,-13.48333,-76.1925,-13.4275), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.1925,-13.4275,-68.96417,-13.48333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.49251,-13.04611,-68.97472,-12.92639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.97472,-12.92639,-76.5264,-12.84861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.5264,-12.84861,-76.61279,-12.78056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.61279,-12.78056,-68.88194,-12.75889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.88194,-12.75889,-76.61279,-12.78056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.80417,-12.73028,-76.64528,-12.71111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.64528,-12.71111,-68.80417,-12.73028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.73138,-12.66972,-76.64528,-12.71111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.78111,-12.61778,-68.73138,-12.66972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.6875,-12.51945,-76.80695,-12.49417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.80695,-12.49417,-68.6875,-12.51945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.80112,-12.38778,-68.77084,-12.30861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-68.77084,-12.30861,-76.93251,-12.25945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.93251,-12.25945,-77.01918,-12.22584), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.01918,-12.22584,-76.93251,-12.25945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.07112,-12.10306,-77.17612,-12.07084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.17612,-12.07084,-77.07112,-12.10306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.14445,-11.94945,-77.17612,-12.07084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.18695,-11.79667,-77.14445,-11.94945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.15028,-11.63222,-77.28557,-11.54361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.28557,-11.54361,-69.15028,-11.63222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.65028,-11.29639,-77.6664,-11.24611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.6664,-11.24611,-77.64001,-11.21639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.64001,-11.21639,-77.6664,-11.24611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.61057,-11.18,-77.64001,-11.21639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.26945,-11.05861,-70.39417,-11.05111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.39417,-11.05111,-70.26945,-11.05861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.63194,-11.00306,-70.0275,-10.96806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.0275,-10.96806,-69.56744,-10.95047), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.56744,-10.95047,-70.0275,-10.96806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.53307,-10.93222,-77.68251,-10.92973), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.68251,-10.92973,-70.53307,-10.93222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.92917,-10.91445,-77.68251,-10.92973), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.9364,-10.52639,-70.62999,-10.51167), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.62999,-10.51167,-77.9364,-10.52639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.19612,-10.11,-78.17778,-10.06473), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.17778,-10.06473,-78.19612,-10.11), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.18582,-10.00389,-71.5014,-10.00361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.5014,-10.00361,-72.18582,-10.00389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.29916,-9.99639,-71.5014,-10.00361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.24501,-9.95334,-71.29916,-9.99639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.15028,-9.88889,-70.62805,-9.83361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.62805,-9.83361,-72.18028,-9.80139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.18028,-9.80139,-78.24612,-9.7925), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.24612,-9.7925,-72.18028,-9.80139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.5575,-9.77611,-78.24612,-9.7925), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.27112,-9.74667,-70.5575,-9.77611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.90695,-9.71472,-70.53722,-9.69111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.53722,-9.69111,-70.90695,-9.71472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.37889,-9.61361,-70.59666,-9.60611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.59666,-9.60611,-78.37889,-9.61361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.56946,-9.56972,-70.70889,-9.54333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.70889,-9.54333,-72.30556,-9.53), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.30556,-9.53,-70.70889,-9.54333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.4286,-9.48306,-70.53389,-9.45389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.53389,-9.45389,-72.58612,-9.45361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.58612,-9.45361,-70.53389,-9.45389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.58444,-9.43806,-78.39806,-9.43417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.39806,-9.43417,-70.58444,-9.43806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.20528,-9.40722,-78.39806,-9.43417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.53946,-9.26056,-78.58778,-9.23306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.58778,-9.23306,-78.50974,-9.22695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.50974,-9.22695,-78.58778,-9.23306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.51556,-9.17472,-78.55556,-9.17389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.55556,-9.17389,-78.51556,-9.17472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.62611,-9.16111,-72.97556,-9.15472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.97556,-9.15472,-78.62611,-9.16111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.57195,-9.13945,-72.97556,-9.15472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.58223,-9.09694,-72.94861,-9.08389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.94861,-9.08389,-78.58223,-9.09694), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.64806,-9.06334,-72.94861,-9.08389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.96445,-8.98333,-78.66389,-8.9175), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.66389,-8.9175,-72.96445,-8.98333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.77444,-8.75917,-73.17,-8.71028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.17,-8.71028,-78.75111,-8.69084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.75111,-8.69084,-73.17,-8.71028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.2975,-8.65111,-78.75111,-8.69084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.77196,-8.58639,-73.2975,-8.65111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.35194,-8.46833,-78.94667,-8.42778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.94667,-8.42778,-73.35194,-8.46833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.9164,-8.37973,-73.53333,-8.35556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.53333,-8.35556,-78.9164,-8.37973), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.9789,-8.25695,-73.53333,-8.35556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.13612,-8.09056,-73.65111,-8.01611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.65111,-8.01611,-79.22723,-7.99889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.22723,-7.99889,-73.65111,-8.01611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.76723,-7.95722,-79.22723,-7.99889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.71417,-7.8725,-73.78223,-7.87111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.78223,-7.87111,-73.71417,-7.8725), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.37779,-7.84389,-73.78223,-7.87111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.69276,-7.80917,-79.37779,-7.84389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.71056,-7.77139,-73.69276,-7.80917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.83333,-7.72556,-73.71056,-7.77139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.00945,-7.535,-73.94888,-7.50167), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.94888,-7.50167,-74.00945,-7.535), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.8925,-7.375,-73.97278,-7.34584), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.97278,-7.34584,-73.7114,-7.31917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.7114,-7.31917,-73.97278,-7.34584), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.70418,-7.285,-79.63806,-7.2575), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.63806,-7.2575,-73.70418,-7.285), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.7114,-7.17806,-73.80147,-7.11724), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.80147,-7.11724,-79.71501,-7.11667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.71501,-7.11667,-73.80147,-7.11724), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.75473,-6.895,-79.94446,-6.87556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.94446,-6.87556,-73.75473,-6.895), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.98279,-6.76445,-79.94446,-6.87556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.1239,-6.63917,-73.43443,-6.63306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.43443,-6.63306,-80.1239,-6.63917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.24583,-6.56806,-73.19194,-6.55361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.19194,-6.55361,-73.24583,-6.56806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.42473,-6.48917,-73.12389,-6.44778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.12389,-6.44778,-80.42473,-6.48917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.16417,-6.20778,-73.21611,-6.15528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.21611,-6.15528,-73.16417,-6.20778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.18224,-6.09917,-73.21889,-6.04583), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.21889,-6.04583,-81.18224,-6.09917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.19084,-5.94834,-80.97723,-5.87028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.97723,-5.87028,-80.92639,-5.85222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.92639,-5.85222,-80.97723,-5.87028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.08974,-5.80222,-80.8889,-5.79056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.8889,-5.79056,-81.08974,-5.80222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.87279,-5.6425,-72.95056,-5.56417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.95056,-5.56417,-80.92307,-5.5025), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.92307,-5.5025,-72.95056,-5.56417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.91833,-5.3225,-72.89195,-5.2075), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.89195,-5.2075,-81.21001,-5.19278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.21001,-5.19278,-72.89195,-5.2075), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.91777,-5.15195,-81.21001,-5.19278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.1714,-5.06389,-81.09917,-5.06333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.09917,-5.06333,-81.1714,-5.06389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.63501,-5.05917,-81.09917,-5.06333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.74138,-5.05139,-72.63501,-5.05917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.04944,-5.00333,-81.10973,-4.96833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.10973,-4.96833,-72.53195,-4.95306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.53195,-4.95306,-81.10973,-4.96833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.35666,-4.89111,-72.41278,-4.88639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.41278,-4.88639,-79.35666,-4.89111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.89139,-4.84417,-72.41278,-4.88639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.32556,-4.76084,-72.24388,-4.75889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.24388,-4.75889,-72.32556,-4.76084), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.91833,-4.73917,-72.24388,-4.75889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.48083,-4.71334,-78.91833,-4.73917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.3564,-4.68195,-78.86583,-4.66639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.86583,-4.66639,-81.3564,-4.68195), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.50307,-4.56861,-78.66833,-4.55917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.66833,-4.55917,-81.30334,-4.55222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.30334,-4.55222,-78.66833,-4.55917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.495,-4.53306,-81.30334,-4.55222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.65639,-4.50917,-71.8264,-4.50778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.8264,-4.50778,-71.65639,-4.50917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.78944,-4.48306,-80.39417,-4.47695), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.39417,-4.47695,-71.69055,-4.47389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.69055,-4.47389,-81.32751,-4.47111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.32751,-4.47111,-71.69055,-4.47389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.85722,-4.4475,-80.46777,-4.43889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.46777,-4.43889,-71.30556,-4.43833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.30556,-4.43833,-80.46777,-4.43889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.62471,-4.43667,-71.30556,-4.43833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.40668,-4.435,-78.62471,-4.43667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.64999,-4.43278,-71.40668,-4.435), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.20056,-4.41139,-80.48277,-4.39972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.48277,-4.39972,-71.12944,-4.39889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.12944,-4.39889,-80.48277,-4.39972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.89917,-4.39333,-71.12944,-4.39889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-79.98083,-4.38389,-71.29083,-4.38361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.29083,-4.38361,-79.98083,-4.38389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.97278,-4.37945,-78.67027,-4.37917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.67027,-4.37917,-70.97278,-4.37945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.23332,-4.37611,-78.67027,-4.37917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.16972,-4.35778,-70.98889,-4.34139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.98889,-4.34139,-70.03528,-4.33611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.03528,-4.33611,-70.98889,-4.34139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.20862,-4.32528,-70.1725,-4.32472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.1725,-4.32472,-70.20862,-4.32528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.97223,-4.30306,-78.66055,-4.30222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.66055,-4.30222,-69.97223,-4.30306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.88167,-4.29333,-78.66055,-4.30222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.13072,-4.28127,-70.15611,-4.27333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.15611,-4.27333,-80.13072,-4.28127), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.26112,-4.25528,-70.10777,-4.25389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.10777,-4.25389,-81.26112,-4.25528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-69.94658,-4.22423,-80.44777,-4.22195), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.44777,-4.22195,-69.94658,-4.22423), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-81.18945,-4.2025,-80.33556,-4.19944), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.33556,-4.19944,-81.18945,-4.2025), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.58528,-4.19611,-80.33556,-4.19944), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.83501,-4.18778,-70.51195,-4.18083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.51195,-4.18083,-80.48083,-4.17917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.48083,-4.17917,-70.51195,-4.18083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.68443,-4.17417,-80.48083,-4.17917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.76584,-4.14694,-70.54527,-4.13944), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.54527,-4.13944,-70.3239,-4.13722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.3239,-4.13722,-70.54527,-4.13944), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.43332,-4.13167,-70.3239,-4.13722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.65222,-4.11861,-70.62,-4.11583), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.62,-4.11583,-70.65222,-4.11861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.31639,-4.01306,-80.47472,-4.00722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.47472,-4.00722,-80.31639,-4.01306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.42332,-3.97833,-80.98251,-3.9525), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.98251,-3.9525,-80.42332,-3.97833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.53168,-3.91278,-80.15639,-3.88778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.15639,-3.88778,-80.87167,-3.8825), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.87167,-3.8825,-80.15639,-3.88778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.24889,-3.86111,-80.87167,-3.8825), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.51611,-3.86111,-80.87167,-3.8825), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.62077,-3.8259,-70.24889,-3.86111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.37193,-3.78833,-70.72444,-3.77972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.72444,-3.77972,-70.37193,-3.78833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.4025,-3.75361,-80.81361,-3.74361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.81361,-3.74361,-78.4025,-3.75361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.22,-3.68833,-80.81361,-3.74361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.59862,-3.61667,-80.21278,-3.59583), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.21278,-3.59583,-80.59862,-3.61667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.25612,-3.51694,-78.22112,-3.50861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.22112,-3.50861,-80.52972,-3.50556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.52972,-3.50556,-78.22112,-3.50861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.37556,-3.47222,-80.27112,-3.47083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.27112,-3.47083,-80.37556,-3.47222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.17027,-3.43139,-78.26056,-3.42556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.26056,-3.42556,-78.33778,-3.42278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.33778,-3.42278,-80.25389,-3.42083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.25389,-3.42083,-78.33778,-3.42278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.28473,-3.40806,-80.25389,-3.42083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-80.34039,-3.38227,-78.21584,-3.37389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-78.21584,-3.37389,-80.34039,-3.38227), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.90334,-3.02695,-77.34222,-2.8075), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-77.34222,-2.8075,-70.04973,-2.7275), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.04973,-2.7275,-70.12555,-2.69945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.12555,-2.69945,-70.04973,-2.7275), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.07167,-2.66111,-70.12555,-2.69945), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.32695,-2.57833,-76.66139,-2.57278), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.66139,-2.57278,-70.32695,-2.57833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.33833,-2.55556,-70.23694,-2.55222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.23694,-2.55222,-70.33833,-2.55556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.29999,-2.53167,-70.23694,-2.55222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.42055,-2.51111,-72.20889,-2.50639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.20889,-2.50639,-72.89195,-2.50306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.89195,-2.50306,-72.20889,-2.50639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.3075,-2.49555,-70.54083,-2.49472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.54083,-2.49472,-72.64612,-2.49417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.64612,-2.49417,-70.54083,-2.49472), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.80583,-2.48778,-72.64612,-2.49417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.58778,-2.48111,-72.33667,-2.47972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.33667,-2.47972,-70.58778,-2.48111), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.10388,-2.47639,-72.71666,-2.475), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.71666,-2.475,-72.10388,-2.47639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.20889,-2.45833,-72.71666,-2.475), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.69249,-2.44056,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.12999,-2.44056,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.35445,-2.43444,-70.55222,-2.4325), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.74472,-2.43444,-70.55222,-2.4325), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.55222,-2.4325,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.52722,-2.42639,-73.02278,-2.42222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.02278,-2.42222,-70.59973,-2.42083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.59973,-2.42083,-73.02278,-2.42222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.59584,-2.40778,-72.94444,-2.39556), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.94444,-2.39556,-71.35556,-2.39139), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.35556,-2.39139,-72.04805,-2.38722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.04805,-2.38722,-71.89862,-2.38444), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.89862,-2.38444,-72.04805,-2.38722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.03612,-2.36528,-73.08307,-2.36333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.08307,-2.36333,-73.03612,-2.36528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-72.00862,-2.35722,-71.11389,-2.35306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.11389,-2.35306,-71.46056,-2.3525), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.35777,-2.35306,-71.46056,-2.3525), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.46056,-2.3525,-71.11389,-2.35306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.09389,-2.30306,-70.98833,-2.30028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.98833,-2.30028,-71.09389,-2.30306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.75862,-2.29611,-70.98833,-2.30028), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.43028,-2.27695,-70.75862,-2.29611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.1339,-2.24333,-71.51501,-2.23583), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.51501,-2.23583,-73.1339,-2.24333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.08833,-2.22445,-70.97139,-2.22361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-70.97139,-2.22361,-73.08833,-2.22445), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.67944,-2.22167,-70.97139,-2.22361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-76.14417,-2.18389,-71.73138,-2.1675), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.73138,-2.1675,-76.14417,-2.18389), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-71.6886,-2.14972,-73.0575,-2.14083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.0575,-2.14083,-71.6886,-2.14972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.09334,-1.91806,-73.30943,-1.87222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.30943,-1.87222,-73.09334,-1.91806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.19916,-1.80306,-73.50751,-1.74833), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.50751,-1.74833,-73.19916,-1.80306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.44638,-1.59639,-75.59029,-1.55333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.59029,-1.55333,-73.44638,-1.59639), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.51723,-1.50389,-75.59029,-1.55333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.55638,-1.37083,-73.75557,-1.29417), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.75557,-1.29417,-73.84416,-1.26056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.84416,-1.26056,-73.73889,-1.23861), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.73889,-1.23861,-73.84416,-1.26056), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.85028,-1.17083,-73.93832,-1.16889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-73.93832,-1.16889,-73.85028,-1.17083), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.07779,-1.07361,-74.02055,-1.03528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.02055,-1.03528,-74.23694,-1.01889), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.23694,-1.01889,-74.02055,-1.03528), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.21861,-0.97722,-75.34389,-0.97611), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.34389,-0.97611,-75.21861,-0.97722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.40222,-0.92278,-74.29361,-0.90222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.29361,-0.90222,-74.24249,-0.89), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.24249,-0.89,-74.29361,-0.90222), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.23889,-0.85917,-74.24249,-0.89), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.30916,-0.81333,-74.23889,-0.85917), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.29138,-0.71833,-74.30916,-0.81333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.245,-0.61972,-74.3761,-0.56806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.3761,-0.56806,-75.26279,-0.52306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.26279,-0.52306,-74.3761,-0.56806), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.36389,-0.46667,-75.26279,-0.52306), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.63834,-0.41028,-74.60777,-0.38361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.60777,-0.38361,-74.70667,-0.36778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.70667,-0.36778,-74.60777,-0.38361), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.66389,-0.34667,-74.70667,-0.36778), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.73083,-0.26639,-74.8761,-0.22722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.8761,-0.22722,-75.48555,-0.22667), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.48555,-0.22667,-74.8761,-0.22722), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-74.78667,-0.20056,-75.62469,-0.17681), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.62469,-0.17681,-75.36,-0.15333), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.36,-0.15333,-75.62469,-0.17681), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.245,-0.11972,-75.28525,-0.11912), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.28525,-0.11912,-75.245,-0.11972), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.62805,-0.10833,-75.28525,-0.11912), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.15083,-0.03889,-75.21222,-0.0375), mapfile, tile_dir, 0, 11, "pe-peru")
	render_tiles((-75.21222,-0.0375,-75.15083,-0.03889), mapfile, tile_dir, 0, 11, "pe-peru")