#!/bin/sh

# 
# Tiles for Washington
# north-america/us/washington 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/washington.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-washington/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/washington.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-washington/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/washington.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-washington/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/washington.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-washington/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-washington/
find . -empty -type d -delete