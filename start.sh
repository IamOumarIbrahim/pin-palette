#!/bin/sh
# Start Pin Palette. Optional argument: an image or a folder of reference images.
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || exec ./install.sh
exec .venv/bin/python app.py "$@"
