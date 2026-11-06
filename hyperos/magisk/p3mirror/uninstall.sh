#!/system/bin/sh
# back to colour-managed display, previous saturation and colour matrix
S=/data/local/tmp/tb710fu_p3mirror.prev
[ -f $S ] || exit 0
service call SurfaceFlinger 1023 i32 "$(sed -n 1p $S)" i32 0 >/dev/null
service call SurfaceFlinger 1022 f "$(sed -n 2p $S)" >/dev/null
c0=$(sed -n 3p $S)
if [ -n "$c0" ] && [ "$c0" != id ]; then a="i32 1"; for v in $c0; do a="$a f $v"; done; service call SurfaceFlinger 1015 $a >/dev/null
else service call SurfaceFlinger 1015 i32 0 >/dev/null; fi
rm -f $S
