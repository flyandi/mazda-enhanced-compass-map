@font_reg: "Open Sans Semibold";
@font_bold: "Open Sans Bold";

/* ---- PLACENAMES ---- */

@fontzoom: 5;
@clzoom: 3;

.placename.medium[place='state'][zoom>0][zoom<7],
.placename.medium[place='city'][zoom>6][zoom<14],
.placename.medium[place='metropolis'][zoom>6][zoom<14] {
  text-face-name:@font_bold;
  text-name:"[name]";
  text-fill:#000;
  text-halo-fill:#fff;
  text-halo-radius:1.5;
  [zoom>=3] { text-size:10+@clzoom; text-halo-radius:1; }
  [zoom=8] { text-size:10+@clzoom; text-halo-radius:1; }
  [zoom=9] { text-size:11+@clzoom; }
  [zoom=10] { text-size:12+@clzoom; }
  [zoom=10] { text-size:13+@clzoom; text-character-spacing:1; }
  [zoom=11] { text-size:14+@clzoom; text-character-spacing:1; }
  [zoom=12] { text-size:16+@clzoom; text-character-spacing:1; }
  [zoom=13] { text-size:18+@clzoom; text-character-spacing:1; }
}

.placename.medium[place='large_town'][zoom>7][zoom<16],
.placename.medium[place='town'][zoom>8][zoom<16],
.placename.medium[place='small_town'][zoom>9][zoom<16]{
  text-face-name:@font_bold;
  text-name:"[name]";
  text-fill:#000;
  text-halo-fill:#fff;
  text-halo-radius:1.5;
  [zoom=8] { text-size:10+@clzoom; text-halo-radius:1; }
  [zoom=9] { text-size:10+@clzoom; text-halo-radius:1; }
  [zoom=10] { text-size:10+@clzoom; text-halo-radius:1; }
  [zoom=11] { text-size:11+@clzoom; }
  [zoom=12] { text-size:12+@clzoom; }
  [zoom=13] { text-size:13+@clzoom; text-character-spacing:1; }
  [zoom=14] { text-size:14+@clzoom; text-character-spacing:1; }
  [zoom=15] { text-size:16+@clzoom; text-character-spacing:1; }
  [zoom=16] { text-size:18+@clzoom; text-character-spacing:1; }
}

/* ---- HIGHWAY ---- */

.highway-label[zoom>11] {
  text-face-name:@font_reg;
  text-halo-radius:2;
  text-placement:line;
  text-name:"''";
  [highway='motorway'][zoom>=12] {
    text-name:"[name]";
    text-fill:spin(darken(@motorway_fill,70),-15);
    text-halo-fill:lighten(@motorway_fill,8);
    [zoom>=12] { text-size:12+@fontzoom; }
    [zoom>=17] { text-size:14+@fontzoom; }
  }
  [highway='trunk'][zoom>=12] {
    text-name:"[name]";
    text-fill:spin(darken(@trunk_fill,66),-15);
    text-halo-fill:lighten(@trunk_fill,8);
    [zoom>=12] { text-size:11+@fontzoom; }
    [zoom>=17] { text-size:12+@fontzoom; }
  }
  [highway='primary'][zoom>=13] {
    text-name:"[name]";
    text-fill:spin(darken(@primary_fill,66),-15);
    text-halo-fill:lighten(@primary_fill,8);
    [zoom>=13] { text-size:11+@fontzoom; }
    [zoom>=17] { text-size:16+@fontzoom; }
  }
  [highway='secondary'][zoom>=13] {
    text-name:"[name]";
    text-fill:spin(darken(@secondary_fill,66),-15);
    text-halo-fill:lighten(@secondary_fill,8);
    [zoom>=13] { text-size:11+@fontzoom; }
    [zoom>=17] { text-size:16+@fontzoom; }
  }
  [highway='residential'][zoom>=15],
  [highway='road'][zoom>=15],
  [highway='tertiary'][zoom>=15],
  [highway='unclassified'][zoom>=15] {
    text-name:"[name]";
    text-fill:#444;
    text-halo-fill:#fff;
    text-size:10+@fontzoom;
    [zoom>=17] { text-size:14+@fontzoom; }
  }
}
