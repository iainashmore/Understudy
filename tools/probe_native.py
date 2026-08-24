#!/usr/bin/env python3
"""Find out how a Windows application can be driven, before writing a driver.

Run this on the machine with CATIA V5 / 3DEXPERIENCE, with the application open
and the panel of interest visible. It answers the three questions that decide
the whole approach, and writes the evidence to a folder:

  1. Is there a Chrome DevTools endpoint?  -- an embedded WebView2/CEF panel with
     remote debugging on is drivable as an ordinary web page, with full DOM
     access. By far the best outcome.
  2. What does UIAutomation actually see?  -- dumps the control tree. CAD apps
     commonly expose menus and dialogs but nothing about the canvas, and an
     embedded browser may or may not surface its content as accessible text.
  3. What does it look like?               -- a screenshot, so the tree can be
     read against the pixels.

It also reports the monitor layout and DPI scaling, because anchors and regions
are captured per monitor: a second screen at a different scale, or one placed
left of the primary so its coordinates are negative, changes what a recorded
flow means.

Nothing here modifies the application. It only reads.

    python probe_native.py --title "*CATIA*"
    python probe_native.py --title "*3DEXPERIENCE*" --ports 9222,9223 --watch 20

If step 1 finds nothing, try relaunching the host with remote debugging on:

    WebView2   set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
    CEF        add --remote-debugging-port=9222 to the host command line

then run this again.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_PORTS = [9222, 9223, 9229, 8888, 1337]
MAX_TREE_DEPTH = 12
MAX_TREE_NODES = 4000


# -- 1. Chrome DevTools Protocol ----------------------------------------------


def fetch_json(url: str, timeout: float = 1.5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def probe_cdp(ports: list[int]) -> list[dict[str, Any]]:
    """Look for a debugging endpoint. Pure stdlib so it runs anywhere."""
    found = []
    for port in ports:
        base = f"http://127.0.0.1:{port}"
        try:
            version = fetch_json(f"{base}/json/version")
        except Exception:
            continue
        try:
            targets = fetch_json(f"{base}/json/list")
        except Exception:
            targets = []
        found.append({
            "port": port,
            "cdp_url": base,
            "browser": version.get("Browser", "?"),
            "webkit_version": version.get("WebKit-Version", "?"),
            "pages": [
                {"title": t.get("title", ""), "url": t.get("url", ""),
                 "type": t.get("type", "")}
                for t in targets if t.get("type") == "page"
            ],
        })
    return found


# -- 2. UIAutomation -----------------------------------------------------------


def describe_element(element) -> dict[str, Any]:
    """One node, defensively: any property can throw on a custom-drawn control,
    and a probe that dies on the first odd element is useless."""
    def safe(getter, default=""):
        try:
            value = getter()
            return "" if value is None else value
        except Exception:
            return default

    info = safe(lambda: element.element_info, None)
    return {
        "control_type": safe(lambda: info.control_type),
        "automation_id": safe(lambda: info.automation_id),
        "name": str(safe(lambda: info.name))[:120],
        "class_name": safe(lambda: info.class_name),
        "rectangle": str(safe(lambda: info.rectangle)),
        "visible": safe(lambda: bool(info.visible), None),
    }


def walk(element, depth: int = 0, budget: dict[str, int] | None = None) -> list[dict[str, Any]]:
    budget = budget if budget is not None else {"n": 0}
    if depth > MAX_TREE_DEPTH or budget["n"] >= MAX_TREE_NODES:
        return []
    budget["n"] += 1

    node = describe_element(element)
    node["depth"] = depth
    nodes = [node]
    try:
        children = element.children()
    except Exception as exc:
        node["children_error"] = str(exc)[:200]
        children = []
    for child in children:
        nodes.extend(walk(child, depth + 1, budget))
    return nodes


def format_tree(nodes: list[dict[str, Any]]) -> str:
    lines = []
    for node in nodes:
        parts = [f"{'  ' * node['depth']}{node['control_type'] or '?'}"]
        if node["automation_id"]:
            parts.append(f"id={node['automation_id']!r}")
        if node["name"]:
            parts.append(f"name={node['name']!r}")
        if node["class_name"]:
            parts.append(f"class={node['class_name']!r}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


def summarise_tree(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers that decide whether UIA is usable here."""
    types: dict[str, int] = {}
    for node in nodes:
        control = node["control_type"] or "?"
        types[control] = types.get(control, 0) + 1
    return {
        "nodes": len(nodes),
        "max_depth": max((n["depth"] for n in nodes), default=0),
        "with_automation_id": sum(1 for n in nodes if n["automation_id"]),
        "with_name": sum(1 for n in nodes if n["name"]),
        "control_types": dict(sorted(types.items(), key=lambda kv: -kv[1])[:25]),
    }


def probe_display() -> dict[str, Any]:
    """Monitor layout and DPI awareness.

    Needed because anchors and regions are captured per monitor: a second
    screen at a different scale, or one placed left of the primary so its
    coordinates are negative, changes what a recorded flow means.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from flowrunner.geometry import enumerate_monitors, make_dpi_aware
    except Exception as exc:
        return {"error": f"could not import flowrunner.geometry: {exc}"}

    awareness = make_dpi_aware()
    monitors = [
        {"name": m.name, "left": m.left, "top": m.top, "right": m.right,
         "bottom": m.bottom, "width": m.width, "height": m.height,
         "primary": m.primary, "scale": m.scale}
        for m in enumerate_monitors()
    ]
    scales = {m["scale"] for m in monitors}
    return {
        "dpi_awareness": awareness,
        "monitors": monitors,
        "mixed_scaling": len(scales) > 1,
        "negative_origin": any(m["left"] < 0 or m["top"] < 0 for m in monitors),
    }


def probe_uia(title_pattern: str, out_dir: Path) -> dict[str, Any]:
    try:
        from pywinauto import Desktop
    except ImportError:
        return {"available": False,
                "error": "pywinauto not installed -- pip install pywinauto"}

    try:
        window = Desktop(backend="uia").window(title_re=_glob_to_regex(title_pattern))
        window.wait("exists", timeout=10)
    except Exception as exc:
        return {"available": True, "error": f"no window matching {title_pattern!r}: {exc}"}

    started = time.time()
    nodes = walk(window)
    elapsed = time.time() - started

    (out_dir / "uia-tree.txt").write_text(format_tree(nodes), encoding="utf-8")
    (out_dir / "uia-tree.json").write_text(json.dumps(nodes, indent=2), encoding="utf-8")

    result = {"available": True, "walk_seconds": round(elapsed, 1)}
    result.update(summarise_tree(nodes))
    try:
        window.capture_as_image().save(out_dir / "window.png")
        result["screenshot"] = "window.png"
    except Exception as exc:
        result["screenshot_error"] = str(exc)[:200]
    return result


def watch_focus(seconds: int, out_dir: Path) -> list[dict[str, Any]]:
    """Dump whatever has focus, once a second.

    The practical way to learn what UIA can see: click around the panel and read
    back what, if anything, it reported. This is the substitute for the
    right-click inspect that an embedded view does not give you.
    """
    try:
        from pywinauto import Desktop
    except ImportError:
        return []

    seen: list[dict[str, Any]] = []
    desktop = Desktop(backend="uia")
    for _ in range(seconds):
        try:
            element = desktop.get_focus()
            record = describe_element(element) if element else {"name": "(no focus)"}
        except Exception as exc:
            record = {"error": str(exc)[:200]}
        record["at"] = datetime.now().strftime("%H:%M:%S")
        if not seen or {k: v for k, v in record.items() if k != "at"} != {
            k: v for k, v in seen[-1].items() if k != "at"
        }:
            seen.append(record)
            print(f"  {record.get('at')}  {record.get('control_type', '?')}  "
                  f"id={record.get('automation_id', '')!r}  "
                  f"name={record.get('name', '')!r}")
        time.sleep(1)

    (out_dir / "focus-watch.json").write_text(json.dumps(seen, indent=2), encoding="utf-8")
    return seen


def _glob_to_regex(pattern: str) -> str:
    import re
    return "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"


# -- report --------------------------------------------------------------------


def verdict(cdp: list[dict[str, Any]], uia: dict[str, Any]) -> list[str]:
    lines = []
    if cdp:
        pages = sum(len(endpoint["pages"]) for endpoint in cdp)
        lines.append(
            f"BEST ROUTE: attach over CDP. {len(cdp)} endpoint(s), {pages} page(s). "
            f"Put this in the flow's target_app.web:"
        )
        lines.append(f"    cdp_url: \"{cdp[0]['cdp_url']}\"")
        if cdp[0]["pages"]:
            lines.append(f"    page_title_pattern: \"{cdp[0]['pages'][0]['title']}*\"")
        lines.append("  Full DOM access: exact text reads, real selectors, no OCR.")
        return lines

    lines.append("No CDP endpoint found. Either the host is not Chromium-based, or")
    lines.append("remote debugging is off. Worth trying before falling back to UIA:")
    lines.append("    WebView2  set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222")
    lines.append("    CEF       --remote-debugging-port=9222 on the host command line")

    if not uia.get("available"):
        lines.append(f"UIA not checked: {uia.get('error')}")
    elif uia.get("error"):
        lines.append(f"UIA failed: {uia['error']}")
    else:
        named = uia.get("with_name", 0)
        identified = uia.get("with_automation_id", 0)
        lines.append(
            f"UIA sees {uia['nodes']} node(s); {identified} have an AutomationId, "
            f"{named} have a Name."
        )
        if identified < 5:
            lines.append(
                "  Very few AutomationIds: expect to target by control type and "
                "name, and expect the canvas to be opaque. Pixel-stability and "
                "OCR are likely needed for reading responses."
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="*", help="window title glob, e.g. '*CATIA*'")
    parser.add_argument("--ports", default=",".join(str(p) for p in DEFAULT_PORTS))
    parser.add_argument("--watch", type=int, default=0,
                        help="seconds to report the focused element, once a second")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = Path(args.out or f"probe-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    print(f"probing ports {ports} ...")
    cdp = probe_cdp(ports)
    for endpoint in cdp:
        print(f"  FOUND {endpoint['cdp_url']}  {endpoint['browser']}")
        for page in endpoint["pages"]:
            print(f"        page {page['title']!r}  {page['url'][:90]}")
    if not cdp:
        print("  none")

    display = probe_display()
    print("display:")
    if display.get("error"):
        print(f"  {display['error']}")
    else:
        print(f"  DPI awareness: {display['dpi_awareness']}")
        for monitor in display["monitors"] or []:
            tag = " (primary)" if monitor["primary"] else ""
            print(f"  {monitor['name']}: {monitor['width']}x{monitor['height']} "
                  f"at ({monitor['left']}, {monitor['top']})"
                  f"{tag} @{monitor['scale']:g}x")
        if display.get("mixed_scaling"):
            print("  NOTE: monitors have different DPI scales. Capture anchors on "
                  "the monitor the run will use; they do not carry across.")
        if display.get("negative_origin"):
            print("  NOTE: a monitor sits left of or above the primary, so its "
                  "screen coordinates are negative. Expected, and handled.")

    print(f"walking the UIA tree for windows matching {args.title!r} ...")
    uia = probe_uia(args.title, out_dir)
    print(f"  {uia}")

    if args.watch:
        print(f"watching the focused element for {args.watch}s -- click around the panel now")
        watch_focus(args.watch, out_dir)

    report = {"timestamp": stamp, "title_pattern": args.title,
              "display": display, "cdp": cdp, "uia": uia,
              "verdict": verdict(cdp, uia)}
    (out_dir / "probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "\n".join(report["verdict"]))
    print(f"\nwritten to {out_dir}/ -- send probe.json and uia-tree.txt back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
