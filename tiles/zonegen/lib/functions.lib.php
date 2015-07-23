<?php
/**
 * Helpers
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


// (http://php.net/manual/en/function.json-decode.php)
function json_clean_decode($json, $assoc = false, $depth = 512, $options = 0) {
    // search and remove comments like /* */ and //
    $json = preg_replace("#(/\*([^*]|[\r\n]|(\*+([^*/]|[\r\n])))*\*+/)|([\s\t]//.*)|(^//.*)#", '', $json);
    
    if(version_compare(phpversion(), '5.4.0', '>=')) {
        $json = json_decode($json, $assoc, $depth, $options);
    }
    elseif(version_compare(phpversion(), '5.3.0', '>=')) {
        $json = json_decode($json, $assoc, $depth);
    }
    else {
        $json = json_decode($json, $assoc);
    }

    return $json;
}

// (ConOut) from mg-framework
function ConOut() {
	$a = func_get_args();
	echo vsprintf($a[0], array_slice($a, 1))."\n";
	flush();
}

// (sreps)
function sreps($key, $value, $source) {
	return str_replace(sprintf("{%s}", $key), $value, $source);
}

function srep($source, $values) {
	foreach($values as $key=>$value) $source = sreps($key, $value, $source);
	return $source;
}

function suri($url, $zone, $region, $subregion) { 
	return srep($url, array(
		"zone" => $zone,
		"region" => $region,
		"subregion" => $subregion,
	));
}

function ts($path) {
	if(substr($path, -1) != "/") $path .= "/";
	return $path;
}

