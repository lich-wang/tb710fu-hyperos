#!/bin/bash
# Mount the images compared in docs/porting.md read-only under $MNT (Linux, needs sudo; ext4 + erofs).
#   len_*  Lenovo stock (tb710fu.py pull --package, CN 17.5.04.414): vendor odm vendor_dlkm system_dlkm system_ext product
#   xm_*   Xiaomi Pad 8 yupei (tb710fu.py import, OS4.0.5.0.XPZCNXM): system system_ext product mi_ext vendor odm
#   port_* HyperOS 3 port (vendor_a odm_a vendor_dlkm_a system_dlkm_a, lp_unpack from the port super), optional
# usage: mount.sh LENOVO_DIR XIAOMI_DIR [PORT_DIR]      umount: sudo umount $MNT/*
# An existing mount is reused only when it is read-only and backed by the same image; otherwise the script stops.
# On failure the mounts made by this run are undone.
set -e
L=$1; X=$2; P=$3; MNT=${MNT:-$PWD/mnt}
MADE=()
undo() { for d in "${MADE[@]}"; do sudo umount "$d" || true; done; }
trap 'echo "failed; undoing this run'"'"'s mounts" >&2; undo' ERR
m() {
  local d="$MNT/$1" img; img=$(realpath "$2")
  mkdir -p "$d"
  if mountpoint -q "$d"; then
    local src opts back
    src=$(findmnt -n -o SOURCE --target "$d"); opts=$(findmnt -n -o OPTIONS --target "$d")
    back=$(losetup -n -O BACK-FILE "$src" 2>/dev/null | sed 's/ *$//' || true)
    if [ "$(realpath "${back:-$src}" 2>/dev/null)" != "$img" ] || [[ ",$opts," != *,ro,* ]]; then
      echo "$d is already mounted from ${back:-$src} ($opts), not read-only $img: umount it first" >&2
      return 1
    fi
    return 0
  fi
  sudo mount -o "ro,loop${3:+,$3}" "$img" "$d"
  MADE+=("$d")
}
for n in vendor odm vendor_dlkm system_dlkm system_ext product; do
  if [ -f "$L/$n.img" ]; then m "len_$n" "$L/$n.img" noload; fi
done
for n in system system_ext product mi_ext vendor odm; do m "xm_$n" "$X/$n.img"; done
if [ -n "$P" ]; then for n in vendor odm vendor_dlkm system_dlkm; do m "port_$n" "$P/${n}_a.img"; done; fi
trap - ERR
mount | grep "$MNT" | awk '{print $3, $5}'
