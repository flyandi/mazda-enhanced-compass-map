#!/bin/sh

# 
# Tiles for Florida
# north-america/us/florida 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/florida.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-florida/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-florida/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-florida.7z .

# done
echo "[Done] Ready for upload."
