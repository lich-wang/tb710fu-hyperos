#!/system/bin/sh
# TB710FU: P3 colour for Xiaomi Interconnect PC mirroring.
#
# The Mac (小米互联服务) streams its "Xiaomi Pad" virtual display as 8-bit video tagged BT.709, but
# encodes it in whatever colour profile that display has. With Display P3 assigned on the Mac the
# stream carries P3 values, so the tablet has to show them without converting BT.709 -> panel.
#
# What was tried (2026-10-23):
#  - SurfaceFlinger managed + forced DISPLAY_P3 (HWC then uses the factory P3 calibration): no good,
#    the HWC converts the BT.709 video layer to P3 just like GPU composition does (A/B identical).
#  - SurfaceFlinger unmanaged (debug transaction 1023 = 1 -> ColorMode::NATIVE): values reach the
#    panel unchanged, but no calibration at all ("gamut 255 gamma 255" in the HWC dump). The tianma
#    NT36532 panel's native white is ~9 % too blue and its green/blue are a bit wider than P3: see the
#    factory 3D LUT /vendor/etc/display/panel_topaz_nt36532_tianma_dsc_vid_C3D_cali_p3.txt.
# So while mirroring: unmanaged + a 3x3 colour matrix (transaction 1015) fitted to that LUT in
# gamma-encoded space (the NATIVE pipeline has no IGC, so the matrix acts on encoded values), with
# the neutral axis weighted x20 so whites/greys match the normal calibrated mode (the P3 and sRGB
# factory LUTs have an identical neutral axis): LUT error mean 12.4/255 -> 4.5/255, greys 1.05/255.
# SurfaceFlinger's final matrix = client matrix (1015) x global saturation (1022) x daltonizer, and
# only the product is visible in dumpsys. HyperOS sets the global saturation to 1.3 (its colour
# mode), so on entry the two are separated (client matrix briefly set to identity -> what remains is
# the saturation), then saturation 1.0 + calibration matrix are used; both are restored on exit.
ACT="com.xiaomi.mirror/.sinkpc.SinkPCActivity"
STATE=/data/local/tmp/tb710fu_p3mirror.prev   # lines: colour setting, saturation, client matrix (survive restarts)
MD=${0%/*}; MATH="$MD/matmath.awk"
ID="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
# column-major mat4 for transaction 1015 (column c = output R,G,B for input channel c)
CAL="0.95328 0.00023 -0.00618 0 0.03804 0.99615 -0.01828 0 0.03331 0.00674 0.94494 0 0 0 0 1"
log() { /system/bin/log -t tb710fu_p3mirror "$*"; }
sf_setting() {   # 0 managed, 1 unmanaged, 2 enhanced
  case "$(dumpsys SurfaceFlinger | grep -m1 'DisplayColorSetting:')" in
    *Unmanaged*) echo 1;; *Enhanced*) echo 2;; *) echo 0;;
  esac
}
sf_set() { service call SurfaceFlinger 1023 i32 "$1" i32 0 >/dev/null; }   # i32 0 = no forced colour mode
# current display colour matrix as 16 column-major floats ("colorTransformMatrix=[[r0][r1][r2][r3]]")
mat_get() {
  dumpsys SurfaceFlinger | grep -m1 'colorTransformMatrix=' | tr -c '0-9.\n-' ' ' |
    awk '{ if (NF < 16) exit; for (c = 0; c < 4; c++) for (r = 0; r < 4; r++) printf "%s ", $(r*4+c+1); print "" }'
}
mat_set() {   # $* = 16 floats, or "id"
  if [ "$1" = id ] || [ -z "$1" ]; then service call SurfaceFlinger 1015 i32 0 >/dev/null; return; fi
  a="i32 1"; for v in $*; do a="$a f $v"; done
  service call SurfaceFlinger 1015 $a >/dev/null
}
sat_set() { service call SurfaceFlinger 1022 f "$1" >/dev/null; }
mdiv() { awk -v op=div -v A="$1" -v B="$2" -f "$MATH"; }
msat() { awk -v op=sat -v A="$1" -f "$MATH"; }
same() { awk -v a="$1" -v b="$2" 'BEGIN { n = split(a, x); m = split(b, y); if (n != 16 || m != 16) exit 1;
  for (i = 1; i <= 16; i++) { d = x[i] - y[i]; if (d < -0.002 || d > 0.002) exit 1 } }'; }

until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 5; done
sleep 10
on=0; [ -f "$STATE" ] && on=1   # script restarted while mirroring was on
log "started (on=$on)"
# React to "wm_set_resumed_activity" events at once (no visible lag when entering/leaving the
# mirroring screen); a tick every 5 s re-checks with dumpsys as a fallback.
poll() { dumpsys activity activities | grep -m1 'topResumedActivity' | grep -q "$ACT" && echo 1 || echo 0; }
{ logcat -b events -v brief -T 1 -s wm_set_resumed_activity 2>/dev/null &
  while :; do echo tick; sleep 5; done; } |
while read -r line; do
  case "$line" in
    *wm_set_resumed_activity*"$ACT"*) want=1;;
    *wm_set_resumed_activity*) want=0;;
    *) want=$(poll);;
  esac
  if [ $want = 1 ] && [ $on = 0 ]; then
    prev=$(sf_setting); [ "$prev" = 1 ] && prev=0   # never "restore" to unmanaged
    m0=$(mat_get)                                  # client x saturation
    if [ -z "$m0" ] || same "$m0" "$ID"; then
      s0=1.0; c0=id                                # nothing to separate (no flash)
    else
      mat_set id; sleep 0.05; so=$(mat_get)        # saturation only
      s0=$(msat "$so"); s0=${s0:-1.0}
      c0=$(mdiv "$m0" "$so"); { [ -z "$c0" ] || same "$c0" "$ID" || same "$c0" "$CAL"; } && c0=id
    fi
    printf '%s\n%s\n%s\n' "$prev" "$s0" "$c0" > "$STATE"
    sf_set 1; sat_set 1.0; mat_set $CAL; on=1
    log "mirroring in front -> unmanaged + saturation 1.0 + P3 calibration matrix (will restore setting $prev, saturation $s0, matrix $c0)"
  elif [ $want = 0 ] && [ $on = 1 ]; then
    prev=$(sed -n 1p "$STATE" 2>/dev/null); s0=$(sed -n 2p "$STATE" 2>/dev/null); c0=$(sed -n 3p "$STATE" 2>/dev/null)
    sf_set "${prev:-0}"; sat_set "${s0:-1.0}"; mat_set ${c0:-id}
    rm -f "$STATE"; on=0
    log "mirroring left -> restored setting ${prev:-0}, saturation ${s0:-1.0}, matrix ${c0:-id}"
  elif [ $want = 1 ]; then
    [ "$(sf_setting)" != 1 ] && { sf_set 1; log "colour setting was reset while mirroring -> unmanaged again"; }
    if ! same "$(mat_get)" "$CAL"; then   # system re-applied saturation / matrix (night light, colour mode ...)
      sat_set 1.0; mat_set $CAL; log "saturation/matrix changed while mirroring -> calibration again"
    fi
  fi
done
