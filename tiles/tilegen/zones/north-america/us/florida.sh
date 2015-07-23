#!/bin/sh

# 
# Tiles for Florida
# north-america/us/florida 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

