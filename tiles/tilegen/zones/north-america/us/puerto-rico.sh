#!/bin/sh

# 
# Tiles for Puerto Rico
# north-america/us/puerto-rico 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/puerto-rico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-puerto-rico/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/puerto-rico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-puerto-rico/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/puerto-rico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-puerto-rico/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/puerto-rico.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-puerto-rico/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-puerto-rico/
find . -empty -type d -delete