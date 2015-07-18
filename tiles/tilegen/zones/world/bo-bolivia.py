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
    # Region: BO
    # Region Name: Bolivia

	render_tiles((-67.57945,-22.90112,-67.79333,-22.87806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.79333,-22.87806,-64.32501,-22.87362), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.32501,-22.87362,-67.79333,-22.87806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.34584,-22.84723,-67.87639,-22.82806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.87639,-22.82806,-67.18367,-22.82156), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.18367,-22.82156,-67.87639,-22.82806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.12305,-22.71806,-67.01251,-22.64278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.01251,-22.64278,-64.27528,-22.63389), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.27528,-22.63389,-64.45667,-22.63223), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.45667,-22.63223,-64.27528,-22.63389), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.43332,-22.54278,-67.02278,-22.5239), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.02278,-22.5239,-64.43332,-22.54278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.88612,-22.43834,-64.14612,-22.43278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.14612,-22.43278,-67.88612,-22.43834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.77501,-22.42695,-64.14612,-22.43278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.5661,-22.36139,-64.11583,-22.34278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.11583,-22.34278,-64.5661,-22.36139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.93582,-22.29612,-64.53917,-22.27639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.53917,-22.27639,-67.93582,-22.29612), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.56194,-22.24361,-67.9225,-22.24195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.9225,-22.24195,-64.56194,-22.24361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.63866,-22.23663,-67.9225,-22.24195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.73694,-22.2275,-62.63866,-22.23663), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.72278,-22.18139,-64.67027,-22.17112), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.67027,-22.17112,-64.72278,-22.18139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.78944,-22.12278,-66.34555,-22.11445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.34555,-22.11445,-62.78944,-22.12278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.76807,-22.10223,-65.59889,-22.1), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.59889,-22.1,-65.76807,-22.10223), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.9886,-22.09084,-65.59889,-22.1), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.97055,-22.06473,-64.9886,-22.09084), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.94111,-22.00084,-62.80917,-21.99639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.80917,-21.99639,-63.94111,-22.00084), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.08444,-21.96695,-66.27362,-21.95723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.27362,-21.95723,-68.08444,-21.96695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.92639,-21.93333,-66.04138,-21.91667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.04138,-21.91667,-65.92639,-21.93333), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.0775,-21.83195,-66.23167,-21.78751), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.23167,-21.78751,-66.0775,-21.83195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.10583,-21.7425,-66.23167,-21.78751), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.18861,-21.60639,-68.10583,-21.7425), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.18861,-21.29695,-62.25917,-21.05695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.25917,-21.05695,-68.50029,-20.93945), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.50029,-20.93945,-68.42111,-20.93917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.42111,-20.93917,-68.50029,-20.93945), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.55083,-20.90945,-68.42111,-20.93917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.5675,-20.74223,-68.46611,-20.64473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.46611,-20.64473,-68.4886,-20.60778), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.4886,-20.60778,-68.46611,-20.64473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.26945,-20.56223,-68.4886,-20.60778), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.68474,-20.50973,-62.26945,-20.56223), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.74945,-20.43306,-68.75751,-20.37611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.75751,-20.37611,-68.66833,-20.32778), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.66833,-20.32778,-68.75751,-20.37611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.7261,-20.23111,-58.15817,-20.16886), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.15817,-20.16886,-68.71028,-20.14917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.71028,-20.14917,-68.77472,-20.13167), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.77472,-20.13167,-68.71028,-20.14917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.78694,-20.10334,-61.91306,-20.08001), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.91306,-20.08001,-68.57445,-20.05751), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.57445,-20.05751,-68.65056,-20.05667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.65056,-20.05667,-68.57445,-20.05751), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.84778,-19.97889,-68.65056,-20.05667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.54111,-19.84473,-58.15166,-19.82806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.15166,-19.82806,-68.54111,-19.84473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.2325,-19.78251,-68.69695,-19.74056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.69695,-19.74056,-58.1217,-19.74031), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.1217,-19.74031,-68.69695,-19.74056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.68495,-19.70084,-58.1217,-19.74031), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.74277,-19.645,-68.68495,-19.70084), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.61362,-19.45917,-68.48083,-19.44445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.48083,-19.44445,-68.4375,-19.43723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.4375,-19.43723,-68.48083,-19.44445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.46222,-19.39889,-68.4375,-19.43723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-59.10194,-19.34806,-68.46222,-19.39889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-59.99084,-19.29667,-59.10194,-19.34806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.74083,-19.17778,-57.79389,-19.08112), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.79389,-19.08112,-57.70472,-19.04834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.70472,-19.04834,-57.79389,-19.08112), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.9525,-18.9675,-57.71666,-18.94695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.71666,-18.94695,-68.9525,-18.9675), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.77277,-18.90973,-68.93277,-18.88278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.93277,-18.88278,-57.77277,-18.90973), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.00084,-18.74306,-68.93277,-18.88278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.02724,-18.46639,-57.55194,-18.23889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.55194,-18.23889,-57.49111,-18.23861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.49111,-18.23861,-57.55194,-18.23889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.45861,-18.20472,-57.5042,-18.17773), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.5042,-18.17773,-57.45861,-18.20472), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.56555,-18.14139,-69.14806,-18.13778), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.14806,-18.13778,-57.56555,-18.14139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.58258,-18.10044,-69.07501,-18.07723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.07501,-18.07723,-57.58258,-18.10044), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.13972,-18.0275,-69.07861,-18.01806), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.07861,-18.01806,-69.13972,-18.0275), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.26556,-17.96361,-69.30333,-17.96334), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.30333,-17.96334,-69.26556,-17.96361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.33556,-17.78139,-57.77528,-17.63695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.77528,-17.63695,-69.49138,-17.62889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.49138,-17.62889,-57.77528,-17.63695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.73972,-17.59528,-57.75224,-17.58579), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.75224,-17.58579,-57.73972,-17.59528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.49973,-17.50528,-57.87389,-17.49344), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.87389,-17.49344,-69.49973,-17.50528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-57.94389,-17.4403,-57.87389,-17.49344), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.50111,-17.37889,-58.20917,-17.35472), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.20917,-17.35472,-69.50111,-17.37889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.59085,-17.29528,-69.6525,-17.28584), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.6525,-17.28584,-69.59085,-17.29528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.39806,-17.24834,-69.6525,-17.28584), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.61806,-17.18667,-58.39806,-17.24834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.38583,-17.05084,-69.41112,-17.02056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.41112,-17.02056,-69.38583,-17.05084), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.34361,-16.9825,-69.41112,-17.02056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.46445,-16.90028,-69.34361,-16.9825), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.1664,-16.72056,-69.09447,-16.70723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.09447,-16.70723,-69.1664,-16.72056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.0025,-16.64473,-58.46639,-16.63861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.46639,-16.63861,-69.0025,-16.64473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.03778,-16.60306,-69.03751,-16.57139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.03751,-16.57139,-69.02812,-16.54087), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.02812,-16.54087,-69.03751,-16.57139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.34722,-16.50556,-69.02812,-16.54087), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.35555,-16.44055,-68.99083,-16.41972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.99083,-16.41972,-58.35555,-16.44055), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.82611,-16.35139,-58.44055,-16.33001), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.44055,-16.33001,-58.75778,-16.31889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.75778,-16.31889,-58.44055,-16.33001), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.83363,-16.3025,-59.46083,-16.29139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-59.46083,-16.29139,-68.83363,-16.3025), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.3275,-16.27917,-58.38667,-16.27667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-58.38667,-16.27667,-58.3275,-16.27917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.87933,-16.27074,-58.38667,-16.27667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.16028,-16.26306,-68.87933,-16.27074), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.91916,-16.24305,-60.16028,-16.26306), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.15056,-16.21861,-69.10077,-16.21555), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.10077,-16.21555,-68.95879,-16.2155), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.95879,-16.2155,-69.10077,-16.21555), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.95879,-16.2155,-69.10077,-16.21555), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.97028,-16.2075,-68.95879,-16.2155), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.21472,-16.15639,-68.97028,-16.2075), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.42223,-15.61806,-69.36858,-15.51216), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.36858,-15.51216,-60.22722,-15.47861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.22722,-15.47861,-69.36858,-15.51216), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.32722,-15.43056,-69.27583,-15.40417), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.27583,-15.40417,-69.32722,-15.43056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.28555,-15.35722,-69.27583,-15.40417), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.14056,-15.25473,-69.13806,-15.22528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.13806,-15.22528,-69.14056,-15.25473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.27806,-15.11111,-60.57167,-15.0975), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.57167,-15.0975,-60.38389,-15.09278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.38389,-15.09278,-60.57167,-15.0975), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.38417,-14.95889,-60.38389,-15.09278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.43256,-14.81804,-69.36166,-14.79361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.36166,-14.79361,-60.43256,-14.81804), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.23972,-14.72528,-69.36166,-14.79361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.245,-14.61639,-60.35389,-14.60222), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.35389,-14.60222,-69.245,-14.61639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.15372,-14.57234,-69.22084,-14.57028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.22084,-14.57028,-69.15372,-14.57234), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.34639,-14.48333,-69.22084,-14.57028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.97917,-14.3675,-69.00696,-14.33361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.00696,-14.33361,-68.97917,-14.3675), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.44861,-14.29639,-69.00696,-14.33361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.98444,-14.23028,-68.85333,-14.19917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.85333,-14.19917,-68.98444,-14.23028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.48083,-14.15139,-68.86777,-14.11), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.86777,-14.11,-60.48083,-14.15139), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.38417,-13.98445,-68.97749,-13.96167), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.97749,-13.96167,-60.38417,-13.98445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.98222,-13.86167,-60.48277,-13.79972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.48277,-13.79972,-68.98222,-13.86167), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-60.79501,-13.67861,-69.08556,-13.67445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.08556,-13.67445,-60.79501,-13.67861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.08,-13.64361,-69.01167,-13.63417), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.01167,-13.63417,-69.08,-13.64361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.52972,-13.54834,-61.44944,-13.54639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.44944,-13.54639,-61.52972,-13.54834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.84167,-13.53861,-61.44944,-13.54639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.16055,-13.52695,-61.695,-13.52611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.695,-13.52611,-61.16055,-13.52695), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.04,-13.51528,-61.59389,-13.50723), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.59389,-13.50723,-61.04,-13.51528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.33833,-13.48778,-68.96417,-13.48333), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.96417,-13.48333,-61.33833,-13.48778), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.0675,-13.47361,-68.96417,-13.48333), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-61.87444,-13.45222,-61.0675,-13.47361), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.01722,-13.34861,-61.87444,-13.45222), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.12194,-13.14834,-62.39472,-13.14195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.39472,-13.14195,-62.12194,-13.14834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.46944,-13.07111,-62.58389,-13.05861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.58389,-13.05861,-62.46944,-13.07111), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.77917,-13.01084,-62.69361,-12.96611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.69361,-12.96611,-68.97472,-12.92639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.97472,-12.92639,-62.69361,-12.96611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-62.99805,-12.835,-68.88194,-12.75889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.88194,-12.75889,-68.80417,-12.73028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.80417,-12.73028,-63.24139,-12.70445), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.24139,-12.70445,-68.80417,-12.73028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.73138,-12.66972,-63.07056,-12.65278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.07056,-12.65278,-63.13667,-12.63611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.13667,-12.63611,-63.07056,-12.65278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.78111,-12.61778,-63.13667,-12.63611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.91833,-12.54473,-63.95834,-12.53278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.95834,-12.53278,-63.91833,-12.54473), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.6875,-12.51945,-63.95834,-12.53278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.67834,-12.46917,-63.81361,-12.465), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-63.81361,-12.465,-64.39417,-12.46167), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.39417,-12.46167,-63.81361,-12.465), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.49861,-12.36417,-68.77084,-12.30861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.77084,-12.30861,-64.46916,-12.2825), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.46916,-12.2825,-68.77084,-12.30861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.51973,-12.24195,-64.485,-12.23556), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.485,-12.23556,-64.51973,-12.24195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.68056,-12.16861,-64.73666,-12.15306), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.73666,-12.15306,-64.68056,-12.16861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.69138,-12.1,-64.73666,-12.15306), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.81194,-12.0275,-64.9875,-12.01056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.9875,-12.01056,-64.81194,-12.0275), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.01723,-11.97,-64.9875,-12.01056), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-64.99527,-11.91111,-65.01723,-11.97), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.16501,-11.7725,-65.20889,-11.71861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.20889,-11.71861,-65.12083,-11.69389), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.12083,-11.69389,-65.20889,-11.71861), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.1875,-11.65,-69.15028,-11.63222), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.15028,-11.63222,-65.1875,-11.65), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.23138,-11.51,-65.31555,-11.48889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.31555,-11.48889,-65.23138,-11.51), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.35196,-11.38056,-65.32556,-11.33195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.32556,-11.33195,-65.38503,-11.29333), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.38503,-11.29333,-65.32556,-11.33195), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.40028,-11.16417,-68.76445,-11.13972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.76445,-11.13972,-68.78444,-11.11972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.78444,-11.11972,-68.57918,-11.1046), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.57918,-11.1046,-68.78444,-11.11972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.86833,-11.01611,-68.7525,-11.00972), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.7525,-11.00972,-68.86833,-11.01611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.28944,-10.98556,-65.29944,-10.97278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.29944,-10.97278,-69.06973,-10.96639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.06973,-10.96639,-65.29944,-10.97278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-69.56744,-10.95047,-69.06973,-10.96639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.32806,-10.85111,-65.40944,-10.79889), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.40944,-10.79889,-65.32806,-10.85111), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.10194,-10.70528,-67.70348,-10.6943), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.70348,-10.6943,-68.10194,-10.70528), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.82224,-10.66333,-68.01501,-10.65917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-68.01501,-10.65917,-67.82224,-10.66333), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.67426,-10.60999,-68.01501,-10.65917), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.44388,-10.51472,-65.40417,-10.44584), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.40417,-10.44584,-65.39606,-10.3953), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.39606,-10.3953,-65.40417,-10.44584), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.30029,-10.31667,-67.15804,-10.31317), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-67.15804,-10.31317,-67.30029,-10.31667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.31361,-10.29389,-67.15804,-10.31317), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.28889,-10.19556,-65.31361,-10.29389), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.33362,-10.02611,-66.77917,-10.00667), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.77917,-10.00667,-65.33362,-10.02611), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.55943,-9.89167,-65.30139,-9.83722), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.30139,-9.83722,-65.57001,-9.83639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.57001,-9.83639,-65.30139,-9.83722), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.61305,-9.83306,-65.57001,-9.83639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.02917,-9.80695,-66.05917,-9.78834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-66.05917,-9.78834,-65.79861,-9.7825), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.79861,-9.7825,-66.05917,-9.78834), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.75917,-9.77,-65.79861,-9.7825), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.82364,-9.75639,-65.70612,-9.75028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.70612,-9.75028,-65.82364,-9.75639), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.78278,-9.73195,-65.70612,-9.75028), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.38194,-9.69778,-65.43832,-9.68278), mapfile, tile_dir, 0, 11, "bo-bolivia")
	render_tiles((-65.43832,-9.68278,-65.38194,-9.69778), mapfile, tile_dir, 0, 11, "bo-bolivia")