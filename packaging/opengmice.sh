#!/bin/sh
# Flatpak launcher: the package lives in /app/share/opengmice.
export PYTHONPATH="/app/share/opengmice${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m opengmice "$@"
