#!/bin/sh

# 
# Tiles for Utah
# north-america/us/utah 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/utah.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-utah/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/utah.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-utah/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/utah.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-utah/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/utah.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-utah/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/
