# tb710fu_nativeres (Magisk module)

The HyperOS 3 port (lwky, kept in our HyperOS 4 build) runs `/system/lwky/set_res.sh` on
`sys.boot_completed=1` (`init.lwky.rc` service `set_resolution`), which does
`cmd window size 2000x3198`. The author gives no reason; nothing found online (2026-10-24).
Effect: apps and mirroring (AirPlay / Xiaomi Interconnect) see 3198x2000, the Mac shows
1599x1000 HiDPI, and SurfaceFlinger scales 3198 -> 3200 every frame.

This module magic-mounts a replacement script that does `cmd window size reset` instead.
Removing the module brings the original back (3198 on next boot).

Install (copy the directory straight into /data/adb/modules):
`adb push` the directory to /data/local/tmp, then as root copy it to /data/adb/modules/tb710fu_nativeres,
`chcon u:object_r:vendor_file:s0 system/lwky/set_res.sh` (same label as the original), reboot.
To make it permanent without Magisk: drop `start set_resolution` from `init.lwky.rc` at the next super build.
