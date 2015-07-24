#!/bin/sh

# 
# Tiles for Arkansas
# north-america/us/arkansas 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/arkansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arkansas/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arkansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arkansas/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arkansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arkansas/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arkansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arkansas/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-arkansas/
find . -empty -type d -delete