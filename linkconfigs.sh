#!/bin/bash
# linkdirs.sh
# Usage: ./linkdirs.sh <source_dir> <dest_dir>

set -e

if [ $# -ne 2 ]; then
  echo "Usage: $0 <source_dir> <dest_dir>"
  exit 1
fi

SRC="$1"
DST="$2"

# Make sure both dirs exist
if [ ! -d "$SRC" ]; then
  echo "Source directory '$SRC' does not exist"
  exit 1
fi

mkdir -p "$DST"

for dir in "$SRC"/*/; do
  # skip if no subdirs
  [ -d "$dir" ] || continue

  dirname=$(basename "$dir")
  target="$DST/$dirname"

  if [ -e "$target" ]; then
    echo "Skipping: $target already exists"
  else
    ln -s "$dir" "$target"
    echo "Linked: $dir -> $target"
  fi
done
