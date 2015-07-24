#!/bin/sh

# 
# Tiles for District Of Columbia
# north-america/us/district-of-columbia 
#

cd ../../../base

./polytiles.py -p ../poly/north-america/us/district-of-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-district-of-columbia/ --zooms 11 11 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/district-of-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-district-of-columbia/ --zooms 13 13 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/district-of-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-district-of-columbia/ --zooms 15 15 --delete-empty --custom-fonts ../../../fonts/
./polytiles.py -p ../poly/north-america/us/district-of-columbia.poly -s ../../tilestyles/mazda/mazda.xml -t ../../../output/north-america-us-district-of-columbia/ --zooms 17 17 --delete-empty --custom-fonts ../../../fonts/

# cleanup
echo "[Cleanup] This may take a while so hold tight."
cd ../../../output/north-america-us-district-of-columbia/
find . -empty -type d -delete

# packup
echo "[Packing] Please wait."
7z a north-america-us-district-of-columbia.7z .

# done
echo "[Done] Ready for upload."
