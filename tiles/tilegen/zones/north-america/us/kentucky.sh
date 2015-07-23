#!/bin/sh

# 
# Tiles for Kentucky
# north-america/us/kentucky 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

