#!/bin/sh

# 
# Tiles for Wisconsin
# north-america/us/wisconsin 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/wisconsin.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-wisconsin/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-wisconsin/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-wisconsin.7z .

# done
echo "[Done] Ready for upload."
