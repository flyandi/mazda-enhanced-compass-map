#!/bin/sh

# 
# Tiles for Nevada
# north-america/us/nevada 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/nevada.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-nevada/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/nevada.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-nevada/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/nevada.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-nevada/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/nevada.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-nevada/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-nevada/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-nevada.7z .

# done
echo "[Done] Ready for upload."
