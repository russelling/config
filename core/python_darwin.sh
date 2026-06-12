#!/bin/bash
# Find the Python bundled with Shotgun.app, regardless of version
PYTHON=$(find /Applications/Shotgun.app/Contents/Resources/Python3/bin -name "python3.[0-9]*" ! -name "*config*" ! -name "*intel64*" ! -name "*-config" | sort -V | tail -1)
exec "$PYTHON" "$@"
