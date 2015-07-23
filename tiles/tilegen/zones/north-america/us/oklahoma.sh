#!/bin/sh

# 
# Tiles for Oklahoma
# north-america/us/oklahoma 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/oklahoma.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oklahoma/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oklahoma.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oklahoma/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oklahoma.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oklahoma/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/oklahoma.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-oklahoma/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

