#!/bin/sh

# 
# Tiles for Tennessee
# north-america/us/tennessee 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/tennessee.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-tennessee/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/tennessee.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-tennessee/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/tennessee.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-tennessee/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/tennessee.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-tennessee/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

