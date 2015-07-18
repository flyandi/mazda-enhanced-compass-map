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
    # Region: AG
    # Region Name: Antigua and Barbuda

	render_tiles((-61.7389,17.5406,-61.7328,17.5411), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7389,17.5406,-61.7328,17.5411), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7328,17.5411,-61.7389,17.5406), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7428,17.5439,-61.7672,17.5447), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7672,17.5447,-61.7614,17.5453), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7614,17.5453,-61.7672,17.5447), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7311,17.5472,-61.7456,17.5478), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7456,17.5478,-61.7572,17.5481), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7572,17.5481,-61.7456,17.5478), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7694,17.5492,-61.7519,17.5494), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7519,17.5494,-61.7694,17.5492), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7308,17.5533,-61.7717,17.5536), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7717,17.5536,-61.7308,17.5533), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7739,17.5583,-61.7308,17.5594), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7308,17.5594,-61.7739,17.5583), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7775,17.5622,-61.7308,17.5594), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7822,17.565,-61.7311,17.5653), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7311,17.5653,-61.7822,17.565), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7869,17.5678,-61.7311,17.5653), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7914,17.5706,-61.7308,17.5714), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7308,17.5714,-61.7978,17.5722), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7978,17.5722,-61.7308,17.5714), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8017,17.5756,-61.7314,17.5769), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7314,17.5769,-61.8017,17.5756), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8064,17.5783,-61.7314,17.5769), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8475,17.5808,-61.8111,17.5811), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8111,17.5811,-61.8475,17.5808), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8428,17.5828,-61.8531,17.5831), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8531,17.5831,-61.8428,17.5828), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7306,17.5839,-61.8531,17.5831), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8156,17.5839,-61.8531,17.5831), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8219,17.5856,-61.8394,17.5867), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8394,17.5867,-61.8283,17.5869), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8283,17.5869,-61.8394,17.5867), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8553,17.5875,-61.8283,17.5869), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8347,17.5886,-61.8553,17.5875), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7303,17.59,-61.8347,17.5886), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8567,17.5925,-61.7303,17.59), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.73,17.5961,-61.8561,17.5989), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8561,17.5989,-61.73,17.5961), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7294,17.6025,-61.8558,17.605), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8558,17.605,-61.7294,17.6025), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7292,17.6086,-61.8339,17.61), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8339,17.61,-61.8397,17.6108), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8575,17.61,-61.8397,17.6108), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8397,17.6108,-61.8339,17.61), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8306,17.6139,-61.73,17.6144), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.73,17.6144,-61.8428,17.6147), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8428,17.6147,-61.73,17.6144), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8578,17.6156,-61.8428,17.6147), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8289,17.6197,-61.7306,17.62), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7306,17.62,-61.8289,17.6197), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8433,17.6206,-61.7306,17.62), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8578,17.6219,-61.8433,17.6206), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8267,17.625,-61.8417,17.6264), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7317,17.625,-61.8417,17.6264), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8417,17.6264,-61.8267,17.625), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8575,17.6281,-61.7339,17.6294), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7339,17.6294,-61.8575,17.6281), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.825,17.6311,-61.7339,17.6294), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8406,17.6333,-61.8578,17.6336), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8578,17.6336,-61.8406,17.6333), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7353,17.6347,-61.8578,17.6336), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8236,17.6378,-61.7369,17.6397), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7369,17.6397,-61.8578,17.64), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8578,17.64,-61.7369,17.6397), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8397,17.64,-61.7369,17.6397), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8228,17.6444,-61.7381,17.6447), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7381,17.6447,-61.8228,17.6444), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8583,17.6456,-61.8392,17.6464), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8392,17.6464,-61.8583,17.6456), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7403,17.6492,-61.8225,17.6508), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8225,17.6508,-61.8589,17.6511), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8589,17.6511,-61.8225,17.6508), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8406,17.6514,-61.8589,17.6511), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7433,17.6531,-61.8406,17.6514), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8428,17.6558,-61.7469,17.6564), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7469,17.6564,-61.8594,17.6569), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8231,17.6564,-61.8594,17.6569), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8594,17.6569,-61.7469,17.6564), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7511,17.6597,-61.845,17.6603), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.845,17.6603,-61.7511,17.6597), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8617,17.6614,-61.8236,17.6619), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8236,17.6619,-61.8617,17.6614), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.755,17.6631,-61.8236,17.6619), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8472,17.6647,-61.8639,17.6658), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8639,17.6658,-61.7589,17.6664), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7589,17.6664,-61.8256,17.6667), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8256,17.6667,-61.7589,17.6664), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8494,17.6692,-61.8658,17.6703), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8658,17.6703,-61.8278,17.6711), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7617,17.6703,-61.8278,17.6711), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8278,17.6711,-61.8658,17.6703), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8533,17.6725,-61.7653,17.6736), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7653,17.6736,-61.8689,17.6742), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8689,17.6742,-61.8317,17.6744), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8317,17.6744,-61.8689,17.6742), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8561,17.6767,-61.8364,17.6772), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8364,17.6772,-61.8561,17.6767), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7675,17.6781,-61.8711,17.6786), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8711,17.6786,-61.7675,17.6781), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8594,17.6806,-61.7714,17.6817), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8403,17.6806,-61.7714,17.6817), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7714,17.6817,-61.8594,17.6806), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8442,17.6839,-61.8622,17.6844), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8725,17.6839,-61.8622,17.6844), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8622,17.6844,-61.8442,17.6839), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7747,17.6856,-61.8094,17.6864), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8139,17.6856,-61.8094,17.6864), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8094,17.6864,-61.8489,17.6867), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8489,17.6867,-61.8094,17.6864), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8211,17.6867,-61.8094,17.6864), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8661,17.6878,-61.8489,17.6867), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8264,17.6889,-61.8536,17.6894), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8739,17.6889,-61.8536,17.6894), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8536,17.6894,-61.8264,17.6889), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7775,17.6894,-61.8264,17.6889), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8075,17.6903,-61.8536,17.6894), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8314,17.6917,-61.8681,17.6922), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8681,17.6922,-61.8314,17.6917), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8564,17.6933,-61.8681,17.6922), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7806,17.6933,-61.8681,17.6922), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8744,17.6944,-61.8564,17.6933), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8064,17.6956,-61.8319,17.6958), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8319,17.6958,-61.8064,17.6956), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8258,17.6964,-61.8661,17.6967), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8661,17.6967,-61.8258,17.6964), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7836,17.6972,-61.8661,17.6967), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8208,17.6978,-61.7836,17.6972), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8586,17.6978,-61.7836,17.6972), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8753,17.6986,-61.8208,17.6978), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8033,17.7,-61.7872,17.7006), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7872,17.7006,-61.8681,17.7011), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8681,17.7011,-61.7872,17.7006), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7989,17.7022,-61.8194,17.7025), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8194,17.7025,-61.7989,17.7022), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7931,17.7028,-61.8194,17.7025), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8592,17.7033,-61.7931,17.7028), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8731,17.7039,-61.8592,17.7033), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8217,17.7069,-61.8589,17.7097), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8589,17.7097,-61.8236,17.7114), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8236,17.7114,-61.8589,17.7097), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8567,17.7147,-61.8267,17.7153), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8267,17.7153,-61.8567,17.7147), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8539,17.7192,-61.8342,17.7219), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8297,17.7192,-61.8342,17.7219), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8342,17.7219,-61.8503,17.7228), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8503,17.7228,-61.8342,17.7219), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8392,17.7247,-61.8456,17.725), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8456,17.725,-61.8392,17.7247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7381,16.9897,-61.7322,16.9903), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7381,16.9897,-61.7322,16.9903), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7322,16.9903,-61.7436,16.9906), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7436,16.9906,-61.7322,16.9903), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.75,16.9922,-61.7436,16.9906), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7286,16.9939,-61.7553,16.9944), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7553,16.9944,-61.7286,16.9939), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7617,16.9961,-61.8239,16.9969), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8239,16.9969,-61.7617,16.9961), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8292,16.9969,-61.7617,16.9961), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7672,16.9981,-61.8194,16.9989), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8194,16.9989,-61.7278,16.9994), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7278,16.9994,-61.8194,16.9989), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8322,16.9994,-61.8194,16.9989), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.79,17.0003,-61.8144,17.0011), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8144,17.0011,-61.7972,17.0014), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7972,17.0014,-61.8144,17.0011), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8094,17.0022,-61.8033,17.0028), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8033,17.0028,-61.7686,17.0033), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7686,17.0033,-61.8033,17.0028), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8353,17.0033,-61.8033,17.0028), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7275,17.0056,-61.7883,17.0061), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8406,17.0056,-61.7883,17.0061), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7883,17.0061,-61.7275,17.0056), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8478,17.0067,-61.7883,17.0061), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7708,17.0078,-61.8478,17.0067), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8517,17.01,-61.7853,17.0106), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7853,17.0106,-61.7281,17.0111), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7281,17.0111,-61.8578,17.0114), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8578,17.0114,-61.7281,17.0111), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7736,17.0117,-61.8578,17.0114), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7808,17.0128,-61.8642,17.0131), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8642,17.0131,-61.7808,17.0128), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8703,17.0147,-61.8642,17.0131), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7294,17.0164,-61.8761,17.0169), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8761,17.0169,-61.7294,17.0164), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8806,17.0197,-61.7317,17.0208), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7317,17.0208,-61.8806,17.0197), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6919,17.0233,-61.8836,17.0236), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8836,17.0236,-61.6919,17.0233), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6867,17.0244,-61.7347,17.0247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6989,17.0244,-61.7347,17.0247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7347,17.0247,-61.6867,17.0244), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7053,17.0258,-61.7347,17.0247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7117,17.0275,-61.8858,17.0281), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6825,17.0275,-61.8858,17.0281), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8858,17.0281,-61.7117,17.0275), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7369,17.0292,-61.7169,17.0297), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7169,17.0297,-61.7369,17.0292), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7217,17.0325,-61.8872,17.0331), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8872,17.0331,-61.6808,17.0333), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6808,17.0333,-61.8872,17.0331), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7339,17.0336,-61.6808,17.0333), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7281,17.0342,-61.7339,17.0336), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6772,17.0372,-61.8856,17.0392), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8856,17.0392,-61.6675,17.0406), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6725,17.0392,-61.6675,17.0406), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6675,17.0406,-61.8856,17.0392), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8831,17.0444,-61.6664,17.0458), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6664,17.0458,-61.8831,17.0444), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8814,17.0503,-61.6669,17.0514), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6669,17.0514,-61.8814,17.0503), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6683,17.0567,-61.6714,17.0606), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8811,17.0567,-61.6714,17.0606), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6714,17.0606,-61.8825,17.0617), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8825,17.0617,-61.6714,17.0606), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6767,17.0628,-61.8825,17.0617), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6831,17.0642,-61.6767,17.0628), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8856,17.0656,-61.6894,17.0658), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6894,17.0658,-61.8856,17.0656), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6936,17.0678,-61.6894,17.0658), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8878,17.07,-61.6958,17.0711), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6958,17.0711,-61.8878,17.07), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6925,17.0747,-61.6742,17.0756), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6742,17.0756,-61.6806,17.0758), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8869,17.0756,-61.6806,17.0758), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6806,17.0758,-61.6742,17.0756), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6869,17.0761,-61.6806,17.0758), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8822,17.0775,-61.8761,17.0781), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8761,17.0781,-61.67,17.0783), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.67,17.0783,-61.8761,17.0781), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8703,17.0786,-61.67,17.0783), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8669,17.0822,-61.6689,17.085), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6689,17.085,-61.8683,17.0858), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8683,17.0858,-61.6689,17.085), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6967,17.0886,-61.8722,17.0892), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8722,17.0892,-61.8889,17.0897), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7197,17.0892,-61.8889,17.0897), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8889,17.0897,-61.8722,17.0892), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6703,17.0903,-61.7147,17.0906), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8831,17.0903,-61.7147,17.0906), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7028,17.0903,-61.7147,17.0906), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7147,17.0906,-61.6703,17.0903), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8778,17.0914,-61.7092,17.0917), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7092,17.0917,-61.8778,17.0914), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6844,17.0922,-61.7092,17.0917), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6931,17.0922,-61.7092,17.0917), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6742,17.0936,-61.8911,17.0942), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7219,17.0936,-61.8911,17.0942), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8911,17.0942,-61.6797,17.0944), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6797,17.0944,-61.8911,17.0942), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.6892,17.095,-61.6797,17.0944), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7242,17.0981,-61.8894,17.1), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8894,17.1,-61.7272,17.1019), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7272,17.1019,-61.8894,17.1), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8872,17.1053,-61.7303,17.1058), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7303,17.1058,-61.8872,17.1053), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8842,17.1097,-61.7308,17.1103), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7308,17.1103,-61.8842,17.1097), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7569,17.1114,-61.8492,17.1117), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8492,17.1117,-61.7569,17.1114), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7264,17.1122,-61.8561,17.1125), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8433,17.1122,-61.8561,17.1125), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8561,17.1125,-61.7264,17.1122), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7631,17.1128,-61.8561,17.1125), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8808,17.1133,-61.7208,17.1136), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7208,17.1136,-61.8808,17.1133), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8625,17.1142,-61.7208,17.1136), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.715,17.1142,-61.7208,17.1136), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7097,17.1156,-61.8397,17.1158), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7678,17.1156,-61.8397,17.1158), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8397,17.1158,-61.7097,17.1156), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8689,17.1158,-61.7097,17.1156), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7556,17.1158,-61.7097,17.1156), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8775,17.1169,-61.8397,17.1158), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7056,17.1183,-61.7717,17.1189), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7717,17.1189,-61.8725,17.1192), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8725,17.1192,-61.7717,17.1189), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8397,17.1206,-61.7561,17.1217), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7561,17.1217,-61.8397,17.1206), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8453,17.1228,-61.7056,17.1231), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7747,17.1228,-61.7056,17.1231), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7056,17.1231,-61.8453,17.1228), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8517,17.1244,-61.7308,17.1247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7308,17.1247,-61.8517,17.1244), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7247,17.1253,-61.7308,17.1247), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7194,17.1267,-61.755,17.1269), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.755,17.1269,-61.7194,17.1267), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7086,17.1272,-61.755,17.1269), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7353,17.1275,-61.7144,17.1278), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7144,17.1278,-61.7353,17.1275), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7761,17.1281,-61.7144,17.1278), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8539,17.1289,-61.7761,17.1281), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7511,17.13,-61.8539,17.1289), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7725,17.1317,-61.7375,17.1319), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7375,17.1319,-61.7725,17.1317), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7469,17.1328,-61.7375,17.1319), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8542,17.1344,-61.7469,17.1328), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7397,17.1367,-61.7717,17.1369), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7717,17.1369,-61.7397,17.1367), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7442,17.1372,-61.7717,17.1369), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8519,17.1397,-61.7731,17.1422), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7731,17.1422,-61.8483,17.1433), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8483,17.1433,-61.7731,17.1422), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7761,17.1461,-61.845,17.1469), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.845,17.1469,-61.7761,17.1461), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7783,17.1506,-61.8417,17.1508), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8417,17.1508,-61.7783,17.1506), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7814,17.1544,-61.8386,17.1553), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8386,17.1553,-61.7814,17.1544), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7842,17.1583,-61.8361,17.1603), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8361,17.1603,-61.7892,17.1611), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7892,17.1611,-61.8361,17.1603), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.7944,17.1633,-61.8075,17.1639), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8075,17.1639,-61.8014,17.1642), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8328,17.1639,-61.8014,17.1642), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8014,17.1642,-61.8075,17.1639), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8144,17.1647,-61.8014,17.1642), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8208,17.1664,-61.8264,17.1672), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")
	render_tiles((-61.8264,17.1672,-61.8208,17.1664), mapfile, tile_dir, 0, 11, "ag-antigua-and-barbuda")