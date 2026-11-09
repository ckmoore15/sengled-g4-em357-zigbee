#!/usr/bin/env python3
"""Enter the Sengled G4 EM35x bootloader and upload an EBL with XMODEM-CRC
through the rtl8196e UART bridge.

  em35x_xmodem.py <hub-ip> <file.ebl>

Bootloader entry (found on this board): GPIO13 held low through a GPIO11
reset pulse; the bootloader then sends 'C' once per second.
"""
import atexit
import socket
import subprocess
import sys
import time

HUB, EBL = sys.argv[1], sys.argv[2]
P = "/sys/module/rtl8196e_uart_bridge/parameters"
SOH, EOT, ACK, NAK, CAN = 0x01, 0x04, 0x06, 0x15, 0x18


def hub(cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "root@" + HUB, cmd],
                          capture_output=True, text=True, check=True).stdout


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def read_byte(s, timeout):
    s.settimeout(timeout)
    try:
        d = s.recv(1)
        return d[0] if d else None
    except socket.timeout:
        return None


GPIO_DIR, GPIO_DAT, UART1_MCR, UART1_MSR = 0x18003508, 0x1800350C, 0x18002110, 0x18002118
B11, B13 = 1 << 11, 1 << 13
AFE = "--afe" in sys.argv


def rd(addr):
    return int(hub("devmem 0x%08X 32" % addr).strip(), 16)


def wr(addr, val):
    # the hub's busybox shell does 32-bit signed math: always pass literal hex
    hub("devmem 0x%08X 32 0x%08X" % (addr, val & 0xFFFFFFFF))


def main():
    data = open(EBL, "rb").read()
    hub(f"echo 115200 > {P}/baud; echo 0 > {P}/flow_control")
    s = socket.create_connection((HUB, 8888), timeout=5)
    # hold boot (13) low, pulse reset (11), keep boot low for the whole transfer
    dir0, dat0 = rd(GPIO_DIR), rd(GPIO_DAT)
    mcr0 = rd(UART1_MCR)
    print("GPIO DIR=%#010x DAT=%#010x" % (dir0, dat0))
    atexit.register(lambda: (wr(UART1_MCR, mcr0), wr(GPIO_DIR, dir0)))
    wr(GPIO_DAT, dat0 & ~(B11 | B13))
    wr(GPIO_DIR, dir0 | B11 | B13)
    time.sleep(0.2)
    wr(GPIO_DIR, (dir0 | B13) & ~B11)
    # the loader is a silent XMODEM receiver: it answers the first byte with 'C'
    t = time.time()
    got = b""
    while time.time() - t < 8 and not got.endswith(b"C"):
        s.sendall(b"\r")
        b = read_byte(s, 1.0)
        if b is not None:
            got += bytes([b])
    # send block 1 right after a fresh 'C' (the loader repeats it every second)
    print("MSR=%#x MCR=%#x" % (rd(UART1_MSR) >> 24, rd(UART1_MCR) >> 24))
    if not got.endswith(b"C"):
        wr(GPIO_DIR, dir0)
        sys.exit("bootloader did not send 'C' (got %r)" % got[-40:])
    print("bootloader ready (%r)" % got[-20:])
    if AFE:
        mcr = rd(UART1_MCR)
        wr(UART1_MCR, mcr | (0x20 << 24))
        print("AFE on: MCR=%#x" % (rd(UART1_MCR) >> 24))

    blocks = [data[i:i + 128] for i in range(0, len(data), 128)]
    for n, blk in enumerate(blocks, 1):
        blk = blk.ljust(128, b"\xff")
        pkt = bytes([SOH, n & 0xFF, 0xFF - (n & 0xFF)]) + blk + crc16(blk).to_bytes(2, "big")
        for attempt in range(10):
            while read_byte(s, 0.01) is not None:  # drop stale 'C'/NAK bytes
                pass
            s.sendall(pkt)
            r = read_byte(s, 5)
            for _ in range(3):
                if r != ord("C"):
                    break
                r = read_byte(s, 5)
            if r == ACK:
                break
            if r == CAN:
                sys.exit("receiver cancelled at block %d" % n)
            print("block %d: retry (%r)" % (n, r))
        else:
            sys.exit("block %d failed" % n)
        if n % 100 == 0 or n == len(blocks):
            print("sent %d/%d blocks" % (n, len(blocks)), flush=True)
    for attempt in range(5):
        s.sendall(bytes([EOT]))
        r = read_byte(s, 5)
        if r == ACK:
            print("EOT acknowledged: upload complete")
            break
        print("EOT reply %r" % r)
    tail = b""
    t = time.time()
    while time.time() - t < 3:
        b = read_byte(s, 0.5)
        if b is not None:
            tail += bytes([b])
    print("after upload: %r" % tail[:200])
    s.close()


main()
