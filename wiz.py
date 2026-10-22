#!/usr/bin/env python3
"""
wiz.py - Fresh, clean WiZ smart LED controller over UDP 38899.

Confirmed working against Philips WiZ (fw 1.38.0, env "pro", MAC 9877d5e9b194).
Requires the bulb's WiZ app setting:
    Settings > Security > Local Communication > "Allow all controls"
otherwise writes are rejected with -32602 Invalid params.

Usage:
    python3 wiz.py --ip 192.168.31.34 status
    python3 wiz.py --ip 192.168.31.34 on
    python3 wiz.py --ip 192.168.31.34 off
    python3 wiz.py --ip 192.168.31.34 toggle
    python3 wiz.py --ip 192.168.31.34 bright --value 80
    python3 wiz.py --ip 192.168.31.34 color  --rgb 0,255,0
    python3 wiz.py --ip 192.168.31.34 temp   --value 4000
    python3 wiz.py --ip 192.168.31.34 scene  --id 4
    python3 wiz.py discover
"""
import argparse
import json
import socket
import sys
import time

PORT = 38899
BROADCAST = "255.255.255.255"
MIN_DIMMING = 10          # WiZ dimming floor; values below are unreliable


def _send(ip, payload, timeout=3.0):
    """Send one UDP JSON-RPC message and return the decoded reply (or dict error)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(json.dumps(payload).encode(), (ip, PORT))
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode())
    except socket.timeout:
        return {"error": {"code": "TIMEOUT", "message": "no response from bulb"}}
    except OSError as exc:
        return {"error": {"code": "SOCKET", "message": str(exc)}}
    except ValueError as exc:
        return {"error": {"code": "PARSE", "message": str(exc)}}
    finally:
        sock.close()


def call(ip, method, params=None):
    """Send a method call and return the whole reply envelope."""
    return _send(ip, {"method": method, "params": params or {}})


def find_success(reply):
    """Return the 'result' dict if the reply indicates success, else raise SystemExit."""
    if "result" in reply:
        return reply["result"]
    err = reply.get("error", {})
    code = err.get("code")
    msg = err.get("message", "unknown error")
    print(f"[!] Bulb rejected the command: {code} {msg}", file=sys.stderr)
    if code == -32602:
        print("    -> Most likely cause: WiZ app 'verified controls' is ON.",
              file=sys.stderr)
        print("       Fix: WiZ app > Settings > Security > Local Communication",
              file=sys.stderr)
        print("            > set to 'Allow all controls'.", file=sys.stderr)
    sys.exit(1)


def set_pilot(ip, params):
    """Send setPilot with the given params and confirm success."""
    reply = call(ip, "setPilot", params)
    find_success(reply)
    return reply


# ---------------------------------------------------------------- commands

def cmd_status(ip, _args):
    info = find_success(call(ip, "getPilot"))
    print("--- WiZ status ---")
    print(f"  mac      : {info.get('mac')}")
    print(f"  state    : {'ON' if info.get('state') else 'OFF'}")
    if info.get("dimming") is not None:
        print(f"  dimming  : {info.get('dimming')}%")
    if info.get("temp") is not None:
        print(f"  temp     : {info.get('temp')}K")
    if any(k in info for k in ("r", "g", "b")):
        print(f"  rgb      : {info.get('r')},{info.get('g')},{info.get('b')}")
    if info.get("sceneId"):
        print(f"  sceneId  : {info.get('sceneId')}")
    print(f"  rssi     : {info.get('rssi')} dBm")
    print(f"  raw      : {json.dumps(info)}")


def cmd_on(ip, _args):
    set_pilot(ip, {"state": True})
    print("[+] ON")


def cmd_off(ip, _args):
    set_pilot(ip, {"state": False})
    print("[+] OFF")


def cmd_toggle(ip, _args):
    cur = find_success(call(ip, "getPilot"))
    new_state = not cur.get("state", False)
    set_pilot(ip, {"state": new_state})
    print(f"[+] {'ON' if new_state else 'OFF'}")


def cmd_bright(ip, args):
    val = max(MIN_DIMMING, min(100, args.value))
    if val != args.value:
        print(f"[i] clamped dimming {args.value} -> {val}")
    set_pilot(ip, {"state": True, "dimming": val})
    print(f"[+] brightness {val}%")


def cmd_color(ip, args):
    try:
        r, g, b = (int(x) for x in args.rgb.split(","))
    except ValueError:
        sys.exit("[!] --rgb must be 'R,G,B' e.g. 255,0,0")
    for name, v in (("r", r), ("g", g), ("b", b)):
        if not 0 <= v <= 255:
            sys.exit(f"[!] {name}={v} out of range 0-255")
    set_pilot(ip, {"state": True, "r": r, "g": g, "b": b})
    print(f"[+] color {r},{g},{b}")


def cmd_temp(ip, args):
    val = max(2200, min(6500, args.value))
    if val != args.value:
        print(f"[i] clamped temp {args.value} -> {val}")
    set_pilot(ip, {"state": True, "temp": val})
    print(f"[+] white temp {val}K")


def cmd_scene(ip, args):
    set_pilot(ip, {"state": True, "sceneId": args.id})
    print(f"[+] scene {args.id}")


def cmd_config(ip, _args):
    info = find_success(call(ip, "getSystemConfig"))
    print(json.dumps(info, indent=2))


def cmd_discover(_ip, _args):
    """Broadcast for WiZ bulbs on the LAN and list every responder."""
    print("[*] Broadcasting for WiZ devices (3s)...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(3.0)
    found = {}
    try:
        for method in ("getSystemConfig", "getPilot"):
            sock.sendto(json.dumps({"method": method, "params": {}}).encode(),
                        (BROADCAST, PORT))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                reply = json.loads(data.decode())
            except ValueError:
                continue
            res = reply.get("result", {})
            ip = addr[0]
            if ip not in found:
                found[ip] = res
    finally:
        sock.close()

    if not found:
        print("[!] No WiZ devices responded. Check you are on the same subnet.")
        return
    print(f"[+] Found {len(found)} device(s):")
    for ip, res in sorted(found.items()):
        mac = res.get("mac", "?")
        fw = res.get("fwVersion", "?")
        env = res.get("env", "?")
        model = res.get("moduleName", "?")
        print(f"    {ip:15}  mac={mac}  fw={fw}  env={env}  model={model}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Control Philips WiZ smart LEDs over UDP (local, no cloud).")
    ap.add_argument("--ip", help="Target bulb IP (not needed for 'discover')")
    ap.add_argument("--timeout", type=float, default=3.0,
                    help="UDP response timeout seconds (default 3)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Read current state")
    sub.add_parser("on", help="Turn on")
    sub.add_parser("off", help="Turn off")
    sub.add_parser("toggle", help="Flip current state")
    sub.add_parser("config", help="Read system config (fw, ids, region)")
    sub.add_parser("discover", help="Broadcast-discover WiZ bulbs on LAN")

    p = sub.add_parser("bright", help="Set brightness 10-100")
    p.add_argument("--value", type=int, required=True)

    p = sub.add_parser("color", help="Set RGB color")
    p.add_argument("--rgb", required=True, help="R,G,B e.g. 255,0,0")

    p = sub.add_parser("temp", help="Set white temperature 2200-6500K")
    p.add_argument("--value", type=int, required=True)

    p = sub.add_parser("scene", help="Activate a scene by id")
    p.add_argument("--id", type=int, required=True)

    args = ap.parse_args()

    global PORT
    if args.cmd != "discover" and not args.ip:
        ap.error("--ip is required for this command")

    handlers = {
        "status": cmd_status, "on": cmd_on, "off": cmd_off, "toggle": cmd_toggle,
        "bright": cmd_bright, "color": cmd_color, "temp": cmd_temp,
        "scene": cmd_scene, "config": cmd_config, "discover": cmd_discover,
    }
    handlers[args.cmd](args.ip, args)


if __name__ == "__main__":
    main()
