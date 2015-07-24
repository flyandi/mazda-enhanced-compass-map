#!/bin/sh

# 
# Tiles for Rhode Island
# north-america/us/rhode-island 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/rhode-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-rhode-island/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/rhode-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-rhode-island/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/rhode-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-rhode-island/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/rhode-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-rhode-island/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-rhode-island/
find . -empty -type d -delete