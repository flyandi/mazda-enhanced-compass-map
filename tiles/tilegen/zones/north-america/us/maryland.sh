#!/bin/sh

# 
# Tiles for Maryland
# north-america/us/maryland 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/maryland.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maryland/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maryland.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maryland/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maryland.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maryland/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/maryland.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-maryland/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

