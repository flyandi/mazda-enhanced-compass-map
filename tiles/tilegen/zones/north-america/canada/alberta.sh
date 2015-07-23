#!/bin/sh

# 
# Tiles for Alberta
# north-america/canada/alberta 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/alberta.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-alberta/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/alberta.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-alberta/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/alberta.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-alberta/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/alberta.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-alberta/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-alberta/
find . -empty -type d -delete