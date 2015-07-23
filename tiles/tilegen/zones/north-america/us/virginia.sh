#!/bin/sh

# 
# Tiles for Virginia
# north-america/us/virginia 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virginia/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virginia/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virginia/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virginia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virginia/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

