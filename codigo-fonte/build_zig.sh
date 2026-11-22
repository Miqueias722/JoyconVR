#!/usr/bin/env bash
# Compila o driver e a ferramenta de calibracao para Windows 64 bits usando o Zig
# (pip install ziglang). Nao precisa de Visual Studio nem MinGW.
# Uso: ./build_zig.sh <pasta deps com hidapi/ e json/> <python com ziglang>
set -euo pipefail
DEPS=${1:-../../deps}
PY=${2:-python}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/build/win64"
mkdir -p "$OUT/obj"

ZCC="$PY -m ziglang cc -target x86_64-windows-gnu"
ZCXX="$PY -m ziglang c++ -target x86_64-windows-gnu"
COMMON="-O2 -DWIN32_LEAN_AND_MEAN -DNOMINMAX -D_USE_MATH_DEFINES -DHID_API_NO_EXPORT_DEFINE"
INC="-I$HERE/include -I$HERE/third_party -I$DEPS/hidapi/hidapi -I$DEPS/json/single_include"
LIBS="-lcfgmgr32 -lsetupapi -lws2_32 -luser32"

$ZCC -O2 -DHID_API_NO_EXPORT_DEFINE -I"$DEPS/hidapi/hidapi" -c "$DEPS/hidapi/windows/hid.c" -o "$OUT/obj/hid.o"

for f in JoyconHid JoyconConfig JoyconCalibration MotionTracker driver_main; do
  $ZCXX -std=c++17 $COMMON $INC -Wall -Wno-unused-parameter -c "$HERE/src/$f.cpp" -o "$OUT/obj/$f.o"
done

$ZCXX -shared -s -o "$OUT/driver_joyconvr.dll" "$OUT"/obj/{JoyconHid,JoyconConfig,JoyconCalibration,MotionTracker,driver_main,hid}.o $LIBS
$ZCXX -std=c++17 -s $COMMON $INC -o "$OUT/joycon_calibrate.exe" "$HERE/tools/joycon_calibrate.cpp" "$OUT/obj/hid.o" $LIBS
echo "built: $OUT"
