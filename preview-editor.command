#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if [ -x "python/bin/python3" ]; then
  exec python/bin/python3 -m tools.desktop_view --standalone --show
fi
exec python3 -m tools.desktop_view --standalone --show
