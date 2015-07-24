#!/bin/sh

# 
# Tiles for Montana
# north-america/us/montana 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/montana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-montana/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/montana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-montana/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/montana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-montana/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/montana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-montana/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-montana/
find . -empty -type d -delete