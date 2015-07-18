<?php
/**
 * ZoneGen
 * Enhanced Compass for Mazda Connect Infotainment
 * 
 * This creates optimized bounding box areas for the tile generation
 *
 * Written by Andreas Schwarz (http://github.com/flyandi/mazda-enhanced-compass)
 * Copyright (c) 2015. All rights reserved.
 * 
 * WARNING: The installation of this application requires modifications to your Mazda Connect system.
 * If you don't feel comfortable performing these changes, please do not attempt to install this. You might
 * be ending up with an unusuable system that requires reset by your Dealer. You were warned!
 *
 * This program is free software: you can redistribute it and/or modify it under the terms of the 
 * GNU General Public License as published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even 
 * the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
 * License for more details.
 * 
 * You should have received a copy of the GNU General Public License along with this program. 
 * If not, see http://www.gnu.org/licenses/
 */

// Includes
include("lib/shp.inc.php");
include("lib/functions.inc.php");

// Definitions
define("FILE_ZONES", "zones/zones.json");
define("FILE_ZONE", "zone.json");

// Read zones
$zones = json_decode(file_get_contents(FILE_ZONES));

// Process each zone
if($zones != null && isset($zones->zones)) 
	foreach($zones->zones as $zoneConfig) {
			
		// read zone file
		$zone = json_decode(file_get_contents(sprintf("zones/%s/%s", $zoneConfig->path, FILE_ZONE)));

		if($zone != null && !$zone->disabled) {

			// load shapefile
			$shapeFile = new ShapeFile(sprintf("zones/%s/%s", $zoneConfig->path, $zone->source), array(
				"noparts" => false
			));

			// process records
			$record = $shapeFile->getNext();

			$dbfData = $record->getDbfData();

			var_dump($dbfData);

		}
	}
