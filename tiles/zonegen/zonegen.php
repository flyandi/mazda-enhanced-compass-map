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

// includes
include "lib/functions.lib.php";

// Definitions
define("FILE_ZONES", "zones.json");
define("OUTPUT_PATH", "../tilegen/zones/");

define("SETTINGS", "_settings");

// pre
@mkdir(OUTPUT_PATH, 0777, true);


// class
class ZoneGen {

	/** 
	 * constructor
	 */

	public function __construct() {

		// Read zone file
		$this->sources = json_clean_decode(file_get_contents(FILE_ZONES));

		// process
		$this->__cycle();

	}

	/**
	 * ::cycle
	 */

	private function __cycle() {

		// process sources
		foreach($this->sources as $name => $source) {

			ConOut("Processing zone %s", $name);

			// process zones
			foreach($source->zones as $zone => $regions) {
				
				// process region
				foreach($regions as $region => $subregion) {

					switch(true) {

						// multiple regions
						case is_array($subregion): 

							// process subregions
							foreach($subregion as $id => $name) {

								$this->__process($source, $zone, $region, $name);

							}

							break;

						// single region
						default:
							$this->__process($source, $zone, $region, $subregion);
							break;

					}
				}
			}
		}
	}


	/**
	 * ::process
	 */

	private function __process($source, $zone, $region, $subregion) {

		// process settings
		$overwrite = isset($source->settings->overwriteExistingFiles) && $source->settings->overwriteExistingFiles === true;
		
		// start
		ConOut("Processing %s/%s/%s", $zone, $region, $subregion);

		// process subregion name
		$name = $subregion;

		// transform
		if(isset($source->filenames->transform)) {
			foreach(explode(",", $source->filenames->transform) as $transform) {

				switch($transform) {

					case "lowercase": $name = strtolower($name); break;
					case "spacedash": $name = str_replace(" ", "-", $name); break;

				}
			}
		}

		$defaults = array(
			"zone" => $zone,
			"region" => $region,
			"subregion" => $subregion,
			"name" => $name,	
		);

		// poly
		if(isset($source->process->poly) && $source->process->poly == true) {

			// build poly filename
			$fn = srep(@$source->filenames->poly, $defaults);
			$uri = suri($source->url, $zone, $region, $fn);

			// check output
			$path = srep(isset($source->output->poly) ? $source->output->poly : "poly/", $defaults);
			$output = ts($path).$fn;

			// create directory
			if(!is_dir($path)) @mkdir($path, 0777, true);

			if(!file_exists($output) || $overwrite) {

				// echo
				ConOut(" [Poly] %s => %s", $uri, $fn);

				// download 
				file_put_contents($output, file_get_contents($uri));
			}
		}

		// render
		if(isset($source->process->render) && $source->process->render == true) {

			// load render template
			$template = file_get_contents("templates/render.template");

			// replace values
			$template = srep($template, array_merge($defaults, array(

				"polyname" => srep(@$source->filenames->poly, $defaults)

			)));

			// create fn
			$fn = srep($source->filenames->render, $defaults);

			// create path
			$path = srep(isset($source->output->render) ? $source->output->render : "render/", $defaults);
			$output = ts($path).$fn;

			// create directory
			if(!is_dir($path)) @mkdir($path, 0777, true);

			// write output
			file_put_contents($output, $template);

			chmod($output, 0755);

			ConOut(" [Render] => %s", $output);

		}
	}
}


/**
 * run
 */

$zg = new ZoneGen();
