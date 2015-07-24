#!/bin/sh

# 
# Tiles for Saskatchewan
# north-america/canada/saskatchewan 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/saskatchewan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-saskatchewan/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/saskatchewan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-saskatchewan/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/saskatchewan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-saskatchewan/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/saskatchewan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-saskatchewan/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-saskatchewan/
find . -empty -type d -delete