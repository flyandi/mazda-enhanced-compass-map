Enhanced Compass for Mazda Connect
=============

The Enhanced Compass Application (ECA) for Mazda Connect brings a moving map and simple POI (Point Of Interests) absolutly free to your Mazda Infotainment System.

Please visit the system repository for more information: https://github.com/flyandi/mazda-enhanced-compass

This repository holds all resources to generate the actual map files which can get quite large.


## Introduction

ECA requires pre-rendered tiles in order to display the map. This concept allows to use the map without a internet connection and independent from other resources. It's also the most simple way to display a map and has a pretty good performance on the Infotainment system.

However it comes with the proce of having these tiles pre-rendered and they can get quiet large and require some hardware resources. For example the State California takes about 8h to pre-render on a MacBook Pro and results in about 8GB in tile data.

For that reason, I optimized as much as I can the rendering process, e.g. optimizing bounding box areas, splitting jobs and removing empty tiles with dynamic replacements.


## Get started

There are several steps involved to get tile rendering going. It's pretty straight forward once you setup the prerequisites. 


### Mapnik 

Mapnik is the base rendering software that transform geo date into tiles. I am not going into detail how to setup an Mapnik server because they are excellent instructions available on the original project.

Get started at https://github.com/mapnik/mapnik/wiki

Installation Guides for 

Linux: https://github.com/mapnik/mapnik/wiki/LinuxInstallation
OSX: https://github.com/mapnik/mapnik/wiki/MacInstallation_Homebrew


### Setup PostGres and PostGIS

Again, great tutorials available all over the Internet, e.g.

https://switch2osm.org/serving-tiles/manually-building-a-tile-server-12-04/

Important: Make sure your database is called "gis" when you create it and import data to it. Best is to follow the article above.


### Import OSM layers into the database

I great resources to download the OSM files is GeoFabrik.de which hostes daily updated snapshots of the OpenStreetMap project. You can import the entire world which is massive or just download the partial regions at http://download.geofabrik.de/

If you don't import the OSM layers, there will be no data available. For example, I only have California imported. If I try to render Colorado, I won't get any tiles because they will be all empty.


### Get read for rendering

The next step is to download the local layers and zone files. I setup a provisioning script that should do the job automatically.


### Clone the Repo

```shell
git clone https://github.com/flyandi/mazda-enhanced-compass-map
````


### Prepare layers and zones

Run the script ```tiles/provision/provision.sh``` 

This will download all resources needed in order to render the tiles.


### Test if it works

There is a script that generates an example tile. Execute the following script:

```
./tiles/tilegen/base/testimage.py
```

and compare it with the ```reference.png``` that is located in the very same directory. If the output matches, you are ready to go.


### Setup Zones

I already pre-compiled the zones and you will find them in the following folder.

```
tiles/tilegen/zones/*
```

But you can regenerate them or setup new zones. I included the world database that creates boundary data for each country in the world. Not really optimized but they work for most countries. You be warned to avoid large countries like Russia or China - they will take a long, long, very long time. 

Also included is the entire United States split in each state. 

I will prepare additional documentation how to create and handle zone files, but for now just check out the raw data in ```tiles/zonegen/```.

You need PHP in order to process the zones.


### Start rendering

As stated above, each zone file renderes a particualar area. There are two folders:

```tiles/tilegen/zones/us``` for the United States
```tiles/tilegen/zones/world``` for the entire World

Each folder contains multiple tile generation scripts for each region. To start rendering a region, just kickoff the script, e.g.

```./tiles/tilegen/zones/us/california-ca.py``` which will render the tiles for California.


### Output 

Any rendered zone will be rendered to ```./output/``` in it's folder. In order to use them with ECA you need to copy the tiles in the appropiate folder on the ECA SD-Card. Follow the instructions on the main project (as soon they are published).


## POI

This is work in progress and contains some simple POI processing but nothing is ready for production. 


## Contribute 

Yeah, let's do this!


## License

Written by Andreas Schwarz (http://github.com/flyandi/mazda-enhanced-compass)
Copyright (c) 2015. All rights reserved.
 
WARNING: The installation of this application requires modifications to your Mazda Connect system.
If you don't feel comfortable performing these changes, please do not attempt to install this. You might
be ending up with an unusuable system that requires reset by your Dealer. You were warned!

This program is free software: you can redistribute it and/or modify it under the terms of the 
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even 
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
License for more details.
 
You should have received a copy of the GNU General Public License along with this program. 
If not, see http://www.gnu.org/licenses/