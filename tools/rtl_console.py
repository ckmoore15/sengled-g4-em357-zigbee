#!/usr/bin/env python3
"""Helper for the RTL8196E console on /dev/serial0 at 38400 8N1.
  rtl_console.py esc [seconds]          send Esc repeatedly until <RealTek> seen
  rtl_console.py cmd "LINE" [--yes] [--wait S] [--until TEXT]
  rtl_console.py listen [seconds]
All output is appended to $RTL_CONSOLE_LOG (default ~/zigbee-garage-tools/console.log)"""
import os, subprocess, sys, time, serial

LOG = os.environ.get("RTL_CONSOLE_LOG", os.path.expanduser("~/zigbee-garage-tools/console.log"))
PROMPT = b"<RealTek>"


def out(data, log):
    if data:
        sys.stdout.write(data.decode("latin-1"))
        sys.stdout.flush()
        log.write(data)
        log.flush()


def main():
    a = sys.argv[1:]
    s = serial.Serial("/dev/serial0", 38400, bytesize=8, parity="N", stopbits=1,
                      xonxoff=False, rtscts=False, timeout=0.05)
    log = open(LOG, "ab")
    buf = b""
    if a[0] == "esc":
        end = time.time() + float(a[1] if len(a) > 1 else 90)
        # Keep Pi TX (GPIO14) floating until the hub talks, so it cannot
        # back-power the hub through its RX pad and spoil the power-on reset.
        subprocess.run(["pinctrl", "set", "14", "ip", "pn"], check=True)
        while time.time() < end:
            d = s.read(256)
            out(d, log)
            buf += d
            # NULs are just a low (unpowered) line, not hub output
            if d.strip(b"\x00"):
                break
        subprocess.run(["pinctrl", "set", "14", "a5"], check=True)
        print("\n### hub output detected, TX enabled, sending Esc" if buf
              else "\n### no output from hub before timeout")
        while time.time() < end:
            s.write(b"\x1b")
            d = s.read(256)
            out(d, log)
            buf += d
            if PROMPT in buf:
                time.sleep(0.5)
                s.reset_input_buffer()
                s.write(b"\r")
                t = time.time() + 1.5
                while time.time() < t:
                    out(s.read(256), log)
                print("\n### REACHED <RealTek>")
                return 0
        print("\n### TIMEOUT: no <RealTek> seen; bytes received: %d" % len(buf))
        return 1
    if a[0] == "listen":
        end = time.time() + float(a[1] if len(a) > 1 else 30)
        while time.time() < end:
            out(s.read(256), log)
        return 0
    if a[0] == "cmd":
        line, yes = a[1], "--yes" in a
        wait = float(a[a.index("--wait") + 1]) if "--wait" in a else 10
        until = a[a.index("--until") + 1].encode() if "--until" in a else None
        s.reset_input_buffer()
        s.write(line.encode() + b"\r")
        end, answered = time.time() + wait, False
        while time.time() < end:
            d = s.read(256)
            out(d, log)
            buf += d
            if yes and not answered and b"(Y)es" in buf:
                time.sleep(0.2)
                s.write(b"Y")
                answered = True
                buf = b""
            if until and until in buf and (answered or not yes):
                t = time.time() + 1
                while time.time() < t:
                    out(s.read(256), log)
                return 0
        return 0 if until is None else 1


sys.exit(main())
