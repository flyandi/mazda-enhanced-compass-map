#!/bin/sh

# 
# Tiles for Delaware
# north-america/us/delaware 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/delaware.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-delaware/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/delaware.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-delaware/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/delaware.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-delaware/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/delaware.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-delaware/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

