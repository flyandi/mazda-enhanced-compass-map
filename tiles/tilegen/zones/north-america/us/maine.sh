#!/bin/sh

# 
# Tiles for Maine
# north-america/us/maine 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/maine.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maine/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maine.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maine/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maine.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maine/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maine.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maine/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-maine/
find . -empty -type d -delete