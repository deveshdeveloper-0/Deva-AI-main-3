"""Control an Android device authorized through Android Debug Bridge (ADB)."""
import os
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


_KEYCODES = {
    "back": "4",
    "home": "3",
    "recents": "187",
    "enter": "66",
    "delete": "67",
    "space": "62",
    "volume_up": "24",
    "volume_down": "25",
    "power": "26",
    "up": "19",
    "down": "20",
    "left": "21",
    "right": "22",
}


def _adb_executable() -> str | None:
    configured = os.environ.get("ANDROID_ADB", "").strip()
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(configured)

    found = shutil.which("adb")
    if found:
        return found

    sdk_roots = [
        os.environ.get("ANDROID_SDK_ROOT"),
        os.environ.get("ANDROID_HOME"),
    ]
    if os.name == "nt":
        sdk_roots.append(str(Path.home() / "AppData/Local/Android/Sdk"))
    elif sys.platform == "darwin":
        sdk_roots.append(str(Path.home() / "Library/Android/sdk"))
    else:
        sdk_roots.append(str(Path.home() / "Android/Sdk"))

    executable = "adb.exe" if os.name == "nt" else "adb"
    for root in sdk_roots:
        if root:
            candidate = Path(root).expanduser() / "platform-tools" / executable
            if candidate.is_file():
                return str(candidate)
    return None


def _devices(adb: str) -> tuple[list[tuple[str, str]], str]:
    result = subprocess.run(
        [adb, "devices"], capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode:
        return [], result.stderr.strip() or "ADB could not list devices."
    devices = []
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            devices.append((fields[0], fields[1]))
    return devices, ""


def _select_device(adb: str, serial: str = "") -> str:
    devices, error = _devices(adb)
    if error:
        raise RuntimeError(error)
    ready = [device_id for device_id, state in devices if state == "device"]
    unauthorized = [device_id for device_id, state in devices if state == "unauthorized"]

    if serial:
        if serial in ready:
            return serial
        if serial in unauthorized:
            raise RuntimeError("Unlock the phone and approve its USB debugging prompt.")
        raise RuntimeError(f"Android device '{serial}' is not connected and authorized.")
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        raise RuntimeError("More than one Android device is connected; specify its serial.")
    if unauthorized:
        raise RuntimeError("Unlock the phone and approve its USB debugging prompt.")
    raise RuntimeError("No authorized Android phone found. Connect it and enable USB debugging.")


def _run(adb: str, serial: str, *args: str, timeout: int = 20) -> str:
    result = subprocess.run(
        [adb, "-s", serial, *args], capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"ADB command failed (exit {result.returncode}).")
    return result.stdout.strip()


def android_control(parameters: dict, player=None) -> str:
    """Perform one allow-listed control action on an authorized Android device."""
    params = parameters or {}
    action = str(params.get("action", "")).strip().lower()
    if action not in {
        "devices", "open_app", "tap", "tap_text", "swipe", "type", "press",
        "back", "home", "recents",
    }:
        return f"Unknown Android control action: '{action}'."

    adb = _adb_executable()
    if not adb:
        return (
            "Android Platform-Tools (adb) not found. Install Android SDK Platform-Tools, "
            "add adb to PATH, or set ANDROID_ADB to its executable path."
        )

    if action == "devices":
        devices, error = _devices(adb)
        if error:
            return error
        if not devices:
            return "No Android devices found. Connect a phone and enable USB debugging."
        return "Android devices: " + ", ".join(
            f"{device_id} ({state})" for device_id, state in devices
        )

    try:
        serial = _select_device(adb, str(params.get("serial", "")).strip())
        if player:
            player.write_log(f"[Android] {action} on {serial}")

        if action == "open_app":
            package = str(params.get("package", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
                return "Provide a valid Android package name, such as com.android.settings."
            output = _run(
                adb, serial, "shell", "monkey", "-p", package, "1", timeout=30
            )
            return output or f"Launched {package}."

        if action == "tap":
            x, y = int(params["x"]), int(params["y"])
            if not 0 <= x <= 20000 or not 0 <= y <= 20000:
                return "Tap coordinates must be between 0 and 20000."
            _run(adb, serial, "shell", "input", "tap", str(x), str(y))
            return f"Tapped Android screen at ({x}, {y})."

        if action == "tap_text":
            target = str(params.get("text", "")).strip()
            if not target or len(target) > 100:
                return "Provide a UI label between 1 and 100 characters."
            _run(adb, serial, "shell", "uiautomator", "dump", "/sdcard/window.xml", timeout=15)
            layout = _run(adb, serial, "exec-out", "cat", "/sdcard/window.xml", timeout=10)
            root = ET.fromstring(layout)
            matches = []
            for node in root.iter():
                label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
                bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
                if label and bounds and target.casefold() in label.casefold():
                    x1, y1, x2, y2 = (int(value) for value in bounds.groups())
                    match = (label, (x1 + x2) // 2, (y1 + y2) // 2)
                    if match not in matches:
                        matches.append(match)
            exact = [match for match in matches if match[0].casefold() == target.casefold()]
            candidates = exact or matches
            if not candidates:
                return f"No Android screen label matched '{target}'."
            if len(candidates) > 1:
                labels = ", ".join(dict.fromkeys(match[0] for match in candidates[:5]))
                return f"More than one Android label matched '{target}': {labels}. No tap performed."
            label, x, y = candidates[0]
            _run(adb, serial, "shell", "input", "tap", str(x), str(y))
            return f"Tapped Android label '{label}'."

        if action == "swipe":
            coordinates = [int(params[key]) for key in ("x1", "y1", "x2", "y2")]
            if any(value < 0 or value > 20000 for value in coordinates):
                return "Swipe coordinates must be between 0 and 20000."
            duration = max(100, min(int(params.get("duration_ms", 350)), 10000))
            _run(adb, serial, "shell", "input", "swipe",
                 *(str(value) for value in coordinates), str(duration))
            return "Swiped Android screen."

        if action == "type":
            text = str(params.get("text", ""))
            if not text or len(text) > 500 or any(ord(char) < 32 for char in text):
                return "Text must be 1-500 characters and contain no control characters."
            encoded = shlex.quote(text.replace(" ", "%s"))
            _run(adb, serial, "shell", "input", "text", encoded)
            return f"Typed text on Android ({len(text)} characters)."

        key = action if action in ("back", "home", "recents") else str(
            params.get("key", "")
        ).strip().lower()
        keycode = _KEYCODES.get(key)
        if not keycode:
            return "Supported keys: back, home, recents, enter, delete, space, volume_up, volume_down, power, up, down, left, right."
        _run(adb, serial, "shell", "input", "keyevent", keycode)
        return f"Pressed Android {key}."
    except (KeyError, TypeError, ValueError) as exc:
        return f"Invalid Android control parameters: {exc}"
    except (ET.ParseError, OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return f"Android control failed: {exc}"


TOOL = {
    "name": "android_control",
    "description": (
        "Control an Android phone connected and authorized with ADB. Check devices, "
        "launch apps by package name, tap UI labels or screen coordinates, swipe, "
    "type text, and use navigation keys. Only act on the user's paired Android device."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "devices | open_app | tap | tap_text | swipe | type | press | back | home | recents",
            },
            "serial": {"type": "STRING", "description": "ADB device serial when multiple phones are connected"},
            "package": {"type": "STRING", "description": "Android app package name for open_app"},
            "x": {"type": "INTEGER", "description": "Tap x coordinate"},
            "y": {"type": "INTEGER", "description": "Tap y coordinate"},
            "x1": {"type": "INTEGER", "description": "Swipe start x coordinate"},
            "y1": {"type": "INTEGER", "description": "Swipe start y coordinate"},
            "x2": {"type": "INTEGER", "description": "Swipe end x coordinate"},
            "y2": {"type": "INTEGER", "description": "Swipe end y coordinate"},
            "duration_ms": {"type": "INTEGER", "description": "Swipe duration in milliseconds (100-10000)"},
            "text": {"type": "STRING", "description": "Text to type or visible UI label to tap with tap_text"},
            "key": {"type": "STRING", "description": "Key for press: enter, delete, space, volume_up/down, power, or d-pad direction"},
        },
        "required": ["action"],
    },
    "handler": android_control,
}