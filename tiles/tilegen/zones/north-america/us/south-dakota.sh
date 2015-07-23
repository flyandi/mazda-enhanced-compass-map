#!/bin/sh

# 
# Tiles for South Dakota
# north-america/us/south-dakota 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/south-dakota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-dakota/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-dakota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-dakota/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-dakota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-dakota/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-dakota.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-dakota/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

