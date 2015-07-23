#!/bin/sh

# 
# Tiles for Oregon
# north-america/us/oregon 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/oregon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oregon/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oregon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oregon/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oregon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oregon/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oregon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oregon/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/
