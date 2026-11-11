#!/bin/bash
# Compile the combined SELinux policy the way init does (platform + mappings + system_ext + product from the
# Xiaomi framework, plat_pub_versioned + vendor + odm from VENDOR_PREFIX). Needs secilc (apt install secilc).
# usage (in the mount dir of mount.sh, as root): sepolicy_compile.sh len_|port_ [extra.cil]
V=$1; C=$2; VV=$(cat ${V}vendor/etc/selinux/plat_sepolicy_vers.txt)
IN=(xm_system/system/etc/selinux/plat_sepolicy.cil xm_system/system/etc/selinux/mapping/$VV.cil xm_system/system/etc/selinux/mapping/$VV.compat.cil
    xm_system_ext/etc/selinux/system_ext_sepolicy.cil xm_system_ext/etc/selinux/mapping/$VV.cil
    xm_product/etc/selinux/product_sepolicy.cil xm_product/etc/selinux/mapping/$VV.cil
    ${V}vendor/etc/selinux/plat_pub_versioned.cil ${V}vendor/etc/selinux/vendor_sepolicy.cil)
[ -f ${V}odm/etc/selinux/odm_sepolicy.cil ] && IN+=(${V}odm/etc/selinux/odm_sepolicy.cil)
[ -n "$C" ] && IN+=($C)
T=$(mktemp -d); EX=(); for f in "${IN[@]}"; do if [ -f $f ]; then o=$T/$(echo $f | tr / _); grep -v "^(policycap functionfs_seclabel)" $f > $o; EX+=($o); else echo "  (missing $f)"; fi; done
secilc -m -M true -G -N -c 33 -o /tmp/sepol-$$.bin -f /dev/null "${EX[@]}" 2>&1 | tail -25
echo "exit=${PIPESTATUS[0]}"; rm -rf /tmp/sepol-$$.bin $T
