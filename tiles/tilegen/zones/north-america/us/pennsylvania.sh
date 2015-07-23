#!/bin/sh

# 
# Tiles for Pennsylvania
# north-america/us/pennsylvania 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/pennsylvania.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-pennsylvania/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/pennsylvania.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-pennsylvania/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/pennsylvania.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-pennsylvania/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/pennsylvania.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-pennsylvania/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

