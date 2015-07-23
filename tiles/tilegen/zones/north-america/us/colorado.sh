#!/bin/sh

# 
# Tiles for Colorado
# north-america/us/colorado 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/colorado.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-colorado/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/colorado.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-colorado/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/colorado.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-colorado/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/colorado.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-colorado/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-colorado/
find . -empty -type d -delete