#!/bin/sh

# 
# World Tiles
#

cd ../../base

./polytiles.py -b -180.0 -90.0 180.0 90.0 -s ../../tilestyles/mazda/mazda.xml -t ../../../output/world/ --zooms 0 6 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/world/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a world.7z .

# done
echo "[Done] Ready for upload."