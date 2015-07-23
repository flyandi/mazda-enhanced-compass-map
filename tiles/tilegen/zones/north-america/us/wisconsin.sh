#!/bin/sh

# 
# Tiles for Wisconsin
# north-america/us/wisconsin 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

