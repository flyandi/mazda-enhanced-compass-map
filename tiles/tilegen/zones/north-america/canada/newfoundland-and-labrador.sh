#!/bin/sh

# 
# Tiles for Newfoundland and Labrador
# north-america/canada/newfoundland-and-labrador 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/newfoundland-and-labrador.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-newfoundland-and-labrador/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/newfoundland-and-labrador.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-newfoundland-and-labrador/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/newfoundland-and-labrador.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-newfoundland-and-labrador/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/newfoundland-and-labrador.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-newfoundland-and-labrador/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-newfoundland-and-labrador/
find . -empty -type d -delete