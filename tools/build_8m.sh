#!/usr/bin/env bash
# Build an 8 MiB image for the Sengled G4 32 MiB RAM / 8 MiB flash revision.
# Keeps the stock Realtek bootloader block (0x000000-0x01FFFF) from the backup.
#   kernel   0x020000 (cs6c, booted by the stock loader)
#   rootfs   0x200000
#   userdata 0x400000-0x7FFFFF (4 MiB JFFS2, DHCP)
#
# Override paths with env vars, e.g.
#   REPO=~/rtl8196e-gateway STOCK=~/backup/fullflash.bin ./build_8m.sh
set -euo pipefail
REPO=${REPO:-~/rtl8196e-gateway}
RTL=$REPO/3-Main-SoC-Realtek-RTL8196E
TOOLS=${TOOLS:-$(cd "$(dirname "$0")" && pwd)}
OUT=${OUT:-$TOOLS/build-8m}
STOCK=${STOCK:-~/zigbee-garage-backups/hub1-stock/fullflash.bin}
KERNEL_IN=$RTL/32-Kernel/kernel-img/sengled-e39-g8c/kernel-6.18.img
mkdir -p "$OUT"

echo "== rootfs"
"$RTL/33-Rootfs/build_rootfs.sh" >"$OUT/rootfs.log" 2>&1
cp "$RTL/33-Rootfs/rootfs.bin" "$OUT/"

echo "== userdata (4 MiB, DHCP)"
SKEL=$(mktemp -d)
trap 'rm -rf "$SKEL" "$RTL/34-Userdata/build_userdata_8m.sh"' EXIT
cp -a "$RTL/34-Userdata/skeleton/." "$SKEL/"
rm -f "$SKEL/etc/eth0.conf"
( cd "$REPO" && . lib/gwconf.sh && gwconf_write_eth0_bak "$SKEL/etc/eth0.bak" )
sed 's/^PARTITION_SIZE_HEX=0xC00000$/PARTITION_SIZE_HEX=0x400000/' \
    "$RTL/34-Userdata/build_userdata.sh" > "$RTL/34-Userdata/build_userdata_8m.sh"
grep -q '^PARTITION_SIZE_HEX=0x400000$' "$RTL/34-Userdata/build_userdata_8m.sh"
chmod +x "$RTL/34-Userdata/build_userdata_8m.sh"
SKELETON_DIR="$SKEL" "$RTL/34-Userdata/build_userdata_8m.sh" --jffs2-only -q
mv "$RTL/34-Userdata/userdata.bin" "$OUT/"

echo "== kernel"
python3 -I "$TOOLS/patch_kernel.py" "$KERNEL_IN" "$OUT/kernel-8m.img"

echo "== assemble"
IMG=$OUT/fullflash-8m.bin
cp "$STOCK" "$IMG"
# erase everything after the stock boot block, then place the parts
dd if=/dev/zero bs=64k count=126 2>/dev/null | tr '\0' '\377' | dd of="$IMG" bs=64k seek=2 conv=notrunc 2>/dev/null
dd if="$OUT/kernel-8m.img" of="$IMG" bs=1 seek=$((0x20000)) conv=notrunc 2>/dev/null
tail -c +17 "$OUT/rootfs.bin"   | dd of="$IMG" bs=1 seek=$((0x200000)) conv=notrunc 2>/dev/null
tail -c +17 "$OUT/userdata.bin" | dd of="$IMG" bs=1 seek=$((0x400000)) conv=notrunc 2>/dev/null

k=$(stat -c%s "$OUT/kernel-8m.img"); r=$(( $(stat -c%s "$OUT/rootfs.bin") - 16 )); u=$(( $(stat -c%s "$OUT/userdata.bin") - 16 ))
printf 'kernel %d / %d\nrootfs %d / %d\nuserdata %d / %d\n' $k $((0x1E0000)) $r $((0x200000)) $u $((0x400000))
[ $k -le $((0x1E0000)) ] && [ $r -le $((0x200000)) ] && [ $u -le $((0x400000)) ]
[ "$(stat -c%s "$IMG")" -eq 8388608 ]
cmp -n $((0x20000)) "$IMG" "$STOCK" && echo "stock boot block 0x0-0x1FFFF unchanged"
for spec in 20000:63733663 200000:68737173 400000:1985; do
    o=${spec%%:*}; m=${spec##*:}
    got=$(dd if="$IMG" bs=1 skip=$((0x$o)) count=$(( ${#m} / 2 )) 2>/dev/null | od -An -tx1 -v | tr -d ' \n')
    [ "$got" = "$m" ] && echo "magic @0x$o $m OK" || { echo "magic @0x$o got $got expected $m" >&2; exit 1; }
done
sha256sum "$IMG"
