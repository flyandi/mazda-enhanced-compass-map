#!/bin/sh

# 
# Tiles for Alaska
# north-america/us/alaska 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/alaska.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alaska/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alaska.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alaska/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alaska.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alaska/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alaska.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alaska/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-alaska/
find . -empty -type d -delete