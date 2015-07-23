#!/bin/sh

# 
# Tiles for California
# north-america/us/california 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/california.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-california/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/california.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-california/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/california.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-california/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/california.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-california/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

