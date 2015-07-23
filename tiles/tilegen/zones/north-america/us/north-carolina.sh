#!/bin/sh

# 
# Tiles for North Carolina
# north-america/us/north-carolina 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/north-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-north-carolina/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/north-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-north-carolina/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/north-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-north-carolina/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/north-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-north-carolina/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

