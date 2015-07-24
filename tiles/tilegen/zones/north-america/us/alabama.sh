#!/bin/sh

# 
# Tiles for Alabama
# north-america/us/alabama 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/alabama.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alabama/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alabama.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alabama/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alabama.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alabama/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/alabama.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-alabama/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-alabama/
find . -empty -type d -delete