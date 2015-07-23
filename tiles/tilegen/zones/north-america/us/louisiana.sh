#!/bin/sh

# 
# Tiles for Louisiana
# north-america/us/louisiana 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/louisiana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-louisiana/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/louisiana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-louisiana/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/louisiana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-louisiana/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/louisiana.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-louisiana/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

