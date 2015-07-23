#!/bin/sh

# 
# Tiles for New York
# north-america/us/new-york 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/new-york.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-york/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-york.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-york/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-york.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-york/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/new-york.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-new-york/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-new-york/
find . -empty -type d -delete