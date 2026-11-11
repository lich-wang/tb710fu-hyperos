#!/bin/bash
# Compile the combined SELinux policy the way init does (platform + mappings + system_ext + product from the
# Xiaomi framework, plat_pub_versioned + vendor + odm from VENDOR_PREFIX). Needs secilc (apt install secilc).
# usage (in the mount dir of mount.sh, as root): sepolicy_compile.sh len_|port_ [extra.cil]
# Exit status = secilc's.
V=$1; C=$2
VV=$(cat "${V}vendor/etc/selinux/plat_sepolicy_vers.txt") || exit 2
IN=(xm_system/system/etc/selinux/plat_sepolicy.cil "xm_system/system/etc/selinux/mapping/$VV.cil"
    "xm_system/system/etc/selinux/mapping/$VV.compat.cil"
    xm_system_ext/etc/selinux/system_ext_sepolicy.cil "xm_system_ext/etc/selinux/mapping/$VV.cil"
    xm_product/etc/selinux/product_sepolicy.cil "xm_product/etc/selinux/mapping/$VV.cil"
    "${V}vendor/etc/selinux/plat_pub_versioned.cil" "${V}vendor/etc/selinux/vendor_sepolicy.cil")
[ -f "${V}odm/etc/selinux/odm_sepolicy.cil" ] && IN+=("${V}odm/etc/selinux/odm_sepolicy.cil")
[ -n "$C" ] && IN+=("$C")
T=$(mktemp -d) || exit 2
[ -n "$T" ] && [ -d "$T" ] || exit 2
trap 'rm -rf -- "$T"' EXIT
EX=()
for f in "${IN[@]}"; do
  if [ -f "$f" ]; then
    o="$T/$(echo "$f" | tr / _)"
    # Ubuntu's secilc 3.9 does not know this Android 17 policy capability
    grep -v "^(policycap functionfs_seclabel)" "$f" > "$o"
    EX+=("$o")
  else
    echo "  (missing $f)"
  fi
done
secilc -m -M true -G -N -c 33 -o "$T/sepolicy.bin" -f /dev/null "${EX[@]}" 2>&1 | tail -25
status=${PIPESTATUS[0]}
echo "exit=$status"
exit "$status"
