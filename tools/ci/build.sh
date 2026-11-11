#!/bin/bash
# CI build of the flash package from inputs already in $TB_WORK (the outputs of check, backup, pull and import:
# device/latest.json, backup/<dir>, lenovo/<dir>, xiaomi/<version>; optional overlay/*.apk).
#   TB_WORK           work directory with those inputs (build/ and out/ are recreated)
#   TB_AVB_DIR        AOSP avbtool.py + testkey_rsa4096.pem / testkey_rsa2048.pem (linked to tools/avb)
#   TB_MKBOOTIMG_DIR  AOSP mkbootimg.py / unpack_bootimg.py (linked to tools/mkbootimg)
# Runs vendor, framework (with / without Find Device), bootchain twice and package; every step does its own checks
# and stops with a non-zero exit on any mismatch. Nothing is written to a device.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${TB_WORK:?set TB_WORK}"
export TB_WORK
[ -n "${TB_AVB_DIR:-}" ] && ln -sfn "$TB_AVB_DIR" tools/avb
[ -n "${TB_MKBOOTIMG_DIR:-}" ] && ln -sfn "$TB_MKBOOTIMG_DIR" tools/mkbootimg
rm -rf "$TB_WORK/build" "$TB_WORK/out"
T() { echo "::group::tb710fu.py $*"; python3 -u tools/tb710fu.py "$@"; echo "::endgroup::"; }
T vendor
T framework
T framework --no-finddevice
F="$TB_WORK/build/framework"
find_dir=$(ls -d "$F"/*/ | grep -v -- '-nofind/' | head -1)
nofind_dir=$(ls -d "$F"/*-nofind/ | head -1)
T bootchain --framework-dir "${find_dir%/}"
T bootchain --framework-dir "${nofind_dir%/}"
T package
# free the intermediate images; the package in out/ is what is kept
rm -rf "$TB_WORK/build/framework" "$TB_WORK/build/bootchain"
ls -la "$TB_WORK"/out/*/
