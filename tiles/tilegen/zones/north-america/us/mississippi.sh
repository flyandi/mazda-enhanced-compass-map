#!/bin/sh

# 
# Tiles for Mississippi
# north-america/us/mississippi 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/mississippi.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-mississippi/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/mississippi.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-mississippi/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/mississippi.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-mississippi/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/mississippi.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-mississippi/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-mississippi/
find . -empty -type d -delete