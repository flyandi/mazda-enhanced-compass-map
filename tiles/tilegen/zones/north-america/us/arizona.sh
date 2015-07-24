#!/bin/sh

# 
# Tiles for Arizona
# north-america/us/arizona 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/arizona.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arizona/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arizona.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arizona/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arizona.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arizona/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/arizona.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-arizona/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-arizona/
find . -empty -type d -delete