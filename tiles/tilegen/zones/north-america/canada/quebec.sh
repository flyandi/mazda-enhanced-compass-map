#!/bin/sh

# 
# Tiles for Quebec
# north-america/canada/quebec 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/canada/quebec.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-canada-quebec/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-canada-quebec/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-canada-quebec.7z .

# done
echo "[Done] Ready for upload."
