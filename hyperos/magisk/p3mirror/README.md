# tb710fu_p3mirror (Magisk module)

P3 colour for Xiaomi Interconnect PC mirroring (Mac 小米互联服务 -> tablet `com.xiaomi.mirror`).

- Mac: assign Display P3 to the "Xiaomi Pad" display once (kept by ColorSync per display):
  `swift ../../../extras/mac_airplay_icc/setprofile.swift "/System/Library/ColorSync/Profiles/Display P3.icc"`
  (undo: `... setprofile.swift reset`).
- Tablet: while `SinkPCActivity` is the top resumed activity this module sets SurfaceFlinger
  unmanaged (no BT.709 conversion, `ColorMode::NATIVE`), global saturation 1.0 (HyperOS uses 1.3)
  and a 3x3 colour matrix fitted to the factory P3 3D LUT; all three are restored when it leaves.
  Log: `logcat -s tb710fu_p3mirror`. State: `/data/local/tmp/tb710fu_p3mirror.prev`.
  v4: switches on `wm_set_resumed_activity` events (≈0.2 s after the activity changes; 5 s dumpsys
  fallback), matrix fitted with the neutral axis weighted x20 so whites/greys match the normal mode.
- HyperOS 色彩风格 on this port is inverted: 「生动」 = no enhancement (accurate, factory sRGB LUT),
  「专业原色」 = global saturation 1.3. Use 「生动」.

Why a matrix and not the factory P3 mode: forcing SurfaceFlinger to DISPLAY_P3 makes the HWC use the
factory calibration, but the HWC then converts the BT.709 video layer to P3 exactly like GPU
composition (A/B test HWC vs `service call SurfaceFlinger 1008 i32 1`: no visible difference).
NATIVE has no calibration at all ("gamut 255 gamma 255"); the panel's native white is ~9 % too blue
(`panel_topaz_nt36532_tianma_dsc_vid_C3D_cali_p3.txt`: white -> 255,251,232). The matrix is fitted in
gamma-encoded space (no IGC in the NATIVE pipeline): LUT error mean 12.4/255 -> 4.5/255.

Measured 2026-10-23 (colour patches on the Mac display, `adb screencap` on the tablet):
| | sRGB profile on Mac | Display P3 profile on Mac |
|---|---|---|
| sRGB red | 255,1,0 | 234,51,36 (= sRGB red in P3) |
| P3 red | 255,1,0 (clipped) | 255,1,0 |
| grey 50% / 20% | 128 / 51 | 128 / 51 |

Both halves must match: Mac P3 + tablet managed = washed out; Mac sRGB + tablet unmanaged = oversaturated.
