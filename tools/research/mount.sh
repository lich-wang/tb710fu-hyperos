#!/bin/bash
# Mount the images compared in docs/porting.md read-only under $MNT (Linux, needs sudo; ext4 + erofs).
#   len_*  Lenovo stock (tb710fu.py pull --package, CN 17.5.04.414): vendor odm vendor_dlkm system_dlkm system_ext product
#   xm_*   Xiaomi Pad 8 yupei (tb710fu.py import, OS4.0.5.0.XPZCNXM): system system_ext product mi_ext vendor odm
#   port_* HyperOS 3 port (vendor_a odm_a vendor_dlkm_a system_dlkm_a, lp_unpack from the port super), optional
# usage: mount.sh LENOVO_DIR XIAOMI_DIR [PORT_DIR]      umount: sudo umount $MNT/*
set -e
L=$1; X=$2; P=$3; MNT=${MNT:-$PWD/mnt}
m() { mkdir -p "$MNT/$1"; mountpoint -q "$MNT/$1" || sudo mount -o ro,loop${3:+,$3} "$2" "$MNT/$1"; }
for n in vendor odm vendor_dlkm system_dlkm system_ext product; do [ -f "$L/$n.img" ] && m len_$n "$L/$n.img" noload; done
for n in system system_ext product mi_ext vendor odm; do m xm_$n "$X/$n.img"; done
if [ -n "$P" ]; then for n in vendor odm vendor_dlkm system_dlkm; do m port_$n "$P/${n}_a.img"; done; fi
mount | grep "$MNT" | awk '{print $3, $5}'
