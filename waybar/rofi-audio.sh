#!/bin/bash

# Get list of sinks (output devices)
devices=$(wpctl status | awk '/Sinks:/,/Sources:/' | grep -E '^\s+\*?\s*[0-9]+' | sed 's/^\s*\*?\s*//' | awk '{$1=$1};1')

# Let user select one with rofi
selected=$(echo "$devices" | rofi -dmenu -p "Select Output:")

# Extract ID (first number)
id=$(echo "$selected" | awk '{print $1}')

# Set as default if valid
[ -n "$id" ] && wpctl set-default "$id"

