<?php
/**
 * ZoneGen
 * Enhanced Compass for Mazda Connect Infotainment
 * 
 * This creates the zones for tile rendering and downloads the necessary resources.
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

define("TEMPLATE", "templates/tiles.template");
define("OUTPUT_PATH", "../tilegen/zones/");

// pre
@mkdir(OUTPUT_PATH, 0777, true);

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
			while($record = $shapeFile->getNext()) {

				// check mapping
				if(isset($zone->mapping)) {

					// read data
					$dbfData = $record->getDbfData();

					// process mapping
					$data = (object) array(
						"id" => trim(@$dbfData[$zone->mapping->id]),
						"name" => trim(@$dbfData[$zone->mapping->name]),
					);

					// read shape
					$shpData = $record->getShpData();
					$shapes = array();
					$boxes = array();
					$json = array();
					$bbox = array(false, false, false, false);


					// process shape
					if(isset($shpData['numparts'])) {

						// some info
						echo sprintf("[ZONE] %s: %s (%s) .. ", $zone->name, $data->id, $data->name);

						// cycle through parts
						foreach($shpData['parts'] as $part) {

							// get coordinates
							$coords = array();
							foreach ($part['points'] as $point) {
								$coords[] = array(
									round($point['x'],5),
									round($point['y'],5)
								);
							}

							// sort by y axis
							usort($coords, function($a, $b) {
								return $a[1] < $b[1] ? -1 : 1;
							});


							// create boxes
							$partId = strtolower(sprintf("%s-%s",
								$data->id,
								str_replace(" ", "-", $data->name)
							));

							$renderParams = array(
								"    render_tiles({BB}, mapfile, tile_dir, 0, 11, \"{NAME}\")",
								"    render_tiles({BB}, mapfile, tile_dir, 13, 13, \"{NAME}\")",
								"    render_tiles({BB}, mapfile, tile_dir, 15, 15, \"{NAME}\")",
								"    render_tiles({BB}, mapfile, tile_dir, 17, 17, \"{NAME}\")",
							);

							foreach($coords as $a) {

								switch(true) {

									case $zone->box:

										if($a[0] < $bbox[0] || $bbox[0] === false) $bbox[0] = $a[0];
										if($a[1] < $bbox[1] || $bbox[1] === false) $bbox[1] = $a[1];

										if($a[0] > $bbox[2] || $bbox[2] === false) $bbox[2] = $a[0];
										if($a[1] > $bbox[3] || $bbox[3] === false) $bbox[3] = $a[1];


										$boxes = array();

										$params = array(
											"BB" => sprintf("(%s)", implode(",", $bbox)),

											"JSON" => array(
												array($bbox[0], $bbox[1]),
												array($bbox[2], $bbox[1]),

												//array($bbox[2], $bbox[3]),
											),

											"NAME" => $partId,

										);

										break;


									default:
							
										// get nearest and farthest
										$b = getNearest($a[0], $coords);
										$c = getFarthest($b[1], $coords);

										$params = array(
											"BB" => sprintf("(%s,%s,%s,%s)",
												$a[0], $a[1], $c[0], $b[1]
											),

											"JSON" => array(
												array($a[0], $a[1]), 
												array($c[0], $b[1]),
											),

											"NAME" => $partId,
										);


										break;
								}

								foreach($renderParams as $cmd) {

									foreach($params as $key=>$value) {
										if(!is_array($value))
											$cmd = str_replace(sprintf("{%s}", $key), $value, $cmd);
									}

									$boxes[] = $cmd;
								}

								$json[] = array(
									"type" => "Feature",
									"geometry" => array(			
										"type" => "LineString",
										"coordinates" => $params["JSON"],
									)
								);

							}
						}

						// write template
						$template = file_get_contents(TEMPLATE);
						
						foreach(array(
							"ZONEID" => $zone->name,
							"PARTID" => $data->id,
							"PARTNAME" => $data->name,
							"CONTENT" => implode("\n", $boxes)
						) as $key=>$value) {
							$template = str_replace(sprintf("{%s}", $key), $value, $template);
						}

						$path = strtolower(sprintf("%s%s/", OUTPUT_PATH, $zone->name));
						$fn = sprintf("%s%s.py", $path, $partId);
						$fjn = sprintf("%s%s.json", $path, $partId);
						@mkdir($path, 0777, true);

						file_put_contents($fn, $template);
						chmod($fn, 0777);

						// write geojson
						file_put_contents($fjn, json_encode(array(
							"type"=> "FeatureCollection",
							"crs"=> array(
								"type" => "name",
								"properties" => array(
									"name" => "EPSG:4326"
								)
							),
							"features" => $json
						)));
						echo " Done.\n";					
					}
				}
			}
		}
	}


