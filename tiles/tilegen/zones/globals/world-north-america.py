#!/bin/sh

# 
# World Tiles and North America Tiles
#

cd ../../base

./polytiles.py -b -180.0 -90.0 180.0 90.0 -s ../../tilestyles/mazda/mazda.xml -t ../../../output/world-north-america/ --zooms 0 6 --delete-empty --custom-fonts ../../../fonts/

./polytiles.py -p ../poly/globals/north-america.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/world-north-america/ --zooms 0 10 --delete-empty --custom-fonts ../../../fonts/


# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/world-north-america/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a world-north-america.7z .

# done
echo "[Done] Ready for upload."