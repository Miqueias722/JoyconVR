#!/usr/bin/env bash
# Cross-compiles the driver and the calibration tool for 64-bit Windows with MinGW-w64.
# Usage: ./build_mingw.sh <deps dir containing hidapi/ and json/>
set -euo pipefail
DEPS=${1:-../deps}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/build/win64"
mkdir -p "$OUT/obj"

CXX=x86_64-w64-mingw32-g++
CC=x86_64-w64-mingw32-gcc
COMMON="-O2 -DWIN32_LEAN_AND_MEAN -DNOMINMAX -D_USE_MATH_DEFINES -DHID_API_NO_EXPORT_DEFINE"
INC="-I$HERE/include -I$HERE/third_party -I$DEPS/hidapi/hidapi -I$DEPS/json/single_include"
STATIC="-static -static-libgcc -static-libstdc++"
LIBS="-lcfgmgr32 -lsetupapi -lws2_32"

$CC $COMMON -I"$DEPS/hidapi/hidapi" -c "$DEPS/hidapi/windows/hid.c" -o "$OUT/obj/hid.o"

for f in JoyconHid JoyconConfig JoyconCalibration MotionTracker driver_main; do
  $CXX -std=c++17 $COMMON $INC -Wall -Wno-unused-parameter -c "$HERE/src/$f.cpp" -o "$OUT/obj/$f.o"
done

$CXX -shared -o "$OUT/driver_joyconvr.dll" "$OUT"/obj/{JoyconHid,JoyconConfig,JoyconCalibration,MotionTracker,driver_main,hid}.o \
  $STATIC $LIBS

$CXX -std=c++17 $COMMON $INC -o "$OUT/joycon_calibrate.exe" "$HERE/tools/joycon_calibrate.cpp" "$OUT/obj/hid.o" $STATIC $LIBS

x86_64-w64-mingw32-strip "$OUT/driver_joyconvr.dll" "$OUT/joycon_calibrate.exe"
echo "built: $OUT"
