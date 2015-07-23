#!/bin/sh

# 
# World Tiles
#

cd ../../base

./polytiles.py -b -180.0 -90.0 180.0 90.0 -s ../../tilestyles/mazda/mazda.xml -t ../../../output/world/ --zooms 0 6 --delete-empty --custom-fonts ../../../fonts/

