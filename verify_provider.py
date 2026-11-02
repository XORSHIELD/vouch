"""Check MAS/manufacturer provider wiring.

Safe by default: validates configuration and rehearses the demo
connector only. A live dial needs --live and still sends an
obviously-fake identifier, never a real PIN or serial.

    python verify_provider.py [mas|mfr|all] [--live]
"""

from __future__ import annotations

import importlib
import os
import sys

KINDS = {
    "mas": {"url_var": "VOUCH_MAS_URL", "key_var": "VOUCH_MAS_KEY",
            "module": "mas"},
    "mfr": {"url_var": "VOUCH_MFR_URL", "key_var": "VOUCH_MFR_KEY",
            "module": "manufacturer"},
}
LOCAL = ("http://localhost", "http://127.0.0.1")

_FAKE_PIN = "0000"
_FAKE_SERIAL = "SN-0000-TEST"
_FAKE_MAKER = "Demo Maker"


def check_config(kind: str) -> dict:
    """Is this provider kind configured and dialable? Never raises."""
    spec = KINDS.get(kind or "")
    if spec is None:
        return {"ok": False, "kind": kind,
                "reason": f"unknown provider kind {kind!r} (mas, mfr)"}
    url = os.environ.get(spec["url_var"]) or ""
    key = os.environ.get(spec["key_var"]) or ""
    if not url:
        return {"ok": False, "kind": kind, "configured": False,
                "reason": f"{spec['url_var']} is not set"}
    if url == "demo":
        return {"ok": True, "kind": kind, "configured": True,
                "demo": True, "key_present": bool(key)}
    if not (url.startswith("https://") or url.startswith(LOCAL)):
        return {"ok": False, "kind": kind, "configured": True,
                "reason": "URL must be https:// (localhost http ok "
                          "for tests)"}
    return {"ok": True, "kind": kind, "configured": True, "demo": False,
            "key_present": bool(key), "url": url}


def demo_echo(kind: str) -> dict:
    """Run one fake check against the demo connector. Dials nothing."""
    spec = KINDS.get(kind or "")
    if spec is None:
        return {"ok": False, "kind": kind,
                "reason": f"unknown provider kind {kind!r} (mas, mfr)"}
    mod = importlib.import_module(spec["module"])
    saved = os.environ.get(spec["url_var"])
    os.environ[spec["url_var"]] = "demo"
    try:
        if kind == "mas":
            res = mod.verify(_FAKE_PIN, "")
        else:
            res = mod.verify(_FAKE_MAKER, _FAKE_SERIAL)
    finally:
        if saved is None:
            os.environ.pop(spec["url_var"], None)
        else:
            os.environ[spec["url_var"]] = saved
    return {"ok": True, "kind": kind, "verdict": res.get("verdict"),
            "detail": res.get("detail"),
            "note": "demo connector only; nothing dialed"}


def live_dial(kind: str) -> dict:
    """One real dial with an obviously-fake identifier. Opt-in only."""
    cfg = check_config(kind)
    if not cfg.get("ok") or cfg.get("demo"):
        return {"ok": False, "kind": kind,
                "reason": cfg.get("reason") or "demo has nothing to dial"}
    mod = importlib.import_module(KINDS[kind]["module"])
    try:
        if kind == "mas":
            res = mod.verify(_FAKE_PIN, "")
        else:
            res = mod.verify(_FAKE_MAKER, _FAKE_SERIAL)
    except Exception as exc:
        return {"ok": False, "kind": kind,
                "reason": f"dial failed: {exc}"}
    return {"ok": True, "kind": kind, "verdict": res.get("verdict"),
            "detail": (res.get("detail") or "")[:200],
            "note": "live dial with an obviously-fake identifier"}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    live = False
    if "--live" in args:
        live = True
        args.remove("--live")
    kind = args[0] if args else "all"
    kinds = ["mas", "mfr"] if kind == "all" else [kind]
    rc = 0
    for k in kinds:
        cfg = check_config(k)
        if not cfg.get("ok"):
            print(f"[{k}] config: NO — {cfg.get('reason')}")
            rc = 1
            continue
        if cfg.get("demo"):
            print(f"[{k}] config: demo mode")
        else:
            key = "key set" if cfg.get("key_present") else "no key"
            print(f"[{k}] config: {cfg.get('url')} ({key})")
        echo = demo_echo(k)
        print(f"[{k}] demo: {echo.get('verdict')} — "
              f"{echo.get('detail')}")
        if live:
            if cfg.get("demo"):
                print(f"[{k}] live: skipped (demo has nothing to dial)")
            else:
                dial = live_dial(k)
                if not dial.get("ok"):
                    print(f"[{k}] live: NO — {dial.get('reason')}")
                    rc = 1
                else:
                    print(f"[{k}] live: {dial.get('verdict')} — "
                          f"{dial.get('detail')}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
