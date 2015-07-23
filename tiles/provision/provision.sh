#!/bin/sh

# -----------------------------------------------------------
# Automatic Provisioning for tile rendering
# Andreas Schwarz, 2015
# http://github.com/flyandi
# -----------------------------------------------------------

# -----------------------------------------------------------
# Basic directories
# -----------------------------------------------------------
cd ../../
mkdir output
cd tiles/provision


# -----------------------------------------------------------
# Load Style Databases
# -----------------------------------------------------------

cd ../tilestyles/mazda/

# Cleanup
rm -rf layers/

# load databases
mkdir layers
cd layers
wget http://tilemill-data.s3.amazonaws.com/osm/processed_p.zip
unzip processed_p.zip
wget http://tilemill-data.s3.amazonaws.com/osm/shoreline_300.zip
unzip shoreline_300.zip

mkdir world
cd world
wget http://tilemill-data.s3.amazonaws.com/world_borders_merc.zip
unzip world_borders_merc.zip
cd ..

mkdir ne-admin-0
cd ne-admin-0
wget http://tilemill-data.s3.amazonaws.com/natural-earth-10m-1.3.0/admin_0_boundary_lines_land.zip
unzip admin_0_boundary_lines_land.zip
cd ..

mkdir ne-admin-1
cd ne-admin-1
wget http://tilemill-data.s3.amazonaws.com/natural-earth-10m-1.3.0/admin_1_states_provinces_lines_shp.zip
unzip admin_1_states_provinces_lines_shp.zip
cd ..

mkdir ne-lakes
cd ne-lakes
wget http://tilemill-data.s3.amazonaws.com/natural-earth-10m-1.3.0/lakes.zip
unzip lakes.zip

# return to base path
cd ../../../provision
