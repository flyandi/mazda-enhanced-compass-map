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
    # Region: CO
    # Region Name: Colombia

	render_tiles((-69.94658,-4.22423,-70.51611,-3.86111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.51611,-3.86111,-70.62077,-3.8259), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.24889,-3.86111,-70.62077,-3.8259), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.62077,-3.8259,-70.51611,-3.86111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.37193,-3.78833,-70.72444,-3.77972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.72444,-3.77972,-70.37193,-3.78833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.8161,-3.54944,-70.72444,-3.77972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.69583,-2.87695,-70.04973,-2.7275), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.04973,-2.7275,-70.12555,-2.69945), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.12555,-2.69945,-70.04973,-2.7275), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.07167,-2.66111,-70.12555,-2.69945), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.32695,-2.57833,-70.33833,-2.55556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.33833,-2.55556,-70.23694,-2.55222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.23694,-2.55222,-70.33833,-2.55556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.29999,-2.53167,-70.23694,-2.55222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.42055,-2.51111,-72.20889,-2.50639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.20889,-2.50639,-72.89195,-2.50306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.89195,-2.50306,-72.20889,-2.50639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.3075,-2.49555,-70.54083,-2.49472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.54083,-2.49472,-72.64612,-2.49417), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.64612,-2.49417,-70.54083,-2.49472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.80583,-2.48778,-72.64612,-2.49417), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.58778,-2.48111,-72.33667,-2.47972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.33667,-2.47972,-70.58778,-2.48111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.10388,-2.47639,-72.71666,-2.475), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.71666,-2.475,-72.10388,-2.47639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.20889,-2.45833,-72.71666,-2.475), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.69249,-2.44056,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.12999,-2.44056,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.35445,-2.43444,-70.55222,-2.4325), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.74472,-2.43444,-70.55222,-2.4325), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.55222,-2.4325,-72.35445,-2.43444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.52722,-2.42639,-73.02278,-2.42222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.02278,-2.42222,-70.59973,-2.42083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.59973,-2.42083,-73.02278,-2.42222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.59584,-2.40778,-72.94444,-2.39556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.94444,-2.39556,-71.35556,-2.39139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.35556,-2.39139,-72.04805,-2.38722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.04805,-2.38722,-71.89862,-2.38444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.89862,-2.38444,-72.04805,-2.38722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.03612,-2.36528,-73.08307,-2.36333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.08307,-2.36333,-73.03612,-2.36528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.00862,-2.35722,-71.11389,-2.35306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.11389,-2.35306,-71.46056,-2.3525), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.35777,-2.35306,-71.46056,-2.3525), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.46056,-2.3525,-71.11389,-2.35306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.09389,-2.30306,-70.98833,-2.30028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.98833,-2.30028,-71.09389,-2.30306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.75862,-2.29611,-70.98833,-2.30028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.43028,-2.27695,-70.75862,-2.29611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.1339,-2.24333,-71.51501,-2.23583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.51501,-2.23583,-73.1339,-2.24333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.08833,-2.22445,-70.97139,-2.22361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.97139,-2.22361,-73.08833,-2.22445), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.67944,-2.22167,-70.97139,-2.22361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.57611,-2.20833,-71.67944,-2.22167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.73138,-2.1675,-71.6886,-2.14972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.6886,-2.14972,-73.0575,-2.14083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.0575,-2.14083,-71.6886,-2.14972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.09334,-1.91806,-73.30943,-1.87222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.30943,-1.87222,-73.09334,-1.91806), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.19916,-1.80306,-73.50751,-1.74833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.50751,-1.74833,-73.19916,-1.80306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.44638,-1.59639,-69.45528,-1.53778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.45528,-1.53778,-73.51723,-1.50389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.51723,-1.50389,-69.45528,-1.53778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.4375,-1.42917,-73.55638,-1.37083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.55638,-1.37083,-69.3839,-1.36667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.3839,-1.36667,-73.55638,-1.37083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.75557,-1.29417,-73.84416,-1.26056), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.84416,-1.26056,-73.73889,-1.23861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.73889,-1.23861,-73.84416,-1.26056), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.38083,-1.17445,-73.85028,-1.17083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.85028,-1.17083,-73.93832,-1.16889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.93832,-1.16889,-73.85028,-1.17083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.07779,-1.07361,-74.02055,-1.03528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.02055,-1.03528,-69.44223,-1.02417), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.44223,-1.02417,-74.23694,-1.01889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.23694,-1.01889,-69.44223,-1.02417), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.46834,-0.96611,-74.23694,-1.01889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.29361,-0.90222,-74.24249,-0.89), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.24249,-0.89,-74.29361,-0.90222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.23889,-0.85917,-69.56555,-0.845), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.56555,-0.845,-74.23889,-0.85917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.30916,-0.81333,-69.56555,-0.845), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.61806,-0.73389,-74.30916,-0.81333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.57306,-0.63667,-74.3761,-0.56806), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.3761,-0.56806,-69.62027,-0.50444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.62027,-0.50444,-74.3761,-0.56806), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.63834,-0.41028,-74.60777,-0.38361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.60777,-0.38361,-74.70667,-0.36778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.70667,-0.36778,-74.60777,-0.38361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.66389,-0.34667,-74.70667,-0.36778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.92833,-0.30944,-74.66389,-0.34667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.73083,-0.26639,-74.8761,-0.22722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.8761,-0.22722,-74.78667,-0.20056), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.78667,-0.20056,-74.8761,-0.22722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.05804,-0.1575,-75.245,-0.11972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.245,-0.11972,-75.28525,-0.11912), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.28525,-0.11912,-75.245,-0.11972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.43443,-0.06,-75.15083,-0.03889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.15083,-0.03889,-75.21222,-0.0375), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.21222,-0.0375,-75.15083,-0.03889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.62805,0.02889,-75.59778,0.05583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.59778,0.05583,-75.77528,0.05639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.77528,0.05639,-75.59778,0.05583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.73306,0.2325,-76.88028,0.24222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.88028,0.24222,-76.40889,0.24833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.40889,0.24833,-76.88028,0.24222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.72139,0.28056,-76.40889,0.24833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.10611,0.32167,-77.19666,0.33472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.19666,0.33472,-76.04778,0.34), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.04778,0.34,-77.19666,0.33472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.08084,0.35917,-76.33168,0.37361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.33168,0.37361,-76.11583,0.37917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.11583,0.37917,-77.37971,0.38472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.37971,0.38472,-76.11583,0.37917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.40834,0.39694,-77.37971,0.38472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.30305,0.41889,-77.42722,0.42444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.42722,0.42444,-76.30305,0.41889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.04639,0.50444,-69.94055,0.58167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.94055,0.58167,-70.04416,0.59083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.04416,0.59083,-69.94055,0.58167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.20935,0.61828,-77.44444,0.62722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.44444,0.62722,-69.13751,0.63), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.13751,0.63,-77.44444,0.62722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.61139,0.64556,-69.12582,0.65111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.12582,0.65111,-69.31889,0.65306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.31889,0.65306,-69.12582,0.65111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.54167,0.65611,-69.31889,0.65306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.47639,0.66361,-77.54167,0.65611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.67833,0.67889,-77.47639,0.66361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.51112,0.72639,-69.1825,0.73194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.1825,0.73194,-69.51112,0.72639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.46445,0.74028,-77.65834,0.745), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.65834,0.745,-69.46445,0.74028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.8094,0.80549,-77.65594,0.81525), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.65594,0.81525,-77.8094,0.80549), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.89,0.82972,-77.65594,0.81525), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.71472,0.84583,-77.89,0.82972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.14223,0.87056,-77.91637,0.88166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.91637,0.88166,-69.14223,0.87056), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.20279,0.90667,-77.91637,0.88166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.19333,0.95194,-69.20279,0.90667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.4261,1.02722,-69.27556,1.04111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.27556,1.04111,-69.4261,1.02722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.7103,1.06028,-69.44666,1.06222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.44666,1.06222,-69.7103,1.06028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.84222,1.06222,-69.7103,1.06028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.35194,1.06722,-69.44666,1.06222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.72084,1.08972,-78.35194,1.06722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.07556,1.1725,-67.08418,1.19389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.08418,1.19389,-78.47333,1.19778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.47333,1.19778,-78.56027,1.19861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.56027,1.19861,-78.47333,1.19778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-66.87338,1.22554,-78.56027,1.19861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.63417,1.26444,-78.59593,1.26929), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.59593,1.26929,-78.63417,1.26444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.80987,1.43793,-78.81161,1.43964), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.81161,1.43964,-78.80987,1.43793), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.86168,1.555,-79.035,1.6), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-79.035,1.6,-67.07251,1.62528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.07251,1.62528,-79.05168,1.63389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-79.05168,1.63389,-67.07251,1.62528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-66.99168,1.69583,-69.8432,1.71534), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.8432,1.71534,-68.15334,1.72417), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.15334,1.72417,-69.37778,1.72863), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.37778,1.72863,-69.73889,1.73167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.73889,1.73167,-69.37778,1.72863), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.95195,1.7425,-69.73889,1.73167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.96918,1.75639,-69.45917,1.76028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.45917,1.76028,-78.96918,1.75639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.62112,1.76472,-78.58417,1.76889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.58417,1.76889,-78.62112,1.76472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.01529,1.77389,-78.58417,1.76889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.78333,1.80694,-78.63112,1.80778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.63112,1.80778,-67.78333,1.80694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.27583,1.83222,-78.85139,1.83667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.85139,1.83667,-68.27583,1.83222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.19196,1.84944,-78.85139,1.83667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.5475,1.92222,-68.08972,1.93361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.08972,1.93361,-78.5475,1.92222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.59584,1.94694,-68.08972,1.93361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.1339,1.98778,-78.66057,1.99194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.66057,1.99194,-67.1339,1.98778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.59001,2.01028,-68.19499,2.01444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.19499,2.01444,-78.59001,2.01028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.6239,2.03028,-68.19499,2.01444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.11194,2.09944,-67.51723,2.1), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.51723,2.1,-67.11194,2.09944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.3353,2.11111,-67.51723,2.1), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.42444,2.14389,-67.1725,2.14806), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.1725,2.14806,-67.42444,2.14389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.68723,2.2,-67.1725,2.14806), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.21695,2.27528,-67.17418,2.33694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.17418,2.33694,-67.19249,2.3925), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.19249,2.3925,-78.53889,2.43), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.53889,2.43,-78.47473,2.43444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.47473,2.43444,-78.3539,2.43861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.3539,2.43861,-67.28139,2.43889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.28139,2.43889,-78.3539,2.43861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.12584,2.48694,-78.46001,2.50139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.46001,2.50139,-78.42223,2.5075), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.42223,2.5075,-78.46001,2.50139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.99306,2.52139,-78.42223,2.5075), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.21278,2.53611,-78.06029,2.55028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-78.06029,2.55028,-77.86195,2.55889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.86195,2.55889,-77.94862,2.55944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.94862,2.55944,-77.86195,2.55889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.78307,2.57139,-77.94862,2.55944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.90834,2.58778,-77.97696,2.59028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.97696,2.59028,-77.90834,2.58778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.74084,2.60472,-77.97696,2.59028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.89389,2.63028,-77.74084,2.60472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.4939,2.66639,-67.56778,2.68333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.56778,2.68333,-67.4939,2.66639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.81306,2.74833,-67.84634,2.79245), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.84634,2.79245,-67.60722,2.79556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.60722,2.79556,-67.84634,2.79245), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.77695,2.80833,-67.60722,2.79556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.75696,2.83889,-77.6389,2.84583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.6389,2.84583,-67.75696,2.83889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.70723,2.86,-67.84335,2.86833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.84335,2.86833,-77.64362,2.87528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.64362,2.87528,-67.84335,2.86833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.64473,2.89111,-77.64362,2.87528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.70557,2.91806,-77.64473,2.89111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.62001,2.98111,-77.69139,2.99333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.69139,2.99333,-77.62001,2.98111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.69724,3.0475,-77.55917,3.05333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.55917,3.05333,-77.69724,3.0475), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.51501,3.12639,-77.55917,3.05333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.50557,3.21667,-67.44666,3.24194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.44666,3.24194,-77.4189,3.25861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.4189,3.25861,-67.38417,3.25917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.38417,3.25917,-77.4189,3.25861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.40695,3.30306,-77.47195,3.32889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.47195,3.32889,-77.35167,3.335), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.35167,3.335,-77.47195,3.32889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.4425,3.36361,-77.35167,3.335), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.29083,3.3975,-77.4425,3.36361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.30388,3.45194,-77.2639,3.47), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.2639,3.47,-67.37444,3.47889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.37444,3.47889,-77.2639,3.47), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.31584,3.49917,-67.37444,3.47889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.27084,3.52694,-77.31584,3.49917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.28168,3.56889,-77.27084,3.52694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.20222,3.61361,-77.28168,3.56889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.14612,3.66361,-77.19196,3.70028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.19196,3.70028,-77.13,3.71639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.13,3.71639,-67.48891,3.72278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.48891,3.72278,-77.13,3.71639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.59584,3.73778,-77.1964,3.75194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.1964,3.75194,-67.59584,3.73778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.12556,3.76639,-77.1964,3.75194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.1564,3.80306,-77.12611,3.81972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.12611,3.81972,-77.1564,3.80306), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.25111,3.83639,-77.12611,3.81972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.2914,3.86083,-77.25111,3.83639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.11168,3.89639,-77.0289,3.91778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.0289,3.91778,-77.12279,3.92333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.12279,3.92333,-77.0289,3.91778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.34807,3.92889,-77.30945,3.93278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.30945,3.93278,-77.37418,3.93472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.37418,3.93472,-77.30945,3.93278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.69057,3.95861,-77.33556,3.96444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.33556,3.96444,-67.69057,3.95861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.29333,3.97722,-77.24112,3.98389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.24112,3.98389,-77.29333,3.97722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.18723,4.06028,-77.28334,4.06917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.28334,4.06917,-77.18723,4.06028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.72917,4.0875,-77.28334,4.06917), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.43556,4.12222,-67.72917,4.0875), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.43001,4.17416,-77.31361,4.19056), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.31361,4.19056,-77.43001,4.17416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.3539,4.22166,-67.80556,4.2275), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.80556,4.2275,-77.3539,4.22166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.23946,4.25444,-67.80556,4.2275), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.3775,4.32805,-77.23946,4.25444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.34584,4.44528,-67.81305,4.46528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.81305,4.46528,-77.34584,4.44528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.87527,4.52528,-67.81305,4.46528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.31696,4.64805,-77.29167,4.66722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.29167,4.66722,-77.31696,4.64805), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.25667,4.70333,-77.29167,4.66722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.36362,4.99416,-67.79306,5.05), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.79306,5.05,-77.36362,4.99416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.82889,5.11222,-67.79306,5.05), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.84973,5.30778,-77.38223,5.37167), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.38223,5.37167,-67.73167,5.43055), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.73167,5.43055,-77.45001,5.48555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.45001,5.48555,-77.5475,5.48889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.5475,5.48889,-77.45001,5.48555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.485,5.50556,-77.5475,5.48889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.61333,5.53917,-77.485,5.50556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.50667,5.58278,-77.33223,5.61305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.33223,5.61305,-77.41806,5.62472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.41806,5.62472,-77.33223,5.61305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.65167,5.67,-77.25168,5.70667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.25168,5.70667,-67.65167,5.67), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.24362,5.78278,-67.62222,5.7875), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.62222,5.7875,-77.24362,5.78278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.41444,5.98722,-69.25696,6.08389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.25696,6.08389,-69.19499,6.1), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.19499,6.1,-69.25696,6.08389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.49194,6.11861,-68.64417,6.13416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.42972,6.11861,-68.64417,6.13416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.64417,6.13416,-67.49194,6.11861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.33139,6.15305,-77.47528,6.15778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.47528,6.15778,-69.33139,6.15305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.31361,6.16694,-77.47528,6.15778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.45418,6.19055,-67.45479,6.19311), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.45479,6.19311,-68.45418,6.19055), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.97112,6.19861,-69.09416,6.19916), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-69.09416,6.19916,-68.97112,6.19861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.00111,6.20667,-69.09416,6.19916), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-68.15222,6.22333,-77.41028,6.235), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.41028,6.235,-68.15222,6.22333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.57362,6.26611,-77.48029,6.2925), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.48029,6.2925,-67.83556,6.30833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-67.83556,6.30833,-77.48029,6.2925), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.37445,6.34,-67.83556,6.30833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.40668,6.38889,-77.35583,6.39139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.35583,6.39139,-77.40668,6.38889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.3575,6.49993,-77.33974,6.565), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.33974,6.565,-77.3575,6.49993), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.53279,6.66278,-77.49057,6.70889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.49057,6.70889,-77.53279,6.66278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.57529,6.80611,-77.69196,6.84555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.69196,6.84555,-77.66446,6.87556), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.66446,6.87556,-77.69196,6.84555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.69362,6.93889,-70.3075,6.93972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.3075,6.93972,-77.69362,6.93889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.65501,6.9625,-70.11916,6.97583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.11916,6.97583,-71.88083,6.98527), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.88083,6.98527,-70.11916,6.97583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.13222,6.99528,-70.96306,7.00278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.96306,7.00278,-71.13222,6.99528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.51306,7.01528,-70.96306,7.00278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.55305,7.04472,-77.67834,7.05222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.67834,7.05222,-71.55305,7.04472), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.06555,7.06167,-77.67834,7.05222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.82417,7.085,-70.56946,7.09028), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-70.56946,7.09028,-70.82417,7.085), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.81946,7.1575,-72.14555,7.19861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.14555,7.19861,-77.88913,7.22772), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.88913,7.22772,-72.14555,7.19861), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.18332,7.38277,-72.39917,7.40611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.39917,7.40611,-72.18332,7.38277), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.81111,7.48,-77.745,7.4875), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.745,7.4875,-72.47166,7.49194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.47166,7.49194,-77.745,7.4875), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.5739,7.52528,-77.72055,7.54805), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.72055,7.54805,-77.60638,7.54944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.60638,7.54944,-77.72055,7.54805), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.4386,7.62278,-77.75862,7.62555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.75862,7.62555,-77.4386,7.62278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.66222,7.67777,-77.755,7.71), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.755,7.71,-77.32501,7.72083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.32501,7.72083,-77.73528,7.72416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.73528,7.72416,-77.32501,7.72083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.37193,7.78611,-77.73528,7.72416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.82973,7.90639,-77.29195,7.90778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.29195,7.90778,-76.82973,7.90639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.75945,7.91639,-77.29195,7.90778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.92639,7.94305,-77.14667,7.94583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.14667,7.94583,-76.92639,7.94305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.42027,7.99,-76.73473,8.02083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.73473,8.02083,-76.91862,8.025), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.91862,8.025,-76.73473,8.02083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.87001,8.03194,-76.91862,8.025), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.4025,8.04194,-72.33362,8.04944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.33362,8.04944,-72.4025,8.04194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.22166,8.10222,-76.90417,8.10833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.90417,8.10833,-77.22166,8.10222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.94528,8.12583,-76.83862,8.13389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.83862,8.13389,-76.94528,8.12583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.33556,8.14639,-76.83862,8.13389), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.94695,8.16083,-72.33556,8.14639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.76723,8.2425,-76.98334,8.25222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.98334,8.25222,-76.76723,8.2425), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.36389,8.28694,-76.98334,8.25222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.38417,8.36305,-77.10973,8.36361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.10973,8.36361,-72.38417,8.36305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.37389,8.39639,-77.10973,8.36361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.42027,8.47055,-77.2489,8.47111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.2489,8.47111,-77.42027,8.47055), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.47861,8.47972,-76.8239,8.485), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.8239,8.485,-77.47861,8.47972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.94528,8.53416,-76.8239,8.485), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.89445,8.61722,-72.66444,8.64111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.66444,8.64111,-76.89445,8.61722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-77.36613,8.67617,-76.64944,8.70778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.64944,8.70778,-77.36613,8.67617), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.6425,8.74333,-76.64944,8.70778), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.43445,8.87139,-76.43195,8.90555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.43195,8.90555,-76.43445,8.87139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.2789,8.98166,-76.25917,9.00166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.25917,9.00166,-76.2789,8.98166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.26334,9.05305,-72.92694,9.09833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.92694,9.09833,-72.77223,9.11305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.77223,9.11305,-76.19446,9.11583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.19446,9.11583,-72.77223,9.11305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.84528,9.14027,-72.97362,9.1425), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.97362,9.1425,-72.84528,9.14027), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.37193,9.16694,-73.31841,9.17372), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.31841,9.17372,-73.37193,9.16694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.3839,9.18972,-73.31841,9.17372), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.17223,9.23861,-72.98111,9.26083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.98111,9.26083,-76.1089,9.27222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.1089,9.27222,-72.98111,9.26083), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.0014,9.3025,-76.11389,9.31139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-76.11389,9.31139,-73.0014,9.3025), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.66251,9.42444,-75.93224,9.43), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.93224,9.43,-75.66251,9.42444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.62001,9.47222,-75.93224,9.43), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.5864,9.57694,-73.09584,9.58166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.09584,9.58166,-75.5864,9.57694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.62195,9.69333,-75.71945,9.70111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.71945,9.70111,-75.62195,9.69333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.64195,9.80333,-72.96251,9.83694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.96251,9.83694,-75.64195,9.80333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.97888,9.98888,-72.96251,9.83694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.65028,10.19444,-75.53168,10.23694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.53168,10.23694,-75.55945,10.24139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.55945,10.24139,-75.53168,10.23694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.51334,10.30555,-75.55945,10.24139), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.54779,10.41222,-75.48779,10.48222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.48779,10.48222,-72.84723,10.52305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.84723,10.52305,-75.48779,10.48222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.52585,10.56666,-72.84723,10.52305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.25696,10.70555,-75.22723,10.72305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.22723,10.72305,-74.455,10.73833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.455,10.73833,-75.27724,10.74166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.27724,10.74166,-74.39056,10.74305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.39056,10.74305,-75.27724,10.74166), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.54112,10.75805,-74.49695,10.765), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.49695,10.765,-74.54112,10.75805), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-75.27612,10.78416,-74.60667,10.78833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.60667,10.78833,-75.27612,10.78416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.54529,10.80555,-74.4725,10.82222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.4725,10.82222,-74.54529,10.80555), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.50528,10.83916,-74.4725,10.82222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.53111,10.87194,-74.58751,10.88694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.58751,10.88694,-74.53111,10.87194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.51779,10.90472,-74.58751,10.88694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.29224,10.93333,-72.58444,10.93722), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.58444,10.93722,-74.29224,10.93333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.40918,10.96639,-74.49306,10.97416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.49306,10.97416,-74.40918,10.96639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.34167,10.98666,-74.49306,10.97416), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.96722,11.01111,-74.34167,10.98666), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.49306,11.12111,-74.86446,11.12305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.86446,11.12305,-72.49306,11.12111), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.26501,11.1525,-72.34138,11.165), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.34138,11.165,-72.26501,11.1525), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.22945,11.21083,-72.34138,11.165), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.67889,11.26611,-73.34529,11.27944), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.34529,11.27944,-73.67889,11.26611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.1889,11.30972,-73.22696,11.33444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.22696,11.33444,-74.03297,11.35318), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-74.03297,11.35318,-73.22696,11.33444), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-73.035,11.50278,-74.03297,11.35318), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.97722,11.665,-72.60556,11.75222), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.60556,11.75222,-71.40361,11.81278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.40361,11.81278,-72.36057,11.83194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.36057,11.83194,-71.40361,11.81278), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.3248,11.85264,-72.36057,11.83194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.3248,11.85264,-72.36057,11.83194), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.24028,11.90528,-71.3248,11.85264), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.11806,12.03861,-71.11389,12.09611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.11389,12.09611,-72.13972,12.10361), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.13972,12.10361,-71.11389,12.09611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.99306,12.15778,-71.91112,12.19583), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.91112,12.19583,-71.86584,12.20667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.86584,12.20667,-71.99251,12.21055), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.99251,12.21055,-71.86584,12.20667), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.17445,12.22278,-71.99251,12.21055), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-72.14418,12.24833,-71.9689,12.26), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.9689,12.26,-72.14418,12.24833), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.96028,12.27694,-71.89001,12.27972), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.89001,12.27972,-71.96028,12.27694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.22667,12.30694,-71.80972,12.31333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.80972,12.31333,-71.22667,12.30694), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.83945,12.33305,-71.80972,12.31333), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.82861,12.35889,-71.83945,12.33305), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.68333,12.39639,-71.73889,12.41528), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.73889,12.41528,-71.68333,12.39639), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.65501,12.43889,-71.5289,12.44611), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.5289,12.44611,-71.65501,12.43889), mapfile, tile_dir, 0, 11, "co-colombia")
	render_tiles((-71.69334,12.45833,-71.5289,12.44611), mapfile, tile_dir, 0, 11, "co-colombia")