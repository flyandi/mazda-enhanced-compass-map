#!/bin/sh

# 
# Tiles for Georgia
# north-america/us/georgia 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/georgia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-georgia/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/georgia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-georgia/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/georgia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-georgia/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/georgia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-georgia/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-georgia/
find . -empty -type d -delete