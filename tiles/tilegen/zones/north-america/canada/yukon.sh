#!/bin/sh

# 
# Tiles for Yukon
# north-america/canada/yukon 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/yukon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-yukon/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/yukon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-yukon/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/yukon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-yukon/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/yukon.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-yukon/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-yukon/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-canada-yukon.7z .

# done
echo "[Done] Ready for upload."
