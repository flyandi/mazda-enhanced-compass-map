#!/bin/sh

# 
# Tiles for Iowa
# north-america/us/iowa 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/iowa.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-iowa/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/iowa.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-iowa/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/iowa.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-iowa/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/iowa.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-iowa/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

