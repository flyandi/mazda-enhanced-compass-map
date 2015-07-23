#!/bin/sh

# 
# Tiles for Connecticut
# north-america/us/connecticut 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/connecticut.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-connecticut/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/connecticut.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-connecticut/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/connecticut.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-connecticut/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/connecticut.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-connecticut/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

