#!/usr/bin/env python3
"""Patch the built-in device tree of the Sengled G4 kernel image for the
32 MiB RAM / 8 MiB flash hardware revision.

  patch_kernel.py <in kernel.img> <out kernel.img>

kernel.img = 16-byte cvimg header (cs6c) + zboot vmlinuz (+2-byte sum16 fixup).
The vmlinuz carries the kernel as an LZMA-alone stream; the DTB is inside it.
Only same-size property values are rewritten, so the FDT layout is unchanged.
"""
import lzma
import struct
import sys

PATCHES = [
    # (node path suffix, property, old cells, new cells)
    ("/memory@0", "reg", (0x00000000, 0x04000000), (0x00000000, 0x02000000)),
    ("/reserved-memory/boothold@3ffe000", "reg", (0x03FFE000, 0x1000), (0x01FFE000, 0x1000)),
    ("/partitions/partition@400000", "reg", (0x400000, 0xC00000), (0x400000, 0x400000)),
]
RENAMES = [(b"boothold@3ffe000", b"boothold@1ffe000")]


def sum16(b):
    s = 0
    for i in range(0, len(b) - 1, 2):
        s = (s + ((b[i] << 8) | b[i + 1])) & 0xFFFF
    return s


def fdt_walk(dtb):
    """Yield (path, prop_name, value_offset, value_len) for every property."""
    magic, total, off_struct, off_strings = struct.unpack(">IIII", dtb[:16])
    assert magic == 0xD00DFEED
    p, path = off_struct, []
    while True:
        tok = struct.unpack(">I", dtb[p:p + 4])[0]
        p += 4
        if tok == 1:  # BEGIN_NODE
            end = dtb.index(b"\0", p)
            path.append(dtb[p:end].decode())
            p = (end + 4) & ~3
        elif tok == 2:  # END_NODE
            path.pop()
        elif tok == 3:  # PROP
            ln, nameoff = struct.unpack(">II", dtb[p:p + 8])
            p += 8
            nend = dtb.index(b"\0", off_strings + nameoff)
            name = dtb[off_strings + nameoff:nend].decode()
            yield "/".join(path) or "/", name, p, ln
            p = (p + ln + 3) & ~3
        elif tok == 4:  # NOP
            pass
        elif tok == 9:  # END
            return
        else:
            raise ValueError("bad FDT token %d at %#x" % (tok, p - 4))


def patch_dtb(dtb):
    dtb = bytearray(dtb)
    model = None
    done = set()
    for path, name, off, ln in list(fdt_walk(bytes(dtb))):
        if path == "/" and name == "model":
            model = bytes(dtb[off:off + ln]).rstrip(b"\0").decode()
        for suffix, prop, old, new in PATCHES:
            if name == prop and path.endswith(suffix):
                cur = struct.unpack(">%dI" % (ln // 4), dtb[off:off + ln])
                if cur != old:
                    raise SystemExit("%s/%s is %s, expected %s" % (path, prop, [hex(c) for c in cur], [hex(c) for c in old]))
                dtb[off:off + ln] = struct.pack(">%dI" % len(new), *new)
                done.add(suffix)
    missing = [s for s, *_ in PATCHES if s not in done]
    if missing:
        raise SystemExit("properties not found: %s" % missing)
    for old, new in RENAMES:
        assert len(old) == len(new)
        i = dtb.find(old + b"\0")
        if i < 0:
            raise SystemExit("node name %s not found" % old)
        dtb[i:i + len(old)] = new
    return bytes(dtb), model


def find_lzma(buf):
    for i in range(len(buf) - 13):
        if buf[i] not in (0x5D, 0x6D):
            continue
        dict_size = struct.unpack("<I", buf[i + 1:i + 5])[0]
        if dict_size & (dict_size - 1) or not (1 << 16) <= dict_size <= (1 << 26):
            continue
        try:
            d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            out = d.decompress(buf[i:])
        except lzma.LZMAError:
            continue
        if len(out) > 1 << 20 and d.eof:
            used = len(buf) - i - len(d.unused_data)
            return i, used, out
    raise SystemExit("no LZMA stream found")


def lzma_props(b):
    p = b[0]
    lc, rem = p % 9, p // 9
    lp, pb = rem % 5, rem // 5
    return lc, lp, pb, struct.unpack("<I", b[1:5])[0]


def main():
    src, dst = sys.argv[1], sys.argv[2]
    img = bytearray(open(src, "rb").read())
    sig, start, burn, ln = struct.unpack(">4sIII", img[:16])
    assert sig == b"cs6c", sig
    payload = img[16:16 + ln]
    assert sum16(payload) == 0, "input checksum bad"
    print("header: start=%#x burn=%#x len=%#x" % (start, burn, ln))

    off, used, vmlinux = find_lzma(bytes(payload))
    lc, lp, pb, dict_size = lzma_props(payload[off:off + 5])
    print("lzma stream at payload+%#x, %d bytes -> %d bytes (lc=%d lp=%d pb=%d dict=%#x)"
          % (off, used, len(vmlinux), lc, lp, pb, dict_size))

    vm = bytearray(vmlinux)
    n = 0
    i = vm.find(b"\xd0\x0d\xfe\xed")
    while i >= 0:
        total = struct.unpack(">I", vm[i + 4:i + 8])[0]
        if 0x100 < total < 0x40000:
            new, model = patch_dtb(vm[i:i + total])
            print("DTB at vmlinux+%#x (%d bytes), model=%r: patched" % (i, total, model))
            if "Sengled" not in (model or ""):
                raise SystemExit("unexpected model")
            vm[i:i + total] = new
            n += 1
        i = vm.find(b"\xd0\x0d\xfe\xed", i + 4)
    if n == 0:
        raise SystemExit("no DTB found")

    best = None
    for preset in (9 | lzma.PRESET_EXTREME, 9, 8 | lzma.PRESET_EXTREME):
        for nice in (273, 128, 64):
            f = [{"id": lzma.FILTER_LZMA1, "preset": preset, "dict_size": dict_size,
                  "lc": lc, "lp": lp, "pb": pb, "nice_len": nice}]
            c = lzma.compress(bytes(vm), format=lzma.FORMAT_ALONE, filters=f)
            if best is None or len(c) < len(best):
                best = c
    print("recompressed: %d bytes (original %d, slack %d)" % (len(best), used, used - len(best)))
    if len(best) > used:
        raise SystemExit("recompressed stream is larger than the original; cannot patch in place")
    assert lzma.decompress(best, format=lzma.FORMAT_ALONE) == bytes(vm)

    newp = bytearray(payload)
    newp[off:off + used] = best + b"\0" * (used - len(best))
    # cvimg keeps the payload sum16 at 0 through its last two bytes.
    newp[-2:] = b"\0\0"
    fix = (-sum16(newp)) & 0xFFFF
    newp[-2:] = struct.pack(">H", fix)
    assert sum16(newp) == 0
    # round trip
    o2, u2, vm2 = find_lzma(bytes(newp))
    assert o2 == off and vm2 == bytes(vm)
    img[16:16 + ln] = newp
    open(dst, "wb").write(img)
    print("wrote", dst, len(img), "bytes")


main()
