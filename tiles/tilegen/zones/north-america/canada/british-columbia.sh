#!/bin/sh

# 
# Tiles for British Columbia
# north-america/canada/british-columbia 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/british-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-british-columbia/ --zooms 0 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/british-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-british-columbia/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/british-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-british-columbia/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/british-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-british-columbia/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-british-columbia/
find . -empty -type d -delete