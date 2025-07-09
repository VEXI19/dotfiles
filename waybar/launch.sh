#!/bin/bash

echo "Reloading waybar..."

(killall waybar || true) && waybar &
