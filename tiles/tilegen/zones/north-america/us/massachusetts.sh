#!/bin/sh

# 
# Tiles for Massachusetts
# north-america/us/massachusetts 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/massachusetts.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-massachusetts/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/massachusetts.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-massachusetts/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/massachusetts.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-massachusetts/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/massachusetts.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-massachusetts/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-massachusetts/
find . -empty -type d -delete