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
    # Region: MZ
    # Region Name: Mozambique

	render_tiles((32.33305,-26.86028,32.89309,-26.84644), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.89309,-26.84644,32.13422,-26.84057), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.13422,-26.84057,32.89309,-26.84644), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.12915,-26.50584,32.07221,-26.39334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.07221,-26.39334,32.12915,-26.50584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.87804,-26.27834,32.8086,-26.27695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.8086,-26.27695,32.87804,-26.27834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.93193,-26.26612,32.0611,-26.26139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.0611,-26.26139,32.93193,-26.26612), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.86804,-26.24445,32.0611,-26.26139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.77082,-26.21195,32.86804,-26.24445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.10304,-26.16222,32.77082,-26.21195), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.90554,-26.10389,32.9536,-26.09), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.9536,-26.09,32.90554,-26.10389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.6261,-26.07528,32.9536,-26.09), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.08998,-26.00945,32.55027,-25.98278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.55027,-25.98278,32.58221,-25.97528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.58221,-25.97528,32.50971,-25.97251), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.50971,-25.97251,32.47887,-25.97139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.47887,-25.97139,32.50971,-25.97251), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.96774,-25.95818,32.52332,-25.94834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.52332,-25.94834,31.96774,-25.95818), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.68943,-25.88473,32.70443,-25.82584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.70443,-25.82584,31.91944,-25.81417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.91944,-25.81417,32.73471,-25.80834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.73471,-25.80834,31.91944,-25.81417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.01998,-25.65028,32.80832,-25.61639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.80832,-25.61639,32.01998,-25.65028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.98832,-25.51806,33.05554,-25.42334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.05554,-25.42334,33.23471,-25.33139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.23471,-25.33139,33.21971,-25.29278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.21971,-25.29278,33.23471,-25.33139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.5736,-25.18279,33.21971,-25.29278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.01859,-25.035,33.9886,-25.02195), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.9886,-25.02195,32.01859,-25.035), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.48495,-24.85536,33.9886,-25.02195), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.02583,-24.64528,35.17944,-24.53362), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.17944,-24.53362,32.0161,-24.45945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.0161,-24.45945,35.17944,-24.53362), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.88583,-24.17112,35.47555,-24.13334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.47555,-24.13334,31.88583,-24.17112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.33582,-23.95112,31.87638,-23.9275), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.87638,-23.9275,35.33582,-23.95112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.4636,-23.89001,31.76971,-23.85639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.76971,-23.85639,35.38638,-23.84112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.38638,-23.84112,31.76971,-23.85639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.53387,-23.82556,35.38638,-23.84112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.48916,-23.78473,35.38443,-23.74778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.38443,-23.74778,35.4086,-23.73917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.4086,-23.73917,35.38443,-23.74778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.3686,-23.71362,35.4086,-23.73917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.34082,-23.68473,35.38138,-23.68028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.38138,-23.68028,35.34082,-23.68473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.6836,-23.61362,35.38138,-23.68028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.41165,-23.52695,31.55083,-23.47667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.55083,-23.47667,35.41165,-23.52695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.56166,-23.18667,35.55804,-23.0239), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.55804,-23.0239,35.53526,-22.95028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.53526,-22.95028,35.59304,-22.92223), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.59304,-22.92223,35.53526,-22.95028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.48582,-22.63223,35.40166,-22.48528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.40166,-22.48528,35.31248,-22.41806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.31248,-22.41806,31.29763,-22.41614), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.29763,-22.41614,35.31248,-22.41806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.39333,-22.35417,35.30193,-22.33334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.30193,-22.33334,31.39333,-22.35417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.38943,-22.29251,35.52776,-22.27806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.52776,-22.27806,35.38943,-22.29251), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.42888,-22.20223,35.52776,-22.27806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.44749,-22.11834,35.33804,-22.10695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.33804,-22.10695,35.49194,-22.09834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.49194,-22.09834,35.33804,-22.10695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.23749,-21.57806,35.13749,-21.43167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.13749,-21.43167,32.49217,-21.34646), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.49217,-21.34646,35.1111,-21.34056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.1111,-21.34056,32.49217,-21.34646), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.08415,-21.32167,32.41026,-21.31112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.41026,-21.31112,35.08415,-21.32167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.04555,-21.25556,35.0911,-21.2314), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.0911,-21.2314,35.04555,-21.25556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.13026,-21.19112,35.0911,-21.2314), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.36027,-21.13584,35.01638,-21.11695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.01638,-21.11695,32.36027,-21.13584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.0611,-21.09334,35.01638,-21.11695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.08943,-21.03806,35.0611,-21.09334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.1211,-20.96862,35.04054,-20.94139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.04054,-20.94139,35.11388,-20.93056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.11388,-20.93056,35.04054,-20.94139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.52137,-20.91417,35.11388,-20.93056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.9936,-20.735,34.86304,-20.71167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.86304,-20.71167,34.94971,-20.69361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.94971,-20.69361,34.86304,-20.71167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.89443,-20.66556,32.4836,-20.66167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.4836,-20.66167,34.89443,-20.66556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.50221,-20.59861,32.66582,-20.55723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.66582,-20.55723,32.55082,-20.555), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.55082,-20.555,32.66582,-20.55723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.66415,-20.54,32.55082,-20.555), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.71249,-20.49473,34.66415,-20.54), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.70999,-20.44806,34.65694,-20.42806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.65694,-20.42806,34.70999,-20.44806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.69249,-20.38278,34.6411,-20.38111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.6411,-20.38111,34.69249,-20.38278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.74194,-20.23584,34.75416,-20.18251), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.75416,-20.18251,34.66277,-20.16945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.66277,-20.16945,34.75416,-20.18251), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.77693,-20.14862,32.90109,-20.13473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.90109,-20.13473,34.77693,-20.14862), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.95304,-20.03639,33.02665,-20.03112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.02665,-20.03112,32.95304,-20.03639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.70721,-19.89417,34.88221,-19.86362), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.88221,-19.86362,34.8436,-19.85445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.8436,-19.85445,34.77193,-19.85362), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.77193,-19.85362,34.8436,-19.85445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.77499,-19.82001,33.03915,-19.81389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.03915,-19.81389,34.77499,-19.82001), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.05943,-19.78028,34.8111,-19.77612), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.8111,-19.77612,33.05943,-19.78028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.97581,-19.73667,34.70499,-19.71806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.70499,-19.71806,32.97581,-19.73667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.91276,-19.6925,32.84554,-19.685), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.84554,-19.685,32.91276,-19.6925), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.97859,-19.66417,32.9536,-19.64861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.9536,-19.64861,32.97859,-19.66417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.53165,-19.62056,34.61277,-19.61), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.61277,-19.61,34.53165,-19.62056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.6236,-19.59501,35.25471,-19.58834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.25471,-19.58834,34.6236,-19.59501), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.55666,-19.58167,35.25471,-19.58834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.85054,-19.49389,32.78304,-19.46778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.78304,-19.46778,32.85054,-19.49389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.48082,-19.37973,32.78471,-19.36639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.78471,-19.36639,35.48082,-19.37973), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.85221,-19.28667,32.78471,-19.36639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.72499,-19.08473,32.84526,-19.03723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.84526,-19.03723,32.71609,-19.02195), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.71609,-19.02195,32.84526,-19.03723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.69915,-18.94445,36.00749,-18.91973), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.00749,-18.91973,36.13194,-18.90306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.13194,-18.90306,36.00749,-18.91973), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.72026,-18.88278,36.13304,-18.87278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.13304,-18.87278,32.72026,-18.88278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.25127,-18.85999,36.13304,-18.87278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.7011,-18.83695,36.25127,-18.85999), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.11526,-18.80306,36.39888,-18.79417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.39888,-18.79417,32.89999,-18.79111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.89999,-18.79111,36.39888,-18.79417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.81721,-18.77917,36.28082,-18.77639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.28082,-18.77639,32.81721,-18.77917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.92804,-18.76723,36.28082,-18.77639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.40332,-18.74223,32.92804,-18.76723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.22999,-18.69639,36.38499,-18.69528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.38499,-18.69528,36.22999,-18.69639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.94971,-18.69028,36.38499,-18.69528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.47166,-18.59667,32.88832,-18.53056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.88832,-18.53056,36.51054,-18.5289), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.51054,-18.5289,32.88832,-18.53056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.59721,-18.46723,33.0136,-18.46695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.0136,-18.46695,36.59721,-18.46723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.00144,-18.40503,33.07304,-18.34889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.07304,-18.34889,33.00144,-18.40503), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.96665,-18.23584,36.7836,-18.22612), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.7836,-18.22612,32.96665,-18.23584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.82971,-18.21195,36.7836,-18.22612), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.88749,-18.19445,33.0011,-18.18333), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.0011,-18.18333,36.83499,-18.17806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.83499,-18.17806,33.0011,-18.18333), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.97581,-18.10139,36.97027,-18.06278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.97027,-18.06278,32.97581,-18.10139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.9811,-18.01278,32.94609,-17.975), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.94609,-17.975,36.9811,-18.01278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.9747,-17.92361,36.82027,-17.90473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.82027,-17.90473,32.9747,-17.92361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.8761,-17.87723,36.82027,-17.90473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.96609,-17.84223,36.8761,-17.87723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.01859,-17.72334,37.2836,-17.69001), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.2836,-17.69001,33.01859,-17.72334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.41693,-17.63278,33.04332,-17.61389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.04332,-17.61389,37.41693,-17.63278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.96027,-17.52084,37.73082,-17.47306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.73082,-17.47306,37.69749,-17.45917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.69749,-17.45917,37.73082,-17.47306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.87221,-17.37639,33.04492,-17.34626), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.04492,-17.34626,37.87221,-17.37639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.11137,-17.31139,38.14943,-17.28445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.14943,-17.28445,38.11137,-17.31139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.13082,-17.25362,38.14943,-17.28445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.11888,-17.17889,32.96832,-17.1475), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.96832,-17.1475,38.49749,-17.14417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.49749,-17.14417,32.96832,-17.1475), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.2906,-17.13581,35.09165,-17.12917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.09165,-17.12917,35.2906,-17.13581), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.5611,-17.11917,38.50638,-17.10973), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.50638,-17.10973,38.5611,-17.11917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.72305,-17.065,35.30804,-17.06139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.30804,-17.06139,38.72305,-17.065), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.66444,-17.02917,35.05248,-17.02723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.05248,-17.02723,38.66444,-17.02917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.08582,-16.99167,35.05248,-17.02723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.13915,-16.95195,35.2711,-16.95028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.2711,-16.95028,35.13915,-16.95195), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.12749,-16.93306,32.84248,-16.93111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.84248,-16.93111,39.12749,-16.93306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.15638,-16.88084,39.12666,-16.86973), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.12666,-16.86973,39.15638,-16.88084), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.30415,-16.84612,35.14638,-16.84111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.14638,-16.84111,35.30415,-16.84612), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.94387,-16.83278,35.14638,-16.84111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.04943,-16.82389,35.12832,-16.81695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.12832,-16.81695,35.04943,-16.82389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.26749,-16.77973,35.12832,-16.81695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.7685,-16.71782,32.9822,-16.70861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.9822,-16.70861,32.7685,-16.71782), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.27137,-16.69722,32.9822,-16.70861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.70776,-16.68417,34.85971,-16.6775), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.85971,-16.6775,32.70776,-16.68417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.16859,-16.62112,32.70832,-16.60778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.70832,-16.60778,35.16859,-16.62112), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.65526,-16.58139,39.67443,-16.55528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.67443,-16.55528,35.13721,-16.55056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.13721,-16.55056,39.67443,-16.55528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.76193,-16.53723,35.13721,-16.55056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.25166,-16.45861,39.81082,-16.45695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.81082,-16.45695,35.25166,-16.45861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.06276,-16.44917,39.81082,-16.45695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.24026,-16.43889,39.85721,-16.43056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.85721,-16.43056,32.24026,-16.43889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.90805,-16.41833,39.81749,-16.41278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.81749,-16.41278,31.90805,-16.41833), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.59443,-16.40472,39.81749,-16.41278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.57555,-16.32389,39.78221,-16.31167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.78221,-16.31167,34.57555,-16.32389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.45415,-16.28417,39.78221,-16.31167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.87916,-16.24167,31.76055,-16.24), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.76055,-16.24,39.87916,-16.24167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.97499,-16.23751,31.76055,-16.24), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.29332,-16.22472,39.97499,-16.23751), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.41054,-16.205,35.29332,-16.22472), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.42388,-16.16111,35.60609,-16.13723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.60609,-16.13723,35.41165,-16.12445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.41165,-16.12445,35.60609,-16.13723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.42776,-16.06362,35.78693,-16.0625), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.78693,-16.0625,34.42776,-16.06362), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.98471,-16.05917,35.78693,-16.0625), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.30083,-16.02862,31.05805,-16.02306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.05805,-16.02306,31.30083,-16.02862), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.42277,-16.00917,40.13221,-16.00278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.13221,-16.00278,30.9286,-16.00223), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.9286,-16.00223,40.13221,-16.00278), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.05832,-16.00167,30.9286,-16.00223), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.2636,-15.91639,40.05832,-16.00167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.26027,-15.80778,34.32416,-15.74361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.32416,-15.74361,40.36027,-15.72334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.36027,-15.72334,34.32416,-15.74361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.40998,-15.67583,30.41388,-15.63361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.41388,-15.63361,34.40998,-15.67583), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.35777,-15.55472,34.44331,-15.5475), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.44331,-15.5475,30.35777,-15.55472), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.54332,-15.53417,40.49666,-15.52917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.49666,-15.52917,40.54332,-15.53417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.42526,-15.49667,30.38805,-15.47889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.38805,-15.47889,34.42526,-15.49667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.8672,-15.41945,30.38805,-15.47889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.84358,-15.33422,30.35805,-15.33028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.35805,-15.33028,35.84182,-15.32784), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.84182,-15.32784,30.35805,-15.33028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.58971,-15.28278,40.68388,-15.26), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.68388,-15.26,30.26833,-15.25084), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.26833,-15.25084,40.68388,-15.26), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.57804,-15.20278,40.51443,-15.18723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.51443,-15.18723,40.66582,-15.18667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.66582,-15.18667,40.51443,-15.18723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.80026,-15.17778,40.66582,-15.18667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.62721,-15.155,35.80026,-15.17778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.53471,-15.12389,40.62721,-15.155), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.69888,-15.08528,40.53471,-15.12389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.7736,-14.99806,40.65999,-14.98722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.65999,-14.98722,30.21277,-14.98111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.21277,-14.98111,40.65999,-14.98722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.68221,-14.93139,40.74721,-14.92834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.74721,-14.92834,40.68221,-14.93139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.66888,-14.9075,40.74721,-14.92834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.92416,-14.88556,35.8811,-14.885), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.8811,-14.885,35.92416,-14.88556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.73777,-14.87917,35.8811,-14.885), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.64165,-14.86695,40.73777,-14.87917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.83916,-14.80389,34.56693,-14.78722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.56693,-14.78722,40.83916,-14.80389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((30.84555,-14.76389,34.56693,-14.78722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.52248,-14.68861,35.8772,-14.65611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.8772,-14.65611,31.38221,-14.645), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.38221,-14.645,35.8772,-14.65611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.5411,-14.61556,33.69276,-14.59861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.69276,-14.59861,33.65054,-14.58945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.65054,-14.58945,33.69276,-14.59861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.62777,-14.56945,31.58527,-14.56167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((31.58527,-14.56167,40.62777,-14.56945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.76638,-14.55111,31.58527,-14.56167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.6661,-14.5375,33.82555,-14.53389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.82555,-14.53389,40.6661,-14.5375), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.82999,-14.53028,33.82555,-14.53389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.76166,-14.52639,40.82999,-14.53028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.80527,-14.51973,40.76166,-14.52639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.70776,-14.50056,40.82804,-14.49945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.82804,-14.49945,33.70776,-14.50056), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.07388,-14.49389,40.82804,-14.49945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.73637,-14.4875,34.07388,-14.49389), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.93221,-14.47528,33.73637,-14.4875), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.54943,-14.44223,40.68999,-14.43584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.68999,-14.43584,40.82555,-14.43167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.82555,-14.43167,40.68999,-14.43584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.47887,-14.41056,40.8011,-14.40417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.8011,-14.40417,34.30109,-14.40361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.30109,-14.40361,40.8011,-14.40417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.38888,-14.39722,34.30109,-14.40361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((32.07777,-14.38806,34.38888,-14.39722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.68054,-14.3675,32.07777,-14.38806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.63749,-14.33972,40.68054,-14.3675), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.7411,-14.28361,40.60277,-14.26778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.60277,-14.26778,40.7411,-14.28361), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.72027,-14.19806,40.66888,-14.19028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.66888,-14.19028,40.72027,-14.19806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.53054,-14.17167,40.66888,-14.19028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.62276,-14.12556,40.59666,-14.08722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.59666,-14.08722,40.63832,-14.07917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.63832,-14.07917,40.59666,-14.08722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((33.2218,-14.0111,40.63721,-13.95167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.63721,-13.95167,33.2218,-14.0111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.09526,-13.68611,40.53749,-13.64528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.53749,-13.64528,35.09526,-13.68611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.60194,-13.58195,40.53749,-13.64528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.52554,-13.515,34.86579,-13.50039), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.86579,-13.50039,34.86212,-13.49742), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.86212,-13.49742,34.65804,-13.49639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.65804,-13.49639,34.86212,-13.49742), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.56915,-13.34695,40.57027,-13.34528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.57027,-13.34528,34.56915,-13.34695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.55916,-13.09028,40.48971,-13.0275), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.48971,-13.0275,40.51332,-13.00167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.51332,-13.00167,40.59693,-12.98556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.59693,-12.98556,40.41832,-12.98028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.41832,-12.98028,40.59693,-12.98556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.54166,-12.95778,40.49165,-12.95), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.49165,-12.95,40.54166,-12.95778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.41305,-12.93917,40.49165,-12.95), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.52499,-12.91389,40.41305,-12.93917), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.47665,-12.88417,40.55638,-12.85445), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.55638,-12.85445,40.47665,-12.88417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.60944,-12.80195,40.55888,-12.785), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.55888,-12.785,40.6461,-12.77334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.6461,-12.77334,40.55888,-12.785), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.52998,-12.75889,40.6461,-12.77334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.61638,-12.71334,40.57832,-12.71111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.57832,-12.71111,40.61638,-12.71334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56944,-12.65222,40.57832,-12.71111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56805,-12.54306,40.47137,-12.50889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.47137,-12.50889,34.44443,-12.50334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.44443,-12.50334,40.47137,-12.50889), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56248,-12.39972,40.50388,-12.37806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.50388,-12.37806,40.56248,-12.39972), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.50777,-12.29639,40.4511,-12.25473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.4511,-12.25473,40.50943,-12.23945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.50943,-12.23945,40.4511,-12.25473), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.37387,-12.16722,40.50943,-12.23945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.53915,-11.99445,40.4786,-11.90611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.4786,-11.90611,40.5161,-11.84334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.5161,-11.84334,40.47555,-11.82028), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.47555,-11.82028,40.5161,-11.84334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.61443,-11.76389,36.53721,-11.72695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.53721,-11.72695,37.44026,-11.72556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.44026,-11.72556,36.53721,-11.72695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.67221,-11.71611,37.44026,-11.72556), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.2861,-11.70417,36.67221,-11.71611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.18554,-11.70417,36.67221,-11.71611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.34721,-11.68667,37.2861,-11.70417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.10804,-11.66445,37.34721,-11.68667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.47637,-11.59806,35.6436,-11.58945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.6436,-11.58945,35.47637,-11.59806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.16304,-11.57778,34.62609,-11.57583), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.62609,-11.57583,36.16304,-11.57778), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.95806,-11.57291,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.95806,-11.57291,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((34.96201,-11.5729,34.95806,-11.57291), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.41914,-11.57212,34.96201,-11.5729), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.82499,-11.57056,35.41914,-11.57212), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.02582,-11.56723,40.42527,-11.56528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.42527,-11.56528,37.02582,-11.56723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.79193,-11.56111,40.42527,-11.56528), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((36.08637,-11.54306,37.79193,-11.56111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.82471,-11.51472,36.08637,-11.54306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.93332,-11.43084,35.81693,-11.42083), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((35.81693,-11.42083,38.49331,-11.4171), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.49331,-11.4171,40.4811,-11.41639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.4811,-11.41639,38.49331,-11.4171), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.84332,-11.40528,40.4811,-11.41639), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.39582,-11.35,40.35249,-11.32695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.35249,-11.32695,37.86859,-11.32667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((37.86859,-11.32667,40.35249,-11.32695), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.67249,-11.27084,38.10221,-11.25334), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.10221,-11.25334,38.67249,-11.27084), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.85277,-11.20722,40.5061,-11.20306), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.5061,-11.20306,38.85277,-11.20722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((38.89777,-11.17222,39.25694,-11.17083), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.25694,-11.17083,38.89777,-11.17222), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.49944,-11.14445,39.25694,-11.17083), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56165,-11.06639,40.50638,-11.03945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.50638,-11.03945,40.56888,-11.02611), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56888,-11.02611,40.50638,-11.03945), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.62832,-10.94806,40.5036,-10.94111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.5036,-10.94111,39.62832,-10.94806), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((39.76693,-10.92056,40.5036,-10.94111), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.61249,-10.85723,40.61749,-10.83584), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.61749,-10.83584,40.61249,-10.85723), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.49471,-10.79667,40.48721,-10.76834), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.48721,-10.76834,40.49471,-10.79667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.64638,-10.69861,40.5786,-10.68861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.5786,-10.68861,40.64638,-10.69861), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.63943,-10.67167,40.16553,-10.67139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.16553,-10.67139,40.63943,-10.67167), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.57332,-10.65,40.16553,-10.67139), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.56944,-10.59722,40.27387,-10.58667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.27387,-10.58667,40.56944,-10.59722), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.50943,-10.57417,40.27387,-10.58667), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.42054,-10.50584,40.5211,-10.48417), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.5211,-10.48417,40.43555,-10.48045), mapfile, tile_dir, 0, 11, "mz-mozambique")
	render_tiles((40.43555,-10.48045,40.5211,-10.48417), mapfile, tile_dir, 0, 11, "mz-mozambique")