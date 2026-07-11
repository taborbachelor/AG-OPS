#!/usr/bin/env bash
# Downloads the pre-built ArduPlane SITL binary + cygwin DLLs for Windows.
# These are large binaries and are NOT committed to git; run this to fetch them.
set -e
cd "$(dirname "$0")"

BASE="https://firmware.ardupilot.org/Tools/MissionPlanner/sitl"
FILES="ArduPlane.elf cygatomic-1.dll cyggcc_s-1.dll cyggcc_s-seh-1.dll cyggomp-1.dll \
cygiconv-2.dll cygintl-8.dll cygquadmath-0.dll cygssp-0.dll cygstdc++-6.dll cygwin1.dll"

for f in $FILES; do
  echo "Downloading $f ..."
  curl -s -o "$f" "$BASE/$f"
done

# The .elf is actually a Windows PE executable; make a runnable .exe copy.
cp ArduPlane.elf ArduPlane.exe
echo "Done. Run ./run_sitl.bat to start the simulator."
