#!/bin/sh

# 
# Tiles for Hawaii
# north-america/us/hawaii 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/hawaii.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-hawaii/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/hawaii.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-hawaii/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/hawaii.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-hawaii/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/hawaii.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-hawaii/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-hawaii/
find . -empty -type d -delete