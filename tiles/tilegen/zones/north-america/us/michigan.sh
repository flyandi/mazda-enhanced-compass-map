#!/bin/sh

# 
# Tiles for Michigan
# north-america/us/michigan 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/michigan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-michigan/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/michigan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-michigan/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/michigan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-michigan/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/michigan.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-michigan/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-michigan/
find . -empty -type d -delete