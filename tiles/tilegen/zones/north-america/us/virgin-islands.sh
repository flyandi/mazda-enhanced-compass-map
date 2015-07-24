#!/bin/sh

# 
# Tiles for Virgin Islands
# north-america/us/virgin-islands 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/virgin-islands.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virgin-islands/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virgin-islands.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virgin-islands/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virgin-islands.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virgin-islands/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/virgin-islands.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-virgin-islands/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-virgin-islands/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-virgin-islands.7z .

# done
echo "[Done] Ready for upload."
