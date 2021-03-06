#!/bin/sh

# 
# Tiles for South Carolina
# north-america/us/south-carolina 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/south-carolina.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-south-carolina/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-south-carolina/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-south-carolina.7z .

# done
echo "[Done] Ready for upload."
