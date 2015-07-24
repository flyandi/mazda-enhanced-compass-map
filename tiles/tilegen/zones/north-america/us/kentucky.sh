#!/bin/sh

# 
# Tiles for Kentucky
# north-america/us/kentucky 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/kentucky.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-kentucky/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-kentucky/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-kentucky.7z .

# done
echo "[Done] Ready for upload."
