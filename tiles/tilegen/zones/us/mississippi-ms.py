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
    # Zone: us
    # Region: Mississippi
    # Region Name: MS

	render_tiles((-89.5245,30.18075,-89.47582,30.19156), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.47582,30.19156,-89.5245,30.18075), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.44747,30.2051,-89.60766,30.2171), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.60766,30.2171,-89.44747,30.2051), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.42462,30.24539,-89.60766,30.2171), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.34475,30.2932,-89.30702,30.30399), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.30702,30.30399,-89.29444,30.3076), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.29444,30.3076,-89.63421,30.30826), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.63421,30.30826,-89.29444,30.3076), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.47188,30.32002,-88.58193,30.33106), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.58193,30.33106,-89.18684,30.3312), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.18684,30.3312,-88.58193,30.33106), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.52249,30.34009,-88.40993,30.34212), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.40993,30.34212,-88.70059,30.34369), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.70059,30.34369,-88.40993,30.34212), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.74695,30.34762,-88.4465,30.34775), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.4465,30.34775,-88.74695,30.34762), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.61301,30.35396,-88.80034,30.35726), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.80034,30.35726,-88.61301,30.35396), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.81876,30.36059,-88.66382,30.3621), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.66382,30.3621,-88.81876,30.36059), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.08324,30.3681,-88.39502,30.36943), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.39502,30.36943,-89.08324,30.3681), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.97123,30.3908,-88.89393,30.3934), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.89393,30.3934,-88.97123,30.3908), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.67851,30.41401,-88.89393,30.3934), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.69993,30.45404,-89.71249,30.47751), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.71249,30.47751,-89.69993,30.45404), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.40393,30.54336,-89.79166,30.55152), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.79166,30.55152,-88.40393,30.54336), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.82187,30.64402,-89.82618,30.66882), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.82618,30.66882,-89.82187,30.64402), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.83633,30.7272,-88.41227,30.73177), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.41227,30.73177,-88.41247,30.7356), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.41247,30.7356,-88.41227,30.73177), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.79175,30.82039,-88.41247,30.7356), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.75007,30.91293,-88.42602,30.99828), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.42602,30.99828,-91.22407,30.99918), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.22407,30.99918,-91.17614,30.99922), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.17614,30.99922,-91.22407,30.99918), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.06013,30.99932,-91.17614,30.99922), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.63694,30.99942,-91.06013,30.99932), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.82583,30.99953,-90.75878,30.99958), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.75878,30.99958,-90.82583,30.99953), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.5672,30.99995,-90.54757,30.99998), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.54757,30.99998,-90.5672,30.99995), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.34724,31.00036,-90.25955,31.00066), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.34601,31.00036,-90.25955,31.00066), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.25955,31.00066,-90.34724,31.00036), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.89752,31.00191,-89.83591,31.0021), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.83591,31.0021,-89.89752,31.00191), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.72818,31.00231,-89.72815,31.00243), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.72815,31.00243,-89.72818,31.00231), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.62826,31.0051,-89.72815,31.00243), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.56037,31.04951,-91.59469,31.09144), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.59469,31.09144,-88.43201,31.1143), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.43201,31.1143,-91.62167,31.13687), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.62167,31.13687,-88.43201,31.1143), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.59099,31.192,-91.59005,31.19369), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.59005,31.19369,-91.59099,31.192), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.64436,31.23441,-91.56419,31.26163), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.56419,31.26163,-91.62136,31.26781), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.62136,31.26781,-91.56419,31.26163), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.62136,31.26781,-91.56419,31.26163), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.50886,31.29164,-91.62136,31.26781), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.53606,31.33836,-91.50886,31.29164), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.53234,31.39028,-88.44866,31.42128), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.44866,31.42128,-88.44945,31.43584), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.44945,31.43584,-91.51036,31.43893), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.51036,31.43893,-88.44945,31.43584), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.51714,31.49839,-91.48962,31.53427), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.48962,31.53427,-91.43762,31.54617), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.43762,31.54617,-91.48962,31.53427), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.45752,31.58757,-91.46382,31.62037), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.46382,31.62037,-88.45948,31.62165), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.45948,31.62165,-91.46382,31.62037), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.39572,31.64417,-88.45948,31.62165), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.46363,31.69794,-91.38092,31.73246), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.38092,31.73246,-91.38012,31.73263), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.38012,31.73263,-91.38092,31.73246), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.31858,31.74532,-91.32046,31.7478), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.32046,31.7478,-91.31858,31.74532), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.46867,31.79072,-91.35951,31.79936), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.35951,31.79936,-88.46867,31.79072), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.29014,31.83366,-91.34571,31.84286), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.34571,31.84286,-91.29014,31.83366), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.24402,31.86973,-91.2349,31.87686), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.2349,31.87686,-91.24402,31.86973), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.46866,31.89386,-91.2349,31.87686), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.18111,31.92006,-88.46866,31.93317), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.46866,31.93317,-91.18111,31.92006), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.17741,31.97326,-91.11741,31.98706), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.11741,31.98706,-91.17741,31.97326), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.08081,32.02346,-91.07911,32.05026), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.07911,32.05026,-88.45339,32.05305), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.45339,32.05305,-91.07911,32.05026), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.03471,32.10105,-91.03947,32.10797), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.03947,32.10797,-91.03471,32.10105), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.10851,32.20815,-90.99123,32.21466), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.99123,32.21466,-91.10851,32.20815), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.43115,32.22764,-90.99123,32.21466), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.42828,32.25014,-88.43115,32.22764), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.94783,32.28349,-88.42131,32.30868), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.42131,32.30868,-90.94783,32.28349), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.92117,32.34207,-90.98667,32.35176), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.98667,32.35176,-90.92117,32.34207), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.96599,32.42481,-91.05291,32.43844), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.05291,32.43844,-90.96599,32.42481), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.06052,32.51236,-91.01128,32.5166), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.01128,32.5166,-91.06052,32.51236), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.04876,32.5728,-91.04931,32.57362), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.04931,32.57362,-91.04876,32.5728), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.38925,32.57812,-91.05529,32.57898), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.05529,32.57898,-88.38925,32.57812), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.07951,32.60068,-91.05529,32.57898), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.09876,32.68529,-88.37334,32.71183), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.37334,32.71183,-91.057,32.72558), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.057,32.72558,-88.37334,32.71183), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.11365,32.73997,-91.057,32.72558), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.15761,32.77603,-91.11365,32.73997), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.16167,32.81247,-91.15761,32.77603), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.13789,32.84898,-91.16167,32.81247), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.0706,32.88866,-91.13789,32.84898), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.34749,32.92903,-91.07208,32.93783), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.07208,32.93783,-88.34749,32.92903), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.13441,32.98053,-88.34008,32.99126), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.34008,32.99126,-91.13441,32.98053), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.16607,33.00411,-91.15961,33.01124), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.15961,33.01124,-91.16607,33.00411), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.12038,33.05453,-91.15961,33.01124), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.18084,33.09836,-91.10432,33.1316), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.10432,33.1316,-91.15302,33.13509), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.15302,33.13509,-91.10432,33.1316), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.08437,33.18086,-88.31714,33.18412), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.31714,33.18412,-91.08437,33.18086), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.06871,33.23294,-91.08614,33.27365), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.08614,33.27365,-91.12554,33.28026), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.12554,33.28026,-91.08614,33.27365), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.30444,33.28832,-91.12554,33.28026), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.14222,33.34899,-91.11376,33.39312), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.11376,33.39312,-91.14766,33.42717), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.14766,33.42717,-91.11376,33.39312), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.1718,33.46234,-91.18938,33.49301), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.18938,33.49301,-91.1718,33.46234), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.21567,33.52942,-88.27452,33.534), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.27452,33.534,-91.21567,33.52942), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.20564,33.54698,-88.27452,33.534), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.18894,33.57623,-91.20564,33.54698), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.1309,33.61092,-91.18894,33.57623), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.17831,33.65111,-91.10098,33.66055), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.10098,33.66055,-91.17831,33.65111), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.25445,33.69878,-91.07539,33.7144), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.07539,33.7144,-88.25445,33.69878), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.24839,33.74491,-91.14329,33.74714), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.14329,33.74714,-88.24839,33.74491), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.02678,33.76364,-91.11149,33.77457), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.11149,33.77457,-91.08551,33.77641), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.08551,33.77641,-91.11149,33.77457), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.02517,33.80595,-91.05282,33.82418), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.05282,33.82418,-91.02517,33.80595), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.06125,33.87751,-91.02638,33.90798), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.02638,33.90798,-91.06125,33.87751), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.03596,33.94376,-91.0887,33.96133), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.0887,33.96133,-91.00498,33.97701), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.00498,33.97701,-91.04837,33.98508), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-91.04837,33.98508,-91.00498,33.97701), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.97995,34.00011,-91.04837,33.98508), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.94266,34.01805,-90.89242,34.02686), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.89242,34.02686,-90.94266,34.01805), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.20723,34.05833,-90.87454,34.07204), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.87454,34.07204,-88.20723,34.05833), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.20358,34.08653,-90.90113,34.09467), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.90113,34.09467,-88.20358,34.08653), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.94632,34.10937,-90.9448,34.11666), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.9448,34.11666,-90.94408,34.12007), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.94408,34.12007,-90.9448,34.11666), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.93806,34.14875,-90.89439,34.16095), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.89439,34.16095,-90.93806,34.14875), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.8827,34.18436,-90.89439,34.16095), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.89456,34.22438,-90.83998,34.23611), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.83998,34.23611,-90.89456,34.22438), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.81283,34.27944,-90.75268,34.28927), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.75268,34.28927,-90.81283,34.27944), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.17326,34.32104,-90.6604,34.33576), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.6604,34.33576,-90.76517,34.34282), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.76517,34.34282,-90.6604,34.33576), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.72913,34.36421,-90.6414,34.38387), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.6414,34.38387,-90.72913,34.36421), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.57534,34.41515,-90.6414,34.38387), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.58372,34.45883,-88.1549,34.46303), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.1549,34.46303,-90.58372,34.45883), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.56935,34.52487,-90.54924,34.5681), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.54924,34.5681,-88.13956,34.5817), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.13956,34.5817,-90.54924,34.5681), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.58722,34.61573,-88.13426,34.62266), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.13426,34.62266,-90.58722,34.61573), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.57329,34.63367,-88.13426,34.62266), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.55016,34.66345,-90.57329,34.63367), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.54605,34.70208,-90.55016,34.66345), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.50549,34.76457,-90.47353,34.78884), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.47353,34.78884,-90.50549,34.76457), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.4638,34.83492,-90.40798,34.83527), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.40798,34.83527,-90.40163,34.83531), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.40163,34.83531,-90.40798,34.83527), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.31348,34.8717,-90.31142,34.87285), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.31142,34.87285,-90.31348,34.8717), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.09789,34.8922,-90.2501,34.90732), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.2501,34.90732,-88.15462,34.92239), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.15462,34.92239,-90.2501,34.90732), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.24448,34.9376,-88.15462,34.92239), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.43495,34.99375,-89.35268,34.994), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.35268,34.994,-89.64428,34.99407), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.64428,34.99407,-89.35268,34.994), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.72432,34.99419,-89.75961,34.99424), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.75961,34.99424,-89.72432,34.99419), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.79519,34.99429,-89.75961,34.99424), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.19829,34.99445,-89.79519,34.99429), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.02654,34.99496,-89.01713,34.99497), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-89.01713,34.99497,-89.02654,34.99496), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.82305,34.99521,-88.78661,34.99525), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.78661,34.99525,-88.82305,34.99521), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.25811,34.99546,-88.20006,34.99563), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.20006,34.99563,-90.3093,34.99569), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-90.3093,34.99569,-88.36353,34.99575), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.36353,34.99575,-88.38049,34.99579), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.38049,34.99579,-88.36353,34.99575), mapfile, tile_dir, 0, 11, "mississippi-ms")
	render_tiles((-88.46988,34.99603,-88.38049,34.99579), mapfile, tile_dir, 0, 11, "mississippi-ms")