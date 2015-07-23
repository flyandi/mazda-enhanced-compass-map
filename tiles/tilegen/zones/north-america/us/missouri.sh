#!/bin/sh

# 
# Tiles for Missouri
# north-america/us/missouri 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/missouri.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-missouri/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/missouri.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-missouri/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/missouri.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-missouri/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/missouri.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-missouri/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

