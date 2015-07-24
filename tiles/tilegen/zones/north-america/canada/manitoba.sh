#!/bin/sh

# 
# Tiles for Manitoba
# north-america/canada/manitoba 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/manitoba.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-manitoba/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/manitoba.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-manitoba/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/manitoba.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-manitoba/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/manitoba.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-manitoba/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-manitoba/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-canada-manitoba.7z .

# done
echo "[Done] Ready for upload."
