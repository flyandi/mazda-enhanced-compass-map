#!/usr/bin/env python

import mapnik;

import sys, os

# Set up projections
# spherical mercator (most common target map projection of osm data imported with osm2pgsql)
merc = mapnik.Projection('+proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 +x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +no_defs +over')

# long/lat in degrees, aka ESPG:4326 and "WGS 84" 
longlat = mapnik.Projection('+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs')
# can also be constructed as:
#longlat = mapnik.Projection('+init=epsg:4326')

# ensure minimum mapnik version
if not hasattr(mapnik,'mapnik_version') and not mapnik.mapnik_version() >= 600:
    raise SystemExit('This script requires Mapnik >=0.6.0)')

if __name__ == "__main__":

    mapfile = "../tilestyles/mazda/mazda.xml"
    map_uri = "image.png"

    #---------------------------------------------------
    #  Change this to the bounding box you want
    #
    bounds = (-118.1896241, 33.9123493, -118.1810228, 33.9171174)


    #bounds = (-6.5, 49.5, 2.1, 59)
    #---------------------------------------------------

    z = 4
    imgx = 128 * z
    imgy = 128 * z

    m = mapnik.Map(imgx,imgy)
    mapnik.load_map(m,mapfile)
    
    # ensure the target map projection is mercator
    m.srs = merc.params()

    if hasattr(mapnik,'Box2d'):
        bbox = mapnik.Box2d(*bounds)
    else:
        bbox = mapnik.Envelope(*bounds)


    transform = mapnik.ProjTransform(longlat,merc)
    merc_bbox = transform.forward(bbox)
    m.zoom_to_box(merc_bbox)
    
    # render the map to an image
    im = mapnik.Image(imgx,imgy)
    mapnik.render(m, im)
    im.save(map_uri,'png')
    
    sys.stdout.write('output image to %s!\n' % map_uri)
    
    # Note: instead of creating an image, rendering to it, and then 
    # saving, we can also do this in one step like:
    # mapnik.render_to_file(m, map_uri,'png')
    
    # And in Mapnik >= 0.7.0 you can also use `render_to_file()` to output
    # to Cairo supported formats if you have Mapnik built with Cairo support
    # For example, to render to pdf or svg do:
    # mapnik.render_to_file(m, "image.pdf")
    # mapnik.render_to_file(m, "image.svg")
    

