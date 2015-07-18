<?php

# http://stackoverflow.com/questions/5464919/php-nearest-value-from-an-array
function getNearest($search, $arr, $index = 1) {
   $closest = null;
   $closestPoint = null;
   foreach($arr as $item) {
      if($closest == null || abs($search - $closest) > abs($item[$index] - $search)) {
      	if($search != $item[$index]) {
        	$closest = $item[$index];
        	$closestPoint = $item;
        }
      }
   }
   return $closestPoint;
}


