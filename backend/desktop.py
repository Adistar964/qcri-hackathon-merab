"""
Desktop capabilities — the agent's eyes and hands on the real computer.

This is what makes the *desktop* build more powerful than the web build:
  • see_screen   : capture the real screen and UNDERSTAND it with Fanar vision
                   (Fanar-Oryx-IVU-2) — the agent literally reads what's on screen.
  • mouse_click  : move + click the real mouse
  • type_text    : type into whatever app is focused
  • press_keys   : send a hotkey (e.g. ctrl+c, win+d)
  • open_application : launch an app by name
  • scroll       : scroll the active window

Acting tools (everything except see_screen) are "risky": the agent loop is wired
to PAUSE and ask the human to approve before they run (see agent.py). We never run a
shell command here — that is intentionally out of scope for safety.

Screen capture uses `mss` (fast) and control uses `pyautogui`. Both require a real
desktop session, so these tools are only enabled when the request comes from the
Electron desktop app (surface == "desktop").
"""

from __future__ import annotations

import base64
import io
import os
import pathlib
from typing import Any

WORKSPACE = pathlib.Path(os.getenv("AGENT_WORKSPACE", "agent_workspace")).resolve()
SHOTS_DIR = WORKSPACE / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

_shot_counter = {"n": 0}


def _capture_png(max_side: int = 1280) -> tuple[bytes, str, tuple[int, int]]:
    """Grab the primary screen. Returns (png_bytes, saved_filename, (w, h))."""
    import mss  # imported lazily so the web build doesn't need it
    from PIL import Image

    with mss.MSS() as sct:
        mon = sct.monitors[1]
        raw = sct.grab(mon)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    full_size = img.size
    shot = img.copy()
    shot.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    png = buf.getvalue()

    _shot_counter["n"] += 1
    name = f"screen_{_shot_counter['n']:03d}.png"
    (SHOTS_DIR / name).write_bytes(png)
    return png, name, full_size


def _scale_point(x: int, y: int, shot_size: tuple[int, int], full_size: tuple[int, int]) -> tuple[int, int]:
    """Map a coordinate from the (downscaled) screenshot space to real screen pixels."""
    sw, sh = shot_size
    fw, fh = full_size
    if not sw or not sh:
        return x, y
    return int(x * fw / sw), int(y * fh / sh)


# --------------------------------------------------------------------------- #
# Tools (the `client` is the FanarClient, injected by the agent loop)
# --------------------------------------------------------------------------- #
def see_screen(client, question: str = "What is currently on the screen?") -> dict[str, Any]:
    """Screenshot the real screen and have Fanar vision describe / answer about it."""
    png, name, full = _capture_png()
    b64 = base64.b64encode(png).decode()
    try:
        understanding = client.see_image(
            b64,
            f"You are an assistant looking at a user's computer screen. {question} "
            f"Be concise and specific; mention key UI elements, apps, text, buttons or fields you see.",
        )
    except Exception as exc:  # noqa: BLE001
        understanding = f"(vision unavailable: {exc})"
    return {
        "screenshot": name,
        "screen_size": list(full),
        "understanding": understanding,
    }


# --------------------------------------------------------------------------- #
# Set-of-Marks for the DESKTOP: enumerate native clickable controls via Windows
# UI Automation, draw numbered boxes, let Fanar vision pick a box number, then
# click that control's real screen coordinates. (Same idea as the browser SoM,
# but grounded on the OS accessibility tree instead of the DOM.)
# --------------------------------------------------------------------------- #
_SCREEN_MARKS: dict[str, dict[int, tuple[int, int]]] = {}

_CLICKABLE_TYPES = {
    "ButtonControl", "EditControl", "CheckBoxControl", "ComboBoxControl",
    "HyperlinkControl", "MenuItemControl", "ListItemControl", "RadioButtonControl",
    "TabItemControl", "TreeItemControl", "SplitButtonControl",
}


def _enumerate_controls(max_n: int = 55):
    import uiautomation as auto

    fg = auto.GetForegroundControl()
    # Walk the whole top-level window, not just the focused control.
    root = None
    if fg:
        try:
            root = fg.GetTopLevelControl()
        except Exception:  # noqa: BLE001
            root = fg
    root = root or auto.GetRootControl()
    items = []
    try:
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=28):
            try:
                if ctrl.ControlTypeName not in _CLICKABLE_TYPES or not ctrl.IsEnabled:
                    continue
                r = ctrl.BoundingRectangle
                if r.width() <= 3 or r.height() <= 3:
                    continue
                name = (ctrl.Name or ctrl.ControlTypeName).strip()[:40]
                items.append((ctrl.ControlTypeName, name, r))
                if len(items) >= max_n:
                    break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return items


def see_screen_marks(client, session_id: str = "default", question: str = "") -> dict[str, Any]:
    """Screenshot + numbered boxes over native clickable controls; Fanar vision reads it."""
    import base64
    import io

    import mss
    from PIL import Image, ImageDraw

    with mss.MSS() as sct:
        mon = sct.monitors[1]
        raw = sct.grab(mon)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    draw = ImageDraw.Draw(img)
    origin_x, origin_y = mon["left"], mon["top"]

    controls = _enumerate_controls()
    marks: list[dict[str, Any]] = []
    centers: dict[int, tuple[int, int]] = {}
    for i, (ctype, name, r) in enumerate(controls, 1):
        left, top = r.left - origin_x, r.top - origin_y
        right, bottom = r.right - origin_x, r.bottom - origin_y
        draw.rectangle([left, top, right, bottom], outline=(76, 141, 255), width=2)
        draw.rectangle([left, max(0, top - 15), left + 10 + 8 * len(str(i)), top], fill=(76, 141, 255))
        draw.text((left + 2, max(0, top - 14)), str(i), fill=(4, 18, 43))
        marks.append({"n": i, "type": ctype.replace("Control", ""), "name": name})
        centers[i] = (r.xcenter(), r.ycenter())  # real screen coords for clicking

    _SCREEN_MARKS[session_id] = centers

    _shot_counter["n"] += 1
    name = f"screen_{_shot_counter['n']:03d}.png"
    annotated = img.copy()
    annotated.thumbnail((1280, 1280))
    annotated.save(SHOTS_DIR / name)
    buf = io.BytesIO()
    annotated.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    try:
        understanding = client.see_image(
            b64,
            "This is the user's screen. Clickable elements are outlined with numbered blue boxes. "
            + (question or "Describe what's on screen and which numbered boxes are relevant.")
            + " Refer to elements by their box number.",
        )
    except Exception as exc:  # noqa: BLE001
        understanding = f"(vision unavailable: {exc})"

    return {"screenshot": name, "understanding": understanding, "marks": marks, "screen_size": list(img.size)}


def click_mark_screen(session_id: str = "default", n: int = 0, double: bool = False) -> dict[str, Any]:
    """Click the screen control previously labelled #n by see_screen_marks."""
    import pyautogui

    centers = _SCREEN_MARKS.get(session_id, {})
    target = centers.get(int(n))
    if not target:
        return {"error": f"No box #{n}. Call see_screen_marks first to label the screen."}
    pyautogui.moveTo(target[0], target[1], duration=0.3)
    pyautogui.click(clicks=2 if double else 1)
    png, shot, _ = _capture_png()
    return {"clicked_mark": n, "at": list(target), "screenshot": shot}


def mouse_click(x: int = 0, y: int = 0, button: str = "left", double: bool = False) -> dict[str, Any]:
    """Click at screen coordinates (in screenshot space; auto-scaled to real pixels)."""
    import pyautogui

    pyautogui.FAILSAFE = True
    # coordinates are given relative to the most recent screenshot; rescale
    png, name, full = _capture_png()
    shot_w = min(1280, full[0]) or full[0]
    ratio = full[0] / shot_w if shot_w else 1
    shot_size = (int(full[0] / ratio), int(full[1] / ratio))
    rx, ry = _scale_point(int(x), int(y), shot_size, full)
    pyautogui.moveTo(rx, ry, duration=0.3)
    pyautogui.click(button=button, clicks=2 if double else 1)
    return {"clicked": {"x": rx, "y": ry, "button": button, "double": double}, "screenshot": name}


def type_text(text: str = "") -> dict[str, Any]:
    """Type text into the currently focused field."""
    import pyautogui

    pyautogui.typewrite(text, interval=0.02)
    return {"typed": text}


def press_keys(keys: str = "") -> dict[str, Any]:
    """Press a hotkey combination, e.g. 'ctrl+c', 'win+d', 'enter'."""
    import pyautogui

    combo = [k.strip().lower() for k in keys.replace("+", " ").split() if k.strip()]
    if not combo:
        return {"error": "No keys provided"}
    pyautogui.hotkey(*combo)
    return {"pressed": combo}


def scroll(amount: int = -400) -> dict[str, Any]:
    """Scroll the active window. Negative scrolls down, positive scrolls up."""
    import pyautogui

    pyautogui.scroll(int(amount))
    return {"scrolled": amount}


# NOTE: There is intentionally NO app-launcher tool. Every external service/task
# must be done in the BROWSER (e.g. Gmail -> mail.google.com), never by opening a
# native application. The desktop tools below only see/interact with what is
# already on the user's screen.

# Registry metadata used by the agent's tool schemas + risk gating.
DESKTOP_TOOLS = {
    "see_screen_marks": see_screen_marks,
    "see_screen": see_screen,
    "click_mark_screen": click_mark_screen,
    "mouse_click": mouse_click,
    "type_text": type_text,
    "press_keys": press_keys,
    "scroll": scroll,
}

# Tools that need FanarClient injected.
DESKTOP_VISION_TOOLS = {"see_screen", "see_screen_marks"}

# Tools that also need the session_id injected.
DESKTOP_SESSION_TOOLS = {"see_screen_marks", "click_mark_screen"}

# Tools that must be human-approved before running. Scrolling is intentionally NOT here — it's
# harmless navigation (no click/type/delete), so prompting "Approve action — Scroll?" is just noise.
DESKTOP_RISKY_TOOLS = {"click_mark_screen", "mouse_click", "type_text", "press_keys"}

DESKTOP_SCHEMAS = [
    {"name": "see_screen_marks", "description": "PREFERRED way to act on the user's CURRENT screen: screenshots it and draws NUMBERED boxes over every clickable control, then Fanar vision reads it. Use this, then click_mark_screen. (For any external service/task, use the browser instead.)", "args": {"question": "string - what you're looking for (optional)"}},
    {"name": "click_mark_screen", "description": "Click the numbered box from the most recent see_screen_marks (e.g. n=14). Requires user approval.", "args": {"n": "int - the box number", "double": "bool (optional)"}},
    {"name": "see_screen", "description": "Plain screenshot + Fanar-vision description (no boxes). Use see_screen_marks instead when you need to click.", "args": {"question": "string (optional)"}},
    {"name": "type_text", "description": "Type text into the focused field on screen. Requires user approval.", "args": {"text": "string"}},
    {"name": "press_keys", "description": "Press a hotkey like 'ctrl+s' or 'enter'. Requires user approval.", "args": {"keys": "string"}},
    {"name": "scroll", "description": "Scroll the active window (negative = down). Runs immediately — no approval needed.", "args": {"amount": "int"}},
]
