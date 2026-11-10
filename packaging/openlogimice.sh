#!/bin/sh
# Flatpak launcher: the package lives in /app/share/openlogimice.
export PYTHONPATH="/app/share/openlogimice${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m openlogimice "$@"
