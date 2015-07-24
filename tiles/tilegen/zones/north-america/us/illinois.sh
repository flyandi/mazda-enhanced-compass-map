#!/bin/sh

# 
# Tiles for Illinois
# north-america/us/illinois 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/illinois.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-illinois/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/illinois.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-illinois/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/illinois.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-illinois/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/illinois.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-illinois/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-illinois/
find . -empty -type d -delete