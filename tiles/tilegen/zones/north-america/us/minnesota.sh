#!/bin/sh

# 
# Tiles for Minnesota
# north-america/us/minnesota 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/minnesota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-minnesota/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/minnesota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-minnesota/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/minnesota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-minnesota/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/minnesota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-minnesota/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-minnesota/
find . -empty -type d -delete