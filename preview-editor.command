#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
python3 tools/gear_editor.py --show
