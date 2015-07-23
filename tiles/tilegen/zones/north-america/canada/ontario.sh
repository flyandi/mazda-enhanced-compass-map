#!/bin/sh

# 
# Tiles for Ontario
# north-america/canada/ontario 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/ontario.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-ontario/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/ontario.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-ontario/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/ontario.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-ontario/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/ontario.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-ontario/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

