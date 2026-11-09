# Sengled G4 hub (8 MiB flash / EM357) as an Ethernet Zigbee coordinator for Home Assistant

Notes and scripts for turning a **Sengled Smart Hub G4 (E39-G8C)** into a network Zigbee
coordinator for Home Assistant **ZHA**, on a hardware revision that the excellent
[jnilo1/rtl8196e-gateway](https://github.com/jnilo1/rtl8196e-gateway) project does not cover.

> **Warning.** This was done on exactly one unit. It rewrites the hub's flash and replaces the
> radio firmware; the original Sengled radio firmware cannot be backed up and is lost.
> Take a full flash backup first (see below) and keep it safe. You can brick your hub. No warranty.

## Is this your hub?

The rtl8196e-gateway docs describe a G4 with a 16 MiB flash and an EFR32 radio. Mine was different:

| Part | Documented G4 | This revision |
|------|---------------|---------------|
| SoC | Realtek RTL8196E | same |
| SPI flash | 16 MiB (GD25Q127) | **8 MiB GigaDevice GD25Q64CSIG** (JEDEC `C8 40 17`) |
| RAM | 64 MiB | ESMT M14D5121632A (a 64 MiB part) but the **stock loader configures 32 MiB** |
| Zigbee radio | Silicon Labs EFR32MG13 | **Silicon Labs EM357** |
| Other | | Macronix MX25L2006E, unused Sigma Designs ZM5101 Z-Wave module |

At the serial console (38400 8N1), press Esc during power-on to get `<RealTek>`. The banner looked like:

```
RealTek(RTL8196E)at 2015.01.06-18:13-0800 v1.6 [16bit](400MHz)
bootbank is 2, bankmark FFFFFFF0
... GD25Q64 ... chipSize 0x0800000
```

If you see `GD25Q64` / 8 MiB, the project's own bootloader and installer are **not** safe on your unit
(its DRAM settings differ: stock `B8001004=0x54480000`, `B8001008=0x9103A920`). This repo keeps the
stock Sengled bootloader instead.

## Result

- Linux (rtl8196e-gateway v4.7.0 kernel 6.18 + rootfs) running from the **stock** Realtek bootloader.
- EM357 flashed with **EmberZNet 6.7.8.0 NCP** (EZSP v8), 57600 baud, software flow control.
- Raw radio serial exposed by the project's UART bridge on **TCP port 8888**.
- Home Assistant ZHA: radio type **EZSP**, path `socket://<hub-ip>:8888`, baud 57600, flow control software.

**Why ZHA and not Zigbee2MQTT:** Z2M's `ember` driver needs EZSP v13+, which EM35x chips can't run, and
the legacy `ezsp` driver was removed in Z2M 2.0. ZHA (bellows) still supports EZSP v8. A ZHA network
lives happily next to an existing Z2M network on a different coordinator.

## What you need

- A Linux box with a 3.3 V UART. I used a Raspberry Pi: GND to pin 6, hub TX to Pi RX (pin 10),
  hub RX to Pi TX (pin 8). Disable the Pi's serial console (`console=serial0,...` in `cmdline.txt`,
  `serial-getty@ttyS0`).
- A wired network port for the hub and a temporary address `192.168.1.x/24` on the same LAN for TFTP
  (the stock loader is at `192.168.1.6`; it answers ARP but not ping).
- A clone of [jnilo1/rtl8196e-gateway](https://github.com/jnilo1/rtl8196e-gateway) and its build
  prerequisites (I used commit `cc7479b`, release v4.7.0).
- Python 3 with `pyserial`; for checking the radio, `bellows` / `universal-silabs-flasher` in a venv.

## Layout of this repo

| Path | What it is |
|------|------------|
| `tools/rtl_console.py` | Serial helper for the `<RealTek>` console: `esc` (catch the loader), `cmd "LINE" [--yes] [--wait S] [--until TEXT]`, `listen`. Floats the Pi's TX until the hub talks, so the Pi can't back-power the hub. |
| `tools/build_8m.sh` | Builds an 8 MiB full-flash image from your own backup + the project's kernel/rootfs/userdata. |
| `tools/patch_kernel.py` | Patches the kernel's built-in device tree for 32 MiB RAM and a 4 MiB userdata partition. |
| `tools/em35x_xmodem.py` | Puts the EM357 into its bootloader via GPIO and uploads an `.ebl` over XMODEM-CRC through the UART bridge. |
| `hub/userdata/etc/init.d/S49em35x_pinmux` | Boot script that fixes the UART1 pin mux so the radio's TX reaches the SoC. |
| `hub/userdata/etc/radio.conf.example` | Radio settings for the EM357 NCP image. |

No firmware or flash images are included: they contain Sengled / Silicon Labs code, and a backup is
specific to one unit. Build your own from your own backup.

## Steps

### 1. Back up the whole flash

At `<RealTek>`:

```
FLR 80500000 00000000 00800000     # copy 8 MiB of flash to RAM
```

Then from the Linux box (temp IP 192.168.1.x):

```
tftp -m binary 192.168.1.6 -c get fullflash.bin
```

Do it twice and compare the SHA-256 — they must match. Keep copies in two places.

**Restore** (if you ever need stock back): `LOADADDR 80500000`, `tftp -m binary 192.168.1.6 -c put fullflash.bin`,
then `FLW 00000000 80500000 00800000 0`.

### 2. Build the 8 MiB image

```
REPO=~/rtl8196e-gateway STOCK=~/backup/fullflash.bin tools/build_8m.sh
```

Flash layout it produces:

| Offset | Size | Content |
|--------|------|---------|
| `0x000000` | 128 KiB | Stock Sengled/Realtek bootloader (unchanged, copied from your backup) |
| `0x020000` | up to 1.875 MiB | Project kernel (`cs6c`), DTB patched |
| `0x200000` | 2 MiB | Project rootfs |
| `0x400000` | 4 MiB | Userdata (JFFS2, DHCP) |

Why this works: the stock loader (stage 2, LZMA at flash `0x1320`, runs at `0x80400000`) boots a `cs6c`
image it finds at bank+`0x10000`/`0x20000`/`0x30000` (4 KiB steps up to `0x40000`) and only checks a
sum16. `patch_kernel.py` changes three device-tree values in place (memory 64→32 MiB, boothold
`0x3FFE000`→`0x1FFE000`, userdata partition `0xC00000`→`0x400000`), recompresses the LZMA stream with
the original parameters and fixes the checksum.

### 3. Flash it

At `<RealTek>`:

```
AUTOBURN 0
LOADADDR 80500000
```

`tftp -m binary 192.168.1.6 -c put fullflash-8m.bin`, then write everything **except** the bootloader:

```
FLW 00020000 80520000 007E0000 0
```

I verified with a full readback (`FLR` + `tftp get`) compared byte-for-byte to the image.

Power-cycle. You should see `Jump to image start=0x80560000`, Linux 6.18 with 32 MiB, and a DHCP address.
Set a root password and install your SSH key from the serial console, then reserve the hub's
IP in your router (its MAC is generated on first boot and stored in `/userdata/etc/mac_address`).

The project's in-place upgrade scripts and "boothold" assume its own bootloader and 16 MiB layout —
**they don't apply here.** To upgrade, rebuild with `build_8m.sh` and flash over serial again.

### 4. Fix the radio UART (pin mux)

The stock loader leaves `PIN_MUX_SEL` (`0xB8000040`) bit 2 set, so the EM357's TX never reaches UART1
RX (the bridge's rx counter stays 0). Sengled's own kernel programs bits [2:0] = `010`. Install the fix:

```
scp hub/userdata/etc/init.d/S49em35x_pinmux root@<hub-ip>:/userdata/etc/init.d/
ssh root@<hub-ip> chmod +x /userdata/etc/init.d/S49em35x_pinmux
```

It runs before the project's `S50uart_bridge` on every boot (value goes `0x4E` → `0x4A`).

### 5. Flash the EM357 with EmberZNet NCP firmware

Download `em357-v678-ncp-uart-sw.ebl` from
[walthowd/husbzb-firmware](https://github.com/walthowd/husbzb-firmware) (I used commit `d6a512c`).

The EM35x bootloader is entered by holding **GPIO13 low through a GPIO11 (reset, active low) pulse**.
It then becomes a silent XMODEM-CRC receiver that answers the first byte with `C`.
`em35x_xmodem.py` does all of this over SSH + the bridge port (SSH key login to the hub required):

```
tools/em35x_xmodem.py <hub-ip> em357-v678-ncp-uart-sw.ebl
```

Expect 1183 blocks, `EOT acknowledged`, and `Ok` from the loader. Then set the radio settings and reboot:

```
scp hub/userdata/etc/radio.conf.example root@<hub-ip>:/userdata/etc/radio.conf
ssh root@<hub-ip> reboot
```

Check it from any machine with bellows / universal-silabs-flasher:

```
universal-silabs-flasher --device socket://<hub-ip>:8888 --probe-methods ezsp:57600 probe
# -> Detected ApplicationType.EZSP, version '6.7.8.0 build 373'
```

### 6. Home Assistant

Settings → Devices & Services → Add Integration → **Zigbee Home Automation** → Enter manually:

- Radio type: **EZSP**
- Path: `socket://<hub-ip>:8888`, baud **57600**, flow control **software**
- Form a new network

The coordinator shows up as model "ETRX357" with a numeric manufacturer string — that's normal for this
firmware. If the hub reboots, ZHA reconnects on its own after its retry back-off (or reload the entry).

**Tip:** Sengled bulbs are Zigbee end devices, not routers. Add at least one mains-powered plug or switch
to give the network a repeater.

## Pitfalls I hit

- **No serial output.** Three separate causes: `/dev/ttyS0` permissions after disabling the getty;
  the Pi's TX **back-powering the unpowered hub** through its RX pad (spoils the power-on reset —
  `rtl_console.py esc` floats TX until the hub talks); and TX/RX swapped, then a poor contact at the pads.
- **The hub's busybox shell does 32-bit signed arithmetic.** Computing a GPIO register value with
  `$(( ))` when the top bit is set turned `GPIO_DIR` into `0xFFFFFFFF`. Do register math elsewhere and
  pass literal hex to `devmem` (originals on my unit: `DIR 0x18003508 = 0xFF001400`).
- **XMODEM hangs.** The EM35x loader repeats `C` once a second; drain loops must be bounded and stale `C`
  bytes skipped before each block's ACK.
- **Z2M can't drive EZSP v8** (see above) — use ZHA.

## Security

The UART bridge on port 8888 has **no authentication**: anyone who can reach it controls your Zigbee
radio. Keep the hub on a trusted LAN, never port-forward it, and follow the
[project's security notes](https://github.com/jnilo1/rtl8196e-gateway).

## Credits

- [jnilo1/rtl8196e-gateway](https://github.com/jnilo1/rtl8196e-gateway) (MIT) — the kernel, rootfs, UART bridge and almost everything else.
- [walthowd/husbzb-firmware](https://github.com/walthowd/husbzb-firmware) — EM357 EmberZNet NCP images.
- [The1TrueJoe/openHC PR #78](https://github.com/The1TrueJoe/openHC/pull/78) — EM357 on EmberZNet 6.7.8.
- [Home Assistant community: Sengled hub teardown](https://community.home-assistant.io/t/sengled-led-bulbs-and-hub-with-ha/37805/18).

## License

The scripts in this repo are MIT licensed (see `LICENSE`). Third-party firmware is not included and keeps
its own licence.
