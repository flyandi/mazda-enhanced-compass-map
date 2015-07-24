#!/bin/sh

# 
# Tiles for West Virginia
# north-america/us/west-virginia 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/west-virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-west-virginia/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/west-virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-west-virginia/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/west-virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-west-virginia/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/west-virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-west-virginia/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-west-virginia/
find . -empty -type d -delete