#!/bin/sh

# 
# Tiles for Northwest Territories
# north-america/canada/northwest-territories 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/northwest-territories.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-northwest-territories/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/northwest-territories.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-northwest-territories/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/northwest-territories.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-northwest-territories/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/northwest-territories.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-northwest-territories/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-northwest-territories/
find . -empty -type d -delete