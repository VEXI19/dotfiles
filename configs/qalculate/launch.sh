#!/usr/bin/env bash

set -euo pipefail

config_dir=$(dirname "$(readlink -f "$0")")

# Keep the custom GTK theme private to Qalculate. The regular XDG data paths
# remain available so application icons and desktop integration still work.
export XDG_DATA_DIRS="$config_dir/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export GTK_THEME=WofiCalculator

exec qalculate-gtk "$@"
