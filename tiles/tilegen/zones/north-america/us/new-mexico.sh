#!/bin/sh

# 
# Tiles for New Mexico
# north-america/us/new-mexico 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/new-mexico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-mexico/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-mexico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-mexico/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-mexico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-mexico/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-mexico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-mexico/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

