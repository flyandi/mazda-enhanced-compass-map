#!/bin/sh

# 
# Tiles for New Brunswick
# north-america/canada/new-brunswick 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/new-brunswick.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-new-brunswick/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/new-brunswick.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-new-brunswick/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/new-brunswick.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-new-brunswick/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/new-brunswick.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-new-brunswick/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-new-brunswick/
find . -empty -type d -delete