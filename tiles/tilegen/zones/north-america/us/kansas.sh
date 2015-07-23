#!/bin/sh

# 
# Tiles for Kansas
# north-america/us/kansas 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/kansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kansas/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kansas/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kansas/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kansas.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kansas/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

