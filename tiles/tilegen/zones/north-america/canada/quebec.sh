#!/bin/sh

# 
# Tiles for Quebec
# north-america/canada/quebec 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

