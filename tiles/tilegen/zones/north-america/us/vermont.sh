#!/bin/sh

# 
# Tiles for Vermont
# north-america/us/vermont 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/vermont.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-vermont/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/vermont.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-vermont/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/vermont.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-vermont/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/vermont.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-vermont/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-vermont/
find . -empty -type d -delete