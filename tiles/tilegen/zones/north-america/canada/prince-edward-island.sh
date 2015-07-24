#!/bin/sh

# 
# Tiles for Prince Edward Island
# north-america/canada/prince-edward-island 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/prince-edward-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-prince-edward-island/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/prince-edward-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-prince-edward-island/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/prince-edward-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-prince-edward-island/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/prince-edward-island.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-prince-edward-island/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-prince-edward-island/
find . -empty -type d -delete