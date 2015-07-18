/*

OPEN STREETS
============

A TileMill map style for OpenStreetMap. For PostGIS databases created 
by osm2pgsql with a default style file, or style that is compatible 
with the default.

Use the included `configure.py` script to customize your database and
extent settings. See the README for more info.

<http://github.com/mapbox/open-streets-style>

*/







/* ---- PALETTE ---- */
@water:     #333;
@land:      #666;
@forest:    #555;
@agriculture:#777;
@grass:     #777; //lighten(@forest,0.25);
@park:      #888; //lighten(@forest,0.5);
@beach:     #999;
@building:  #888; //darken(,8);

/* ---- NATURAL & LANDUSE ---- */

Map { background-color: @water; }

#world[zoom<6],
#shoreline_300[zoom>=6][zoom<10],
#processed_p[zoom>=10] {
  polygon-fill: @land;
}

#ne-lakes[zoom<6],
.water[zoom>5] {
  polygon-fill:@water;
  polygon-gamma:0.8;
}
.water-outline[zoom>11] {
  line-color:darken(@water,10);
  [zoom=12] { line-width:0.8; }
  [zoom=13] { line-width:1.2; }
  [zoom=14] { line-width:1.4; }
  [zoom=15] { line-width:1.6; }
  [zoom=16] { line-width:1.8; }
  [zoom>16] { line-width:2; }
}

.wetland[zoom>17] {
  polygon-pattern-file:url(./res/wetland-8.png);
  [zoom>13] { polygon-pattern-file:url(./res/wetland-16.png); }
  [zoom>15] { polygon-pattern-file:url(./res/wetland-32.png); }
  polygon-pattern-alignment: global;
}

.forest[zoom>6][size='huge'],
.forest[zoom>7][size='large'],
.forest[zoom>8][size='medium'],
.forest[zoom>9][size='small'] {
  /* At lower zoom levels forests are dense and distracting. 
     Ramp them in gradually. */
  [zoom=7] { polygon-fill:lighten(@forest,14); }
  [zoom=8] { polygon-fill:lighten(@forest,12); }
  [zoom=9] { polygon-fill:lighten(@forest,9); }
  [zoom=10]{ polygon-fill:lighten(@forest,6); }
  [zoom=11]{ polygon-fill:lighten(@forest,3); }
  [zoom>11] { polygon-fill:@forest; }
  /* These outlines create a slight faux-blur effect. */
  [zoom>14] {
    line-color:@forest;
    line-opacity:0.4;
    line-join:round; }
  [zoom=15] { line-width:1.6; }
  [zoom=16] { line-width:2.6; }
  [zoom=17] { line-width:3.6; }
  [zoom>=18] { line-width:4.6; }
  /* a second outline for addtional blur */
  ::xtra {
    [zoom>16] {
      line-color:@forest;
      line-opacity:0.2;
      line-join:round; }
    [zoom=17] { line-width:7; }
    [zoom>=18] { line-width:9; }
  }
}

.agriculture[zoom>17] {
  polygon-fill:@agriculture;
}

.beach[zoom>9] {
  polygon-fill:@beach;
}

.grass[zoom>9][size='huge'],
.grass[zoom>10][size='large'],
.grass[zoom>11][size='medium'],
.grass[zoom>12][size='small'] {
  /* lighten relative to forest ramping */
  [zoom>=13] {polygon-fill:@grass;}
}

.park[zoom>13] {
  polygon-fill:@park;
  [zoom>13] { line-color:darken(@park,20); }
  [zoom=14] { line-width:0.6; }
  [zoom=15] { line-width:0.8; }
  [zoom>15] { line-width:1.2; }
}

/* ---- CAMPUSES ---- */
/* Note that amenity=school, amenity=hospital, etc are ideally polygons of the
   *campus*, but are occasionally applied to the physical building instead. */
@campus: #ECF;
.campus[zoom>13] {
  polygon-opacity:0.2;
  polygon-fill:@campus;
  [zoom>12] {
    line-opacity:0.4;
    line-color:spin(darken(@campus,20),20);
  }
  [zoom=13] { line-width:0.3; }
  [zoom=14] { line-width:0.5; }
  [zoom=15] { line-width:0.7; }
  [zoom=16] { line-width:0.8; }
  [zoom=17] { line-width:0.9; }
  [zoom=18] { line-width:1.0; }
}

/* ---- BUILDINGS ---- */
/* Transparent buildings account for situations where routes go
   in or under them */
.building[zoom>13][zoom<17] {
  polygon-fill:@building;
  [zoom=11] { polygon-opacity:0.1; }
  [zoom=12] { polygon-opacity:0.2; }
  [zoom=13] { polygon-opacity:0.3; }
  [zoom>13] {
    polygon-opacity:0.4;
    line-color:darken(@building,5);
    line-width:0.2;
  }
  [zoom>15] {
    line-color:darken(@building,10);
    line-width:0.4;
  }
}
.building[zoom>=17] {
  building-fill:lighten(@building,4);
  building-fill-opacity: 0.8;
  building-height:1.2;
}