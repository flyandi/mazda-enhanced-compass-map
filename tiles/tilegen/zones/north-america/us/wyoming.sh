#!/bin/sh

# 
# Tiles for Wyoming
# north-america/us/wyoming 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/wyoming.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wyoming/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wyoming.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wyoming/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wyoming.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wyoming/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wyoming.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wyoming/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-wyoming/
find . -empty -type d -delete