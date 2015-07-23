#!/bin/sh

# 
# Tiles for South Carolina
# north-america/us/south-carolina 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

