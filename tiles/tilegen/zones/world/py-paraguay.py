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
    # Region: PY
    # Region Name: Paraguay

	render_tiles((-56.39223,-27.58612,-56.36111,-27.58417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.36111,-27.58417,-56.39223,-27.58612), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.45278,-27.55084,-56.36111,-27.58417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.7261,-27.50362,-56.76926,-27.49974), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.76926,-27.49974,-56.7261,-27.50362), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.7842,-27.49233,-57.05944,-27.48889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.05944,-27.48889,-56.7842,-27.49233), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.51501,-27.46417,-56.67333,-27.46139), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.67333,-27.46139,-56.51501,-27.46417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.27111,-27.46139,-56.51501,-27.46417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.58583,-27.44806,-56.67333,-27.46139), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.78538,-27.43449,-57.40445,-27.42417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.40445,-27.42417,-56.9325,-27.42306), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.9325,-27.42306,-57.40445,-27.42417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.87417,-27.42112,-56.9325,-27.42306), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.27472,-27.39611,-56.87417,-27.42112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.60361,-27.34639,-55.88805,-27.33389), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.88805,-27.33389,-55.60361,-27.34639), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.14861,-27.31223,-57.73556,-27.30528), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.73556,-27.30528,-56.0425,-27.30362), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.0425,-27.30362,-57.73556,-27.30528), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.58167,-27.30195,-56.0425,-27.30362), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.60265,-27.29418,-55.58167,-27.30195), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.01112,-27.27028,-58.60265,-27.29418), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.65889,-27.18139,-55.59888,-27.16445), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.59888,-27.16445,-58.65889,-27.18139), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.63612,-27.13723,-58.56361,-27.11695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.56361,-27.11695,-58.63612,-27.13723), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.45194,-27.09139,-58.56361,-27.11695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.50973,-27.05862,-58.54611,-27.04584), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.54611,-27.04584,-58.50973,-27.05862), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.44583,-27.0239,-58.54611,-27.04584), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.39611,-26.97167,-55.18639,-26.96278), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.18639,-26.96278,-55.39611,-26.97167), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.12611,-26.945,-58.47361,-26.93612), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.47361,-26.93612,-55.12611,-26.945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.25111,-26.93612,-55.12611,-26.945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.31778,-26.87834,-55.11777,-26.85778), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.11777,-26.85778,-58.31778,-26.87834), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.34222,-26.81389,-54.95055,-26.77056), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.95055,-26.77056,-58.24611,-26.76473), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.24611,-26.76473,-54.95055,-26.77056), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.93056,-26.68834,-54.80361,-26.65945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.80361,-26.65945,-58.18527,-26.65862), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.18527,-26.65862,-54.80361,-26.65945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.88333,-26.65611,-58.18527,-26.65862), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.23972,-26.64973,-54.88333,-26.65611), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.16833,-26.59695,-58.23972,-26.64973), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.21806,-26.54056,-58.20722,-26.49417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.20722,-26.49417,-58.21806,-26.54056), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.21139,-26.42583,-58.20722,-26.49417), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.16945,-26.34223,-58.17223,-26.27278), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.17223,-26.27278,-58.10472,-26.24028), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.10472,-26.24028,-58.12194,-26.21889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.12194,-26.21889,-54.63361,-26.20028), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.63361,-26.20028,-58.12194,-26.21889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.85389,-25.99639,-54.66306,-25.97722), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.66306,-25.97722,-57.85389,-25.99639), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.60167,-25.95278,-57.90083,-25.9525), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.90083,-25.9525,-54.60167,-25.95278), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.75001,-25.71667,-57.75111,-25.67), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.75111,-25.67,-54.64417,-25.65695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.64417,-25.65695,-57.75111,-25.67), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.58389,-25.63667,-54.64417,-25.65695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.59611,-25.57487,-57.57557,-25.57306), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.57557,-25.57306,-54.59611,-25.57487), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.61722,-25.43806,-57.55778,-25.43333), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.55778,-25.43333,-54.61722,-25.43806), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.64028,-25.37889,-57.65028,-25.33001), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.65028,-25.33001,-57.64028,-25.37889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.42223,-25.13695,-57.87055,-25.08778), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.87055,-25.08778,-57.98083,-25.0775), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.98083,-25.0775,-57.87055,-25.08778), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.45778,-25.04973,-57.98083,-25.0775), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.33805,-24.9975,-54.45778,-25.04973), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.22861,-24.93834,-58.33805,-24.9975), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.46083,-24.8525,-54.4019,-24.82359), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.4019,-24.82359,-58.68666,-24.81612), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.68666,-24.81612,-54.4019,-24.82359), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.88556,-24.72639,-58.68666,-24.81612), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.31194,-24.60917,-59.13139,-24.59889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.13139,-24.59889,-54.31194,-24.60917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.33167,-24.49278,-54.27444,-24.41139), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.27444,-24.41139,-59.42639,-24.39834), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.42639,-24.39834,-54.27444,-24.41139), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.25306,-24.33139,-59.61584,-24.28639), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.61584,-24.28639,-59.63825,-24.24752), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.63825,-24.24752,-54.31945,-24.23722), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.31945,-24.23722,-59.63825,-24.24752), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.32944,-24.12167,-54.24306,-24.05223), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.24306,-24.05223,-60.17028,-24.04167), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-60.17028,-24.04167,-54.24306,-24.05223), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-60.31306,-24.02028,-55.20444,-24.01834), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.20444,-24.01834,-60.31306,-24.02028), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-60.03806,-24.00973,-55.20444,-24.01834), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.39667,-23.97112,-54.9325,-23.96861), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.9325,-23.96861,-55.39667,-23.97112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.41889,-23.93917,-54.9325,-23.96861), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.42889,-23.90084,-60.64084,-23.89556), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-60.64084,-23.89556,-54.42889,-23.90084), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-54.63805,-23.80751,-61.01501,-23.80611), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.01501,-23.80611,-54.63805,-23.80751), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.44194,-23.70084,-61.12417,-23.59778), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.12417,-23.59778,-55.5225,-23.59695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.5225,-23.59695,-61.12417,-23.59778), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.22472,-23.55528,-55.5225,-23.59695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.50278,-23.37889,-55.55055,-23.31917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.55055,-23.31917,-61.62,-23.28584), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.62,-23.28584,-55.55055,-23.31917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.52722,-23.22,-61.62,-23.28584), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.99917,-22.99472,-55.63,-22.98695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.63,-22.98695,-61.99917,-22.99472), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.00556,-22.94223,-55.63,-22.98695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.61,-22.70861,-62.26083,-22.60028), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.26083,-22.60028,-62.2375,-22.56695), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.2375,-22.56695,-55.73083,-22.53945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.73083,-22.53945,-62.25861,-22.51889), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.25861,-22.51889,-55.73083,-22.53945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.74555,-22.39528,-62.62805,-22.30333), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.62805,-22.30333,-55.92167,-22.30056), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.92167,-22.30056,-56.83417,-22.29834), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.83417,-22.29834,-55.92167,-22.30056), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.87583,-22.285,-55.85973,-22.28334), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-55.85973,-22.28334,-56.87583,-22.285), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.20028,-22.27584,-55.85973,-22.28334), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.63778,-22.26278,-56.78083,-22.25306), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.78083,-22.25306,-62.62389,-22.24916), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.62389,-22.24916,-56.87389,-22.24583), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.87389,-22.24583,-62.62389,-22.24916), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.63866,-22.23663,-57.00086,-22.23308), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.00086,-22.23308,-62.63866,-22.23663), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.17722,-22.21584,-56.70111,-22.21445), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.70111,-22.21445,-57.17722,-22.21584), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.32084,-22.20167,-56.70111,-22.21445), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.51694,-22.18473,-57.61472,-22.17306), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.61472,-22.17306,-57.51694,-22.18473), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.87944,-22.13056,-56.38389,-22.10112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.38389,-22.10112,-57.64555,-22.09917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.64555,-22.09917,-56.38389,-22.10112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.9828,-22.08923,-56.48634,-22.08267), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.48634,-22.08267,-57.9828,-22.08923), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-56.39639,-22.06695,-56.48634,-22.08267), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.92278,-21.89528,-57.95667,-21.8475), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.95667,-21.8475,-57.92278,-21.89528), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.89195,-21.68945,-57.95667,-21.8475), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.95417,-21.51084,-57.89195,-21.68945), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.8514,-21.33055,-57.90666,-21.28112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.90666,-21.28112,-57.8514,-21.33055), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.83833,-21.20056,-57.90666,-21.28112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.25917,-21.05695,-57.81945,-20.95611), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.81945,-20.95611,-57.92111,-20.90223), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.92111,-20.90223,-57.86445,-20.85917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.86445,-20.85917,-57.86139,-20.82917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.86139,-20.82917,-57.86445,-20.85917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.94333,-20.79417,-57.86139,-20.82917), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.85973,-20.73445,-57.97888,-20.71111), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.97888,-20.71111,-57.85973,-20.73445), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.92639,-20.66695,-57.97888,-20.71111), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.99695,-20.61222,-62.26945,-20.56223), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-62.26945,-20.56223,-57.99695,-20.61222), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-57.99084,-20.44084,-58.08556,-20.37112), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.08556,-20.37112,-57.99084,-20.44084), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.14278,-20.28028,-58.10556,-20.26501), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.10556,-20.26501,-58.14278,-20.28028), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.16194,-20.23667,-58.10556,-20.26501), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.11972,-20.20111,-58.15817,-20.16886), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.15817,-20.16886,-58.11972,-20.20111), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.91306,-20.08001,-58.15817,-20.16886), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.15166,-19.82806,-58.2325,-19.78251), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-58.2325,-19.78251,-58.15166,-19.82806), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.74277,-19.645,-58.2325,-19.78251), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-61.74277,-19.645,-58.2325,-19.78251), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-60.61362,-19.45917,-59.10194,-19.34806), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.10194,-19.34806,-59.99084,-19.29667), mapfile, tile_dir, 0, 11, "py-paraguay")
	render_tiles((-59.99084,-19.29667,-59.10194,-19.34806), mapfile, tile_dir, 0, 11, "py-paraguay")