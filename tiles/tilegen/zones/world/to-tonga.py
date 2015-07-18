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
    # Region: TO
    # Region Name: Tonga

	render_tiles((-174.6469,-18.8303,-174.64169,-18.8286), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6469,-18.8303,-174.64169,-18.8286), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.65359,-18.8303,-174.64169,-18.8286), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.64169,-18.8286,-174.65919,-18.8278), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.65781,-18.8286,-174.65919,-18.8278), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.65919,-18.8278,-174.6386,-18.8275), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6386,-18.8275,-174.65919,-18.8278), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.66611,-18.8264,-174.6347,-18.8258), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6347,-18.8258,-174.66611,-18.8264), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6669,-18.8258,-174.66611,-18.8264), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6667,-18.8244,-174.63029,-18.8233), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.63029,-18.8233,-174.6667,-18.8244), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.66859,-18.8208,-174.63029,-18.8233), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.627,-18.8181,-174.6711,-18.8164), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6711,-18.8164,-174.627,-18.8181), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6245,-18.8142,-174.6731,-18.8128), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6731,-18.8128,-174.6245,-18.8142), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.62309,-18.8094,-174.6739,-18.8086), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6739,-18.8086,-174.62309,-18.8094), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6245,-18.8067,-174.6739,-18.8086), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.675,-18.8044,-174.6292,-18.8028), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6292,-18.8028,-174.675,-18.8044), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6747,-18.8008,-174.6347,-18.8003), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6347,-18.8003,-174.6747,-18.8008), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.63921,-18.7958,-174.67419,-18.7942), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.67419,-18.7942,-174.63921,-18.7958), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6425,-18.7919,-174.67081,-18.7903), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.67081,-18.7903,-174.6425,-18.7919), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.64439,-18.7883,-174.6653,-18.7872), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6653,-18.7872,-174.64439,-18.7883), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.66029,-18.7858,-174.6456,-18.7856), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6456,-18.7856,-174.66029,-18.7858), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6555,-18.7839,-174.6497,-18.7831), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.6497,-18.7831,-174.6555,-18.7839), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.14529,-21.2681,-175.1497,-21.2678), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.14529,-21.2681,-175.1497,-21.2678), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1497,-21.2678,-175.14529,-21.2681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.15781,-21.2678,-175.14529,-21.2681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.13499,-21.2678,-175.14529,-21.2681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1658,-21.2664,-175.1297,-21.2661), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1297,-21.2661,-175.1658,-21.2664), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1736,-21.265,-175.1297,-21.2661), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1236,-21.2639,-175.1736,-21.265), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.17999,-21.2622,-175.1236,-21.2639), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1181,-21.2603,-175.17999,-21.2622), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1842,-21.2581,-175.1181,-21.2603), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1147,-21.255,-175.1864,-21.2528), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1864,-21.2528,-175.1147,-21.255), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.11169,-21.2492,-175.188,-21.2472), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.188,-21.2472,-175.11169,-21.2492), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1111,-21.2431,-175.1889,-21.2403), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1889,-21.2403,-175.1111,-21.2431), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1122,-21.2375,-175.19,-21.2356), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.19,-21.2356,-175.1122,-21.2375), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1136,-21.2311,-175.1908,-21.23), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1908,-21.23,-175.1136,-21.2311), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1153,-21.2247,-175.1931,-21.2242), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1931,-21.2242,-175.1153,-21.2247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1972,-21.2197,-175.1161,-21.2178), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1161,-21.2178,-175.1972,-21.2197), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2019,-21.2158,-175.1161,-21.2178), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2083,-21.2133,-175.1153,-21.2111), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1153,-21.2111,-175.2153,-21.2106), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2153,-21.2106,-175.1153,-21.2111), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.22189,-21.2086,-175.2305,-21.2072), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2305,-21.2072,-175.22189,-21.2086), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2361,-21.2047,-175.1133,-21.2044), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1133,-21.2044,-175.2361,-21.2047), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.24249,-21.2019,-175.2489,-21.1994), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2489,-21.1994,-175.1097,-21.1992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1097,-21.1992,-175.2489,-21.1994), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2636,-21.1989,-175.1097,-21.1992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2561,-21.1981,-175.2636,-21.1989), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2722,-21.1981,-175.2636,-21.1989), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2794,-21.1956,-175.10561,-21.1947), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.10561,-21.1947,-175.2794,-21.1956), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.285,-21.1928,-175.10561,-21.1947), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1017,-21.1903,-175.2906,-21.1897), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2906,-21.1897,-175.1017,-21.1903), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.297,-21.1869,-175.0975,-21.1858), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0975,-21.1858,-175.297,-21.1869), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.30251,-21.1844,-175.0975,-21.1858), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3083,-21.1825,-175.09331,-21.1814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09331,-21.1814,-175.3083,-21.1825), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3145,-21.18,-175.09331,-21.1814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.14169,-21.1786,-175.09,-21.1783), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09,-21.1783,-175.1458,-21.1781), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1458,-21.1781,-175.09,-21.1783), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3194,-21.1769,-175.13831,-21.1761), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.13831,-21.1761,-175.15221,-21.1756), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08389,-21.1761,-175.15221,-21.1756), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.15221,-21.1756,-175.13831,-21.1761), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.07719,-21.1744,-175.22501,-21.1742), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.32359,-21.1744,-175.22501,-21.1742), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.22501,-21.1742,-175.07719,-21.1744), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.21831,-21.1739,-175.22501,-21.1742), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0697,-21.1739,-175.22501,-21.1742), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.13499,-21.1731,-175.2131,-21.1725), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2131,-21.1725,-175.13499,-21.1731), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0636,-21.1717,-175.2131,-21.1725), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.15691,-21.1717,-175.2131,-21.1725), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2292,-21.1717,-175.2131,-21.1725), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3275,-21.17,-175.2092,-21.1692), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2092,-21.1692,-175.3275,-21.17), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1339,-21.1683,-175.1619,-21.1681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1619,-21.1681,-175.1339,-21.1683), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0589,-21.1678,-175.1619,-21.1681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.23219,-21.1672,-175.1339,-21.1669), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1339,-21.1669,-175.23219,-21.1672), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.20419,-21.1656,-175.05499,-21.1647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3322,-21.1656,-175.05499,-21.1647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.05499,-21.1647,-175.0556,-21.1642), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1675,-21.1647,-175.0556,-21.1642), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0556,-21.1642,-175.05499,-21.1647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0547,-21.1642,-175.05499,-21.1647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1319,-21.1631,-175.1989,-21.1628), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1989,-21.1628,-175.1319,-21.1631), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1736,-21.1619,-175.0517,-21.1617), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0517,-21.1617,-175.1736,-21.1619), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3364,-21.1617,-175.1736,-21.1619), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1794,-21.1603,-175.1933,-21.1597), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1933,-21.1597,-175.1794,-21.1603), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.23219,-21.1597,-175.1794,-21.1603), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1875,-21.1589,-175.1933,-21.1597), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0489,-21.1572,-175.3403,-21.1564), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3403,-21.1564,-175.0489,-21.1572), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1319,-21.1553,-175.3403,-21.1564), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.22971,-21.1539,-175.1319,-21.1553), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0464,-21.1525,-175.34331,-21.1522), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.34331,-21.1522,-175.0464,-21.1525), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1306,-21.15,-175.34331,-21.1522), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2247,-21.15,-175.34331,-21.1522), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.21941,-21.1472,-175.3472,-21.1469), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3472,-21.1469,-175.21941,-21.1472), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.04559,-21.1458,-175.3472,-21.1469), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.12939,-21.1447,-175.21471,-21.1442), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.21471,-21.1442,-175.12939,-21.1447), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.35139,-21.1433,-175.21471,-21.1442), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.12669,-21.14,-175.0472,-21.1394), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0472,-21.1394,-175.12669,-21.14), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2119,-21.1394,-175.12669,-21.14), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3544,-21.1381,-175.12421,-21.1369), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.12421,-21.1369,-175.3544,-21.1381), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.12331,-21.135,-175.2131,-21.1347), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2131,-21.1347,-175.05029,-21.1344), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.05029,-21.1344,-175.2131,-21.1347), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3569,-21.1339,-175.05029,-21.1344), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1194,-21.1325,-175.3569,-21.1339), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.21471,-21.1311,-175.0553,-21.1306), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0553,-21.1306,-175.11391,-21.1303), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.11391,-21.1303,-175.0553,-21.1306), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.35809,-21.1294,-175.11391,-21.1303), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.10719,-21.1281,-175.22031,-21.1278), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.22031,-21.1278,-175.10719,-21.1281), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.06081,-21.1272,-175.0997,-21.1267), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.1003,-21.1272,-175.0997,-21.1267), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0997,-21.1267,-175.06081,-21.1272), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2767,-21.125,-175.3186,-21.1247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0936,-21.125,-175.3186,-21.1247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0869,-21.125,-175.3186,-21.1247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3186,-21.1247,-175.2767,-21.125), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.31059,-21.1247,-175.2767,-21.125), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2258,-21.1247,-175.2767,-21.125), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.06689,-21.1247,-175.2767,-21.125), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0731,-21.1242,-175.3186,-21.1247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0811,-21.1242,-175.3186,-21.1247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3036,-21.1233,-175.28391,-21.1231), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.28391,-21.1231,-175.3036,-21.1233), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.325,-21.1228,-175.28391,-21.1231), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2706,-21.1228,-175.28391,-21.1231), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2314,-21.1214,-175.29781,-21.1211), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.29781,-21.1211,-175.35719,-21.1208), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.35719,-21.1208,-175.2903,-21.1206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2903,-21.1206,-175.35719,-21.1208), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2653,-21.1197,-175.2903,-21.1206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3297,-21.1197,-175.2903,-21.1206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.237,-21.1183,-175.2653,-21.1197), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2597,-21.1169,-175.237,-21.1183), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.33389,-21.1153,-175.2419,-21.115), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.2419,-21.115,-175.25439,-21.1147), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.25439,-21.1147,-175.2419,-21.115), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3567,-21.1147,-175.2419,-21.115), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.24831,-21.1125,-175.25439,-21.1147), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3369,-21.1103,-175.3575,-21.1097), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3575,-21.1097,-175.3369,-21.1103), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3595,-21.1069,-175.3372,-21.1061), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3372,-21.1061,-175.3595,-21.1069), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3606,-21.1025,-175.3595,-21.0992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3595,-21.0992,-175.3372,-21.0986), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3372,-21.0986,-175.3595,-21.0992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3575,-21.0961,-175.3372,-21.0986), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.355,-21.0914,-175.3358,-21.0911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3358,-21.0911,-175.355,-21.0914), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3325,-21.0872,-175.35139,-21.0861), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.35139,-21.0861,-175.3325,-21.0872), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3286,-21.0842,-175.35139,-21.0861), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3231,-21.0811,-175.31689,-21.0789), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.34669,-21.0811,-175.31689,-21.0789), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.31689,-21.0789,-175.3103,-21.0775), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3103,-21.0775,-175.343,-21.0767), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.343,-21.0767,-175.3103,-21.0775), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3047,-21.0744,-175.3392,-21.0731), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3392,-21.0731,-175.3047,-21.0744), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3022,-21.0714,-175.3331,-21.0708), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3331,-21.0708,-175.3022,-21.0714), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.32561,-21.07,-175.3331,-21.0708), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3203,-21.0683,-175.3019,-21.0681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3019,-21.0681,-175.3203,-21.0683), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3053,-21.065,-175.3161,-21.0647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3161,-21.0647,-175.3053,-21.065), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.3125,-21.0636,-175.3161,-21.0647), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0667,-18.7064,-174.0708,-18.7053), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0667,-18.7064,-174.0708,-18.7053), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0708,-18.7053,-174.0614,-18.7047), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0614,-18.7047,-174.0708,-18.7053), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.075,-18.7036,-174.0903,-18.7031), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0903,-18.7031,-174.075,-18.7036), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0831,-18.7022,-174.0903,-18.7031), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0961,-18.7011,-174.0831,-18.7022), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.06171,-18.6986,-174.09779,-18.6964), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.09779,-18.6964,-174.06171,-18.6986), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0639,-18.6922,-174.09419,-18.6911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.09419,-18.6911,-174.0639,-18.6922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0903,-18.6872,-174.0858,-18.6842), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0656,-18.6872,-174.0858,-18.6842), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0858,-18.6842,-174.06889,-18.6822), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.06889,-18.6822,-174.0797,-18.6814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0797,-18.6814,-174.07359,-18.6811), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.07359,-18.6811,-174.0797,-18.6814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25031,-19.6572,-174.2444,-19.6561), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25031,-19.6572,-174.2444,-19.6561), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2444,-19.6561,-174.25031,-19.6572), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2406,-19.6544,-174.2444,-19.6561), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2594,-19.6522,-174.2608,-19.6519), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2608,-19.6519,-174.2594,-19.6522), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.23891,-19.6511,-174.2608,-19.6519), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.265,-19.6489,-174.2392,-19.6469), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2392,-19.6469,-174.2692,-19.6467), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2692,-19.6467,-174.2392,-19.6469), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2739,-19.6433,-174.2417,-19.6428), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2417,-19.6428,-174.2739,-19.6433), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2464,-19.6389,-174.25281,-19.6356), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2778,-19.6389,-174.25281,-19.6356), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25281,-19.6356,-174.28191,-19.6344), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.28191,-19.6344,-174.25281,-19.6356), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2592,-19.6331,-174.28191,-19.6344), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.285,-19.6294,-174.26311,-19.6292), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26311,-19.6292,-174.285,-19.6294), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.28751,-19.6236,-174.2661,-19.6233), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2661,-19.6233,-174.28751,-19.6236), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2906,-19.6186,-174.26939,-19.6183), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26939,-19.6183,-174.2906,-19.6186), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2717,-19.6133,-174.29221,-19.6122), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.29221,-19.6122,-174.2717,-19.6133), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2939,-19.6072,-174.2742,-19.6069), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2742,-19.6069,-174.2939,-19.6072), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.27499,-19.6019,-174.2939,-19.6011), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2939,-19.6011,-174.27499,-19.6019), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2775,-19.5969,-174.2919,-19.5944), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2919,-19.5944,-174.28,-19.5928), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.28,-19.5928,-174.28641,-19.5914), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.28641,-19.5914,-174.28,-19.5928), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.06329,-19.8008,-175.0589,-19.8006), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0567,-19.8008,-175.0589,-19.8006), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0589,-19.8006,-175.06329,-19.8008), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.05141,-19.7992,-175.0697,-19.7989), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0697,-19.7989,-175.05141,-19.7992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0731,-19.7967,-175.0475,-19.7961), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0475,-19.7961,-175.0731,-19.7967), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.07719,-19.7936,-175.0442,-19.7922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0442,-19.7922,-175.07919,-19.7911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.07919,-19.7911,-175.0442,-19.7922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08087,-19.78906,-175.04221,-19.7875), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08087,-19.78906,-175.04221,-19.7875), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.04221,-19.7875,-175.0831,-19.7861), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0831,-19.7861,-175.04221,-19.7875), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08389,-19.7861,-175.04221,-19.7875), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0856,-19.7825,-175.0425,-19.7814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0425,-19.7814,-175.0856,-19.7825), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0442,-19.7772,-175.088,-19.7767), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.088,-19.7767,-175.0442,-19.7772), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0481,-19.7725,-175.08971,-19.7717), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08971,-19.7717,-175.0481,-19.7725), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0486,-19.7706,-175.08971,-19.7717), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0914,-19.7681,-175.04671,-19.7667), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.04671,-19.7667,-175.0914,-19.7681), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.045,-19.7639,-175.09171,-19.7633), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09171,-19.7633,-175.045,-19.7639), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0439,-19.7592,-175.09419,-19.7589), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09419,-19.7589,-175.0439,-19.7592), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.045,-19.755,-175.0972,-19.7533), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0972,-19.7533,-175.045,-19.755), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0461,-19.7508,-175.0986,-19.7497), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0986,-19.7497,-175.0461,-19.7508), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0464,-19.7472,-175.0986,-19.7497), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.04671,-19.7431,-175.09779,-19.7414), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09779,-19.7414,-175.04671,-19.7431), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.04919,-19.7375,-175.09641,-19.7356), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09641,-19.7356,-175.04919,-19.7375), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0506,-19.7319,-175.05029,-19.7292), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.05029,-19.7292,-175.09419,-19.7286), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09419,-19.7286,-175.05029,-19.7283), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.05029,-19.7283,-175.09419,-19.7286), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0517,-19.7264,-175.093,-19.7253), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.093,-19.7253,-175.0517,-19.7264), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0547,-19.7219,-175.093,-19.7253), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.09171,-19.7181,-175.0625,-19.7169), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0589,-19.7181,-175.0625,-19.7169), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0625,-19.7169,-175.0667,-19.7167), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0667,-19.7167,-175.0625,-19.7169), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0708,-19.715,-175.0905,-19.7144), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0905,-19.7144,-175.0708,-19.715), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0903,-19.7119,-175.0905,-19.7144), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.075,-19.7119,-175.0905,-19.7144), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0869,-19.7081,-175.08389,-19.7069), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.07829,-19.7081,-175.08389,-19.7069), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.08389,-19.7069,-175.0817,-19.7064), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-175.0817,-19.7064,-175.08389,-19.7069), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.27921,-19.7536,-174.27251,-19.7528), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.27921,-19.7536,-174.27251,-19.7528), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.27251,-19.7528,-174.28641,-19.7525), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.28641,-19.7525,-174.27251,-19.7528), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2928,-19.7511,-174.2664,-19.75), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2664,-19.75,-174.2928,-19.7511), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.29919,-19.7486,-174.2664,-19.75), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26311,-19.7447,-174.29919,-19.7486), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.3047,-19.7447,-174.29919,-19.7486), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.30721,-19.7408,-174.26109,-19.7386), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26109,-19.7386,-174.3053,-19.7369), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.3053,-19.7369,-174.3031,-19.7353), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.3031,-19.7353,-174.3,-19.7347), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.3,-19.7347,-174.3031,-19.7353), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.297,-19.7336,-174.3,-19.7347), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2597,-19.7319,-174.2908,-19.7314), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2908,-19.7314,-174.2597,-19.7319), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2861,-19.7275,-174.25751,-19.7253), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25751,-19.7253,-174.2814,-19.7239), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2814,-19.7239,-174.25751,-19.7253), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2767,-19.7194,-174.2547,-19.7186), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2547,-19.7186,-174.2767,-19.7194), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2728,-19.715,-174.25281,-19.7119), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25281,-19.7119,-174.2697,-19.7092), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2697,-19.7092,-174.24921,-19.7072), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.24921,-19.7072,-174.2697,-19.7092), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26781,-19.7025,-174.2478,-19.7006), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2478,-19.7006,-174.26781,-19.7025), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2475,-19.6972,-174.26579,-19.6956), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.26579,-19.6956,-174.2475,-19.6972), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.25079,-19.6922,-174.2617,-19.6911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2617,-19.6911,-174.25079,-19.6922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.2561,-19.6881,-174.2617,-19.6911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91721,-21.4542,-174.91499,-21.4539), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91721,-21.4542,-174.91499,-21.4539), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91499,-21.4539,-174.91721,-21.4542), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9245,-21.4522,-174.9117,-21.4514), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9117,-21.4514,-174.9272,-21.4508), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9272,-21.4508,-174.9117,-21.4514), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90829,-21.4461,-174.9086,-21.4433), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9339,-21.4461,-174.9086,-21.4433), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9086,-21.4433,-174.90829,-21.4461), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9364,-21.4403,-174.90919,-21.4392), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90919,-21.4392,-174.9364,-21.4403), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90939,-21.4358,-174.93941,-21.4344), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.93941,-21.4344,-174.90939,-21.4358), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9106,-21.4317,-174.94279,-21.4303), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.94279,-21.4303,-174.9106,-21.4317), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9108,-21.4267,-174.9467,-21.425), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9467,-21.425,-174.9108,-21.4267), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.953,-21.4231,-174.91251,-21.4217), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91251,-21.4217,-174.9603,-21.4211), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9603,-21.4211,-174.91251,-21.4217), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.96581,-21.4178,-174.9136,-21.4169), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9136,-21.4169,-174.96581,-21.4178), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9675,-21.4117,-174.9142,-21.41), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9142,-21.41,-174.9675,-21.4117), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.96831,-21.4053,-174.91499,-21.4031), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91499,-21.4031,-174.97391,-21.4028), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.97391,-21.4028,-174.91499,-21.4031), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9803,-21.3994,-174.91389,-21.3983), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91389,-21.3983,-174.9803,-21.3994), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9783,-21.395,-174.91389,-21.3983), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9119,-21.395,-174.91389,-21.3983), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9736,-21.3914,-174.9117,-21.3911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9117,-21.3911,-174.9736,-21.3914), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9106,-21.3883,-174.9117,-21.3911), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.97079,-21.3853,-174.9111,-21.3842), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9111,-21.3842,-174.97079,-21.3853), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9119,-21.38,-174.97141,-21.3783), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.97141,-21.3783,-174.9119,-21.38), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9131,-21.3744,-174.9731,-21.3719), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9731,-21.3719,-174.9131,-21.3744), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9122,-21.3664,-174.9747,-21.3658), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9747,-21.3658,-174.9122,-21.3664), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90939,-21.3603,-174.97391,-21.3589), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.97391,-21.3589,-174.90939,-21.3603), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90581,-21.3553,-174.9706,-21.3539), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9706,-21.3539,-174.90581,-21.3553), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9017,-21.3508,-174.96581,-21.35), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.96581,-21.35,-174.9017,-21.3508), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.96111,-21.3464,-174.90109,-21.3447), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90109,-21.3447,-174.9556,-21.3433), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9556,-21.3433,-174.90109,-21.3447), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90221,-21.3411,-174.95081,-21.3397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.95081,-21.3397,-174.90221,-21.3411), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90421,-21.3383,-174.95081,-21.3397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9453,-21.3367,-174.90691,-21.3353), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.90691,-21.3353,-174.9453,-21.3367), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9406,-21.3331,-174.91029,-21.3322), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91029,-21.3322,-174.9406,-21.3331), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9364,-21.3292,-174.9128,-21.3272), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9128,-21.3272,-174.9364,-21.3292), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9353,-21.3247,-174.9153,-21.3236), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9153,-21.3236,-174.9353,-21.3247), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.93559,-21.3197,-174.9155,-21.3175), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9155,-21.3175,-174.93559,-21.3197), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.93581,-21.315,-174.9155,-21.3175), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9133,-21.3108,-174.93671,-21.3094), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.93671,-21.3094,-174.9133,-21.3108), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91141,-21.3061,-174.9361,-21.3039), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9361,-21.3039,-174.91141,-21.3061), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9108,-21.3006,-174.9333,-21.2981), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9333,-21.2981,-174.9108,-21.3006), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91251,-21.2956,-174.9333,-21.2981), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9292,-21.2928,-174.9158,-21.2919), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9158,-21.2919,-174.9292,-21.2928), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.91859,-21.2903,-174.9245,-21.2897), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.9245,-21.2897,-174.91859,-21.2903), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.99471,-18.7056,-173.9953,-18.7047), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.99471,-18.7056,-173.9953,-18.7047), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9953,-18.7047,-173.9903,-18.7044), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.99609,-18.7047,-173.9903,-18.7044), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9903,-18.7044,-173.9953,-18.7047), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9995,-18.7039,-173.9903,-18.7044), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0031,-18.7014,-173.9883,-18.6992), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9883,-18.6992,-174.0031,-18.7014), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0061,-18.6964,-173.98779,-18.6939), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.98779,-18.6939,-174.0078,-18.6914), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0078,-18.6914,-173.98779,-18.6939), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.98669,-18.6889,-174.00751,-18.6867), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00751,-18.6867,-173.98669,-18.6889), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.987,-18.6842,-174.0056,-18.6825), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0056,-18.6825,-173.987,-18.6842), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0061,-18.6806,-173.985,-18.6803), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.985,-18.6803,-174.0061,-18.6806), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0081,-18.6783,-173.98331,-18.6775), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.98331,-18.6775,-174.0108,-18.6767), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0108,-18.6767,-173.98331,-18.6775), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0114,-18.6758,-174.0108,-18.6753), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0108,-18.6753,-174.0114,-18.6758), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0119,-18.6739,-173.98219,-18.6728), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.98219,-18.6728,-174.0119,-18.6739), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.01331,-18.6711,-174.0517,-18.6697), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0517,-18.6697,-174.01331,-18.6711), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.01311,-18.6697,-174.01331,-18.6711), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0544,-18.6697,-174.01331,-18.6711), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0117,-18.6683,-174.04559,-18.6675), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.04559,-18.6675,-174.00999,-18.6672), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00999,-18.6672,-174.04559,-18.6675), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9794,-18.6669,-174.05721,-18.6667), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.05721,-18.6667,-173.9794,-18.6669), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0578,-18.6667,-173.9794,-18.6669), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0047,-18.6661,-174.05721,-18.6667), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0294,-18.6661,-174.05721,-18.6667), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9617,-18.6642,-174.0233,-18.6639), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0367,-18.6642,-174.0233,-18.6639), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0233,-18.6639,-173.9617,-18.6642), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9986,-18.6633,-173.9761,-18.6631), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9761,-18.6631,-173.9986,-18.6633), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0417,-18.6631,-173.9986,-18.6633), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.968,-18.6631,-173.9986,-18.6633), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.062,-18.6622,-173.9731,-18.6619), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9731,-18.6619,-174.062,-18.6622), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9942,-18.6603,-173.9731,-18.6619), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.96001,-18.6603,-173.9731,-18.6619), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0208,-18.6581,-173.9944,-18.6575), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9944,-18.6575,-174.0208,-18.6581), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.06419,-18.6564,-173.99809,-18.6558), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.99809,-18.6558,-174.06419,-18.6564), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9572,-18.6544,-173.9505,-18.6536), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9505,-18.6536,-173.9572,-18.6544), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174,-18.6528,-173.9505,-18.6536), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0222,-18.6517,-174,-18.6528), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00031,-18.65,-173.9472,-18.6489), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0658,-18.65,-173.9472,-18.6489), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9472,-18.6489,-174.00031,-18.65), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0233,-18.6467,-174.0675,-18.645), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0675,-18.645,-174.0233,-18.6467), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0231,-18.6433,-174.0011,-18.6431), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0011,-18.6431,-174.0231,-18.6433), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9481,-18.6428,-174.0011,-18.6431), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00439,-18.64,-173.9158,-18.6397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9158,-18.6397,-174.00439,-18.64), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9536,-18.6394,-173.9158,-18.6397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00439,-18.6394,-173.9158,-18.6397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.07001,-18.6394,-173.9158,-18.6397), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0067,-18.6386,-173.9214,-18.6381), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9214,-18.6381,-174.0233,-18.6378), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9106,-18.6381,-174.0233,-18.6378), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0233,-18.6378,-173.9214,-18.6381), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0208,-18.6358,-173.9575,-18.635), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9575,-18.635,-174.01781,-18.6347), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.01781,-18.6347,-173.9575,-18.635), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0114,-18.6347,-173.9575,-18.635), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9256,-18.6336,-173.9072,-18.6331), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9072,-18.6331,-173.9256,-18.6336), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0706,-18.6325,-173.9072,-18.6331), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9294,-18.6292,-173.95689,-18.6281), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.95689,-18.6281,-173.9294,-18.6292), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9061,-18.6269,-174.0681,-18.6264), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0681,-18.6264,-173.9061,-18.6269), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.95081,-18.6258,-174.0681,-18.6264), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9325,-18.6239,-173.94501,-18.6236), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.94501,-18.6236,-173.9325,-18.6239), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0625,-18.6236,-173.9325,-18.6239), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.03329,-18.6211,-173.9075,-18.6206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0495,-18.6211,-173.9075,-18.6206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.05721,-18.6211,-173.9075,-18.6206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0414,-18.6211,-173.9075,-18.6206), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9075,-18.6206,-174.03329,-18.6211), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.93719,-18.6194,-174.02721,-18.6189), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.02721,-18.6189,-173.93719,-18.6194), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9431,-18.6183,-174.02721,-18.6189), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.94189,-18.6164,-173.9397,-18.6158), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9397,-18.6158,-173.9108,-18.6156), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9108,-18.6156,-173.9397,-18.6158), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0233,-18.6144,-173.9108,-18.6156), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.91389,-18.6103,-174.0197,-18.6083), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0197,-18.6083,-173.91389,-18.6103), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9178,-18.6058,-174.0197,-18.6083), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0172,-18.6031,-173.9178,-18.6058), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9167,-18.5997,-174.015,-18.5964), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.015,-18.5964,-173.9147,-18.5958), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9147,-18.5958,-174.015,-18.5964), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.91811,-18.5922,-173.92281,-18.5897), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.92281,-18.5897,-173.91811,-18.5922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.01311,-18.5897,-173.91811,-18.5922), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.92751,-18.5858,-174.0089,-18.5844), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0089,-18.5844,-173.92751,-18.5858), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9303,-18.5814,-174.0056,-18.58), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.0056,-18.58,-173.9303,-18.5814), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-174.00079,-18.5764,-173.9317,-18.5753), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9317,-18.5753,-174.00079,-18.5764), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.99561,-18.5733,-173.9317,-18.5753), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9894,-18.5711,-173.93559,-18.5708), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.93559,-18.5708,-173.9894,-18.5711), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.98219,-18.5703,-173.95171,-18.57), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.95171,-18.57,-173.98219,-18.5703), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9603,-18.5694,-173.9458,-18.5692), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9747,-18.5694,-173.9458,-18.5692), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9458,-18.5692,-173.9603,-18.5694), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.93919,-18.5689,-173.9458,-18.5692), mapfile, tile_dir, 0, 11, "to-tonga")
	render_tiles((-173.9675,-18.5681,-173.93919,-18.5689), mapfile, tile_dir, 0, 11, "to-tonga")