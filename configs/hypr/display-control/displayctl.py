#!/usr/bin/env python3
"""Display layouts and boot-scoped idle controls for Hyprland.

All compositor interaction uses argv-based subprocess calls.  Saved profiles
are deliberately outside the dotfiles repository: they describe hardware and
are selected by monitor identity rather than unstable connector names.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


APP_ID = "hypr-display-control"
APP_NAME = "Display & Idle"
DEFAULT_LOCK_SECONDS = 120
DISPLAY_OFF_DELAY = 30
SUSPEND_AFTER_DISPLAY_OFF = 300
ROLLBACK_SECONDS = 15
MODE_RE = re.compile(r"^(\d+)x(\d+)@([0-9.]+)Hz$")


def state_root() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"unsafe state directory: {root}")
    os.chmod(root, 0o700)
    return root


def runtime_root() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    root = base / APP_ID
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"unsafe runtime directory: {root}")
    os.chmod(root, 0o700)
    return root


def checked_path(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"refusing symbolic-link state file: {path}")
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        checked_path(path)
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    checked_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def run(argv: list[str], *, timeout: int = 10, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=check)


def hyprctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["hyprctl", *args], check=check)


def monitor_json() -> list[dict[str, Any]]:
    result = hyprctl("monitors", "all", "-j")
    monitors = json.loads(result.stdout)
    if not isinstance(monitors, list):
        raise RuntimeError("Hyprland returned an invalid monitor list")
    return monitors


def monitor_description(monitor: dict[str, Any]) -> str:
    description = str(monitor.get("description") or "").strip()
    # Older Hyprland versions append the connector in parentheses.
    suffix = f" ({monitor.get('name', '')})"
    return description[: -len(suffix)].strip() if suffix and description.endswith(suffix) else description


def identity_payload(monitor: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": monitor_description(monitor),
        "make": str(monitor.get("make") or "").strip(),
        "model": str(monitor.get("model") or "").strip(),
        "serial": str(monitor.get("serial") or "").strip(),
        "physical_width": int(monitor.get("physicalWidth") or 0),
        "physical_height": int(monitor.get("physicalHeight") or 0),
    }


def fingerprint(monitor: dict[str, Any]) -> str:
    identity = identity_payload(monitor)
    # The serial is preferred, while description (which includes it on most
    # external monitors) is a usable fallback for integrated panels.
    material = "|".join(str(identity[key]) for key in sorted(identity))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def connected_set_key(monitors: Iterable[dict[str, Any]]) -> str:
    return ":".join(sorted(fingerprint(monitor) for monitor in monitors))


def mode_from_monitor(monitor: dict[str, Any]) -> str:
    return f"{int(monitor.get('width', 0))}x{int(monitor.get('height', 0))}@{float(monitor.get('refreshRate', 0)):.2f}Hz"


def parse_mode(mode: str) -> tuple[int, int, float]:
    matched = MODE_RE.fullmatch(mode)
    if not matched:
        raise ValueError(f"unsupported display mode: {mode!r}")
    return int(matched.group(1)), int(matched.group(2)), float(matched.group(3))


def hypr_mode(mode: str) -> str:
    """Convert Hyprland JSON's display label to monitor-keyword syntax.

    `hyprctl monitors -j` returns modes such as ``1920x1080@60.00Hz`` while
    the monitor keyword accepts ``1920x1080@60.00``.  Passing the JSON label
    is silently accepted by hyprctl on some versions but leaves the output
    unchanged, which made the old Apply button appear to fail.
    """
    width, height, refresh = parse_mode(mode)
    return f"{width}x{height}@{refresh:.2f}".rstrip("0").rstrip(".")


def logical_size(config: dict[str, Any]) -> tuple[int, int]:
    width, height, _ = parse_mode(str(config["mode"]))
    if int(config.get("transform", 0)) in {1, 3, 5, 7}:
        width, height = height, width
    scale = float(config.get("scale", 1))
    return (round(width / scale), round(height / scale))


def valid_scales(mode: str) -> list[float]:
    width, height, _ = parse_mode(mode)
    candidates: list[float] = []
    # Hyprland represents scales as increments of 1/120.  Only expose scales
    # which leave integral logical dimensions.
    for numerator in range(60, 361):  # 0.5 through 3.0
        scale = numerator / 120
        if width * 120 % numerator == 0 and height * 120 % numerator == 0:
            candidates.append(scale)
    return candidates


def normalize_monitor(monitor: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": identity_payload(monitor),
        "fingerprint": fingerprint(monitor),
        "name": str(monitor.get("name") or ""),
        "enabled": not bool(monitor.get("disabled", False)),
        "mode": mode_from_monitor(monitor),
        "x": int(monitor.get("x", 0)),
        "y": int(monitor.get("y", 0)),
        "scale": float(monitor.get("scale", 1)),
        "transform": int(monitor.get("transform", 0)),
        "mirror_of": None if str(monitor.get("mirrorOf", "none")) == "none" else str(monitor.get("mirrorOf")),
        "available_modes": list(monitor.get("availableModes") or []),
    }


def configs_from_monitors(monitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    configs = [normalize_monitor(monitor) for monitor in monitors]
    # Hyprland reports mirrorOf as either a connector name or the source's
    # numeric monitor ID (for example "0"). Store connector names everywhere
    # else so profiles remain readable and survive monitor-ID renumbering.
    names_by_id = {str(monitor.get("id")): str(monitor.get("name")) for monitor in monitors}
    for config in configs:
        mirror = config.get("mirror_of")
        if mirror in names_by_id:
            config["mirror_of"] = names_by_id[mirror]
    return configs


def validate_layout(configs: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    names = {str(config.get("name", "")) for config in configs}
    enabled = [config for config in configs if config.get("enabled")]
    if not enabled:
        return ["At least one display must remain enabled."]
    for config in configs:
        name = str(config.get("name", ""))
        if not name or not re.fullmatch(r"[A-Za-z0-9_.:+-]+", name):
            errors.append("Invalid output name.")
        if not config.get("enabled"):
            continue
        try:
            width, height, _ = parse_mode(str(config["mode"]))
            scale = float(config["scale"])
            transform = int(config.get("transform", 0))
            if transform not in range(8):
                raise ValueError("transform")
            if scale not in valid_scales(str(config["mode"])):
                errors.append(f"{name}: scale {scale:g} does not produce integral logical pixels.")
            if width <= 0 or height <= 0:
                raise ValueError("mode")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name}: invalid mode, scale, or rotation.")
        mirror = config.get("mirror_of")
        if mirror:
            if mirror == name or mirror not in names:
                errors.append(f"{name}: invalid mirror source.")
            source = next((candidate for candidate in configs if candidate.get("name") == mirror), None)
            if not source or not source.get("enabled") or source.get("mirror_of"):
                errors.append(f"{name}: mirror source must be an enabled, non-mirrored display.")
    extended = [config for config in enabled if not config.get("mirror_of")]
    for index, first in enumerate(extended):
        try:
            first_width, first_height = logical_size(first)
        except (KeyError, ValueError):
            continue
        for second in extended[index + 1 :]:
            try:
                second_width, second_height = logical_size(second)
            except (KeyError, ValueError):
                continue
            overlap = not (
                int(first["x"]) + first_width <= int(second["x"])
                or int(second["x"]) + second_width <= int(first["x"])
                or int(first["y"]) + first_height <= int(second["y"])
                or int(second["y"]) + second_height <= int(first["y"])
            )
            if overlap:
                errors.append(f"{first['name']} overlaps {second['name']}.")
    return errors


def monitor_rule(config: dict[str, Any]) -> str:
    name = str(config["name"])
    if not config.get("enabled"):
        return f"{name},disable"
    rule = f"{name},{hypr_mode(str(config['mode']))},{int(config['x'])}x{int(config['y'])},{float(config['scale']):g},transform,{int(config.get('transform', 0))}"
    mirror = config.get("mirror_of")
    if mirror:
        rule += f",mirror,{mirror}"
    return rule


def refresh_desktop_surfaces() -> None:
    """Recreate per-output background and bar surfaces after a layout move.

    Both Waybar and Hyprpaper can retain layer surfaces at their previous
    global coordinates after an output is moved, mirrored, or disabled.
    Starting them again lets them bind to the compositor's updated layout.
    Failures are intentionally non-fatal: the monitor layout itself remains
    valid even if either optional desktop component is unavailable.
    """
    subprocess.run(["pkill", "-x", "hyprpaper"], check=False, capture_output=True)
    # Give the old layer-shell client a moment to release its surfaces before
    # creating the replacement.
    time.sleep(0.1)
    subprocess.Popen(["hyprpaper"], close_fds=True)
    launcher = Path.home() / ".config/waybar/launch.sh"
    if launcher.is_file() and not launcher.is_symlink():
        subprocess.Popen(["/usr/bin/bash", str(launcher)], close_fds=True)
    else:
        subprocess.run(["pkill", "-x", "waybar"], check=False, capture_output=True)
        subprocess.Popen(["waybar"], close_fds=True)


def apply_layout(configs: list[dict[str, Any]]) -> None:
    errors = validate_layout(configs)
    if errors:
        raise ValueError("\n".join(errors))
    # Sources must exist before their mirrors. Disabling is last so a usable
    # output is always present while a profile is being transitioned.
    ordered = (
        [config for config in configs if config.get("enabled") and not config.get("mirror_of")]
        + [config for config in configs if config.get("enabled") and config.get("mirror_of")]
        + [config for config in configs if not config.get("enabled")]
    )
    for config in ordered:
        result = hyprctl("keyword", "monitor", monitor_rule(config), check=False)
        if result.returncode != 0 or result.stdout.strip().lower().startswith("error"):
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to configure {config['name']}")
    refresh_desktop_surfaces()


def layout_matches(configs: list[dict[str, Any]], monitors: list[dict[str, Any]]) -> bool:
    actual = {str(config["fingerprint"]): config for config in configs_from_monitors(monitors)}
    for config in configs:
        observed = actual.get(str(config.get("fingerprint", "")))
        if observed is None or bool(observed["enabled"]) != bool(config["enabled"]):
            return False
        # A mirror inherits its rendered size and layout coordinates from its
        # source. Hyprland consequently reports source-derived geometry for
        # the mirror rather than the staged mode/position. Its relationship is
        # the only meaningful state to verify here.
        if config["enabled"] and config.get("mirror_of"):
            if observed.get("mirror_of") != config.get("mirror_of"):
                return False
            continue
        if config["enabled"] and (
            observed["mode"] != config["mode"]
            or observed["x"] != int(config["x"])
            or observed["y"] != int(config["y"])
            or not math.isclose(observed["scale"], float(config["scale"]), rel_tol=0, abs_tol=0.01)
            or observed["transform"] != int(config.get("transform", 0))
            or observed.get("mirror_of") is not None
        ):
            return False
    return True


def profiles_path() -> Path:
    return state_root() / "profiles.json"


def load_profiles() -> dict[str, Any]:
    value = read_json(profiles_path(), {"version": 1, "profiles": {}})
    if not isinstance(value, dict) or not isinstance(value.get("profiles"), dict):
        return {"version": 1, "profiles": {}}
    return value


def save_profile(configs: list[dict[str, Any]]) -> None:
    profile_state = load_profiles()
    key = ":".join(sorted(str(config["fingerprint"]) for config in configs))
    profile_state["profiles"][key] = {"saved_at": int(time.time()), "monitors": configs}
    write_json_atomic(profiles_path(), profile_state)


def config_for_current_monitor(saved: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    config = deepcopy(saved)
    config["name"] = str(current["name"])
    if config.get("mirror_of"):
        # Resolve after all profiles have been mapped; temporary fingerprint is
        # kept by save_profile for this purpose.
        config["mirror_of_fingerprint"] = config.get("mirror_of_fingerprint")
    return config


def apply_matching_profile() -> bool:
    monitors = monitor_json()
    # A pending confirmation owns the layout. Without this guard a hotplug
    # event caused by its monitor reconfiguration could reapply the old saved
    # profile before the user can confirm the new one.
    if pending_path().exists():
        return False
    active = [monitor for monitor in monitors if not monitor.get("disabled", False)]
    # Some setups briefly emit monitor removal/addition events while all
    # outputs are DPMS-off. Never wake displays or rewrite their layout then.
    if active and not any(bool(monitor.get("dpmsStatus", True)) for monitor in active):
        return False
    profiles = load_profiles().get("profiles", {})
    profile = profiles.get(connected_set_key(monitors))
    if not profile:
        return False
    current_by_fp = {fingerprint(monitor): monitor for monitor in monitors}
    configs: list[dict[str, Any]] = []
    saved_by_name = {str(item.get("name")): item for item in profile.get("monitors", [])}
    for saved in profile.get("monitors", []):
        current = current_by_fp.get(str(saved.get("fingerprint")))
        if not current:
            return False
        config = config_for_current_monitor(saved, current)
        source_name = saved.get("mirror_of")
        if source_name:
            source = saved_by_name.get(str(source_name))
            if not source or str(source.get("fingerprint")) not in current_by_fp:
                return False
            config["mirror_of"] = str(current_by_fp[str(source["fingerprint"])]["name"])
        configs.append(config)
    errors = validate_layout(configs)
    if errors:
        return False
    if layout_matches(configs, monitors):
        return False
    apply_layout(configs)
    return True


def pending_path() -> Path:
    return runtime_root() / "pending-layout.json"


def start_rollback_watch(snapshot: list[dict[str, Any]]) -> None:
    path = pending_path()
    write_json_atomic(path, {"snapshot": snapshot, "created_at": time.time()})
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "rollback-watch", str(path), str(ROLLBACK_SECONDS)], close_fds=True)


def rollback_watch(path: Path, seconds: int) -> int:
    time.sleep(seconds)
    pending = read_json(path, None)
    if not isinstance(pending, dict) or not isinstance(pending.get("snapshot"), list):
        return 0
    try:
        apply_layout(pending["snapshot"])
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    return 0


def confirm_pending() -> None:
    with contextlib.suppress(FileNotFoundError):
        pending_path().unlink()


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def idle_override_path() -> Path:
    return state_root() / "idle-override.json"


def get_idle_override() -> int | None | str:
    value = read_json(idle_override_path(), {})
    if value.get("boot_id") != boot_id():
        with contextlib.suppress(FileNotFoundError):
            idle_override_path().unlink()
        return None
    timeout = value.get("lock_timeout")
    return "never" if timeout == "never" else (int(timeout) if isinstance(timeout, int) and timeout > 0 else None)


def set_idle_override(lock_timeout: int | str | None) -> None:
    if lock_timeout is None:
        with contextlib.suppress(FileNotFoundError):
            idle_override_path().unlink()
        return
    if lock_timeout != "never" and (not isinstance(lock_timeout, int) or lock_timeout < 15 or lock_timeout > 86400):
        raise ValueError("lock timeout must be between 15 seconds and 24 hours")
    write_json_atomic(idle_override_path(), {"boot_id": boot_id(), "lock_timeout": lock_timeout})


def idle_timeouts() -> tuple[int, int, int] | None:
    value = get_idle_override()
    if value == "never":
        return None
    lock = DEFAULT_LOCK_SECONDS if value is None else int(value)
    return lock, lock + DISPLAY_OFF_DELAY, lock + DISPLAY_OFF_DELAY + SUSPEND_AFTER_DISPLAY_OFF


def generated_idle_config() -> str:
    timeouts = idle_timeouts()
    lines = [
        "general {",
        "    lock_cmd = pidof hyprlock || hyprlock",
        "    before_sleep_cmd = loginctl lock-session",
        "    after_sleep_cmd = hyprctl dispatch dpms on",
        "}",
        "",
    ]
    if timeouts is None:
        lines.append("# Idle actions are disabled for this boot only.")
    else:
        lock, display_off, suspend = timeouts
        lines += [
            "listener {",
            f"    timeout = {lock}",
            "    on-timeout = loginctl lock-session",
            "}",
            "",
            "listener {",
            f"    timeout = {display_off}",
            "    on-timeout = hyprctl dispatch dpms off",
            "    on-resume = hyprctl dispatch dpms on",
            "}",
            "",
            "listener {",
            f"    timeout = {suspend}",
            "    on-timeout = systemctl suspend",
            "}",
        ]
    return "\n".join(lines) + "\n"


def idle_supervisor(replace: bool = False) -> int:
    lock_path = runtime_root() / "idle-supervisor.lock"
    lock_handle = lock_path.open("w")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    if replace:
        subprocess.run(["pkill", "-x", "hypridle"], check=False)
        time.sleep(0.15)
    config_path = runtime_root() / "hypridle.conf"
    child: subprocess.Popen[Any] | None = None
    signature: str | None = None
    running = True

    def stop_child() -> None:
        nonlocal child
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
        child = None

    def stop(_: int, __: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            content = generated_idle_config()
            if content != signature or child is None or child.poll() is not None:
                stop_child()
                config_path.write_text(content, encoding="utf-8")
                os.chmod(config_path, 0o600)
                child = subprocess.Popen(["hypridle", "-c", str(config_path)], close_fds=True)
                signature = content
            time.sleep(1)
    finally:
        stop_child()
        lock_handle.close()
    return 0


def profile_watch() -> int:
    lock_path = runtime_root() / "profile-watch.lock"
    lock_handle = lock_path.open("w")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    with contextlib.suppress(Exception):
        apply_matching_profile()
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not signature or not runtime:
        return 0
    event_socket = Path(runtime) / "hypr" / signature / ".socket2.sock"
    deadline: float | None = None
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.connect(str(event_socket))
                stream.settimeout(0.25)
                buffer = b""
                while True:
                    try:
                        packet = stream.recv(4096)
                    except TimeoutError:
                        packet = None
                    if packet is None:
                        pass
                    elif packet:
                        buffer += packet
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            event = raw.decode("utf-8", "replace")
                            if event.startswith(("monitoradded", "monitorremoved", "configreloaded")):
                                deadline = time.monotonic() + 1.5
                    else:
                        # The compositor closed its event connection. Reopen
                        # it so the watcher survives a Hyprland restart.
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        deadline = None
                        with contextlib.suppress(Exception):
                            apply_matching_profile()
        except (FileNotFoundError, ConnectionError, OSError):
            time.sleep(2)


def short_identity(config: dict[str, Any]) -> str:
    identity = config.get("identity", {})
    return str(identity.get("description") or f"Display {config.get('name', '')}")


def run_panel() -> int:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk

    class DisplayControl(Gtk.Window):
        def __init__(self) -> None:
            super().__init__(title=APP_NAME)
            self.set_wmclass(APP_ID, APP_ID)
            self.set_default_size(920, 680)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.set_resizable(True)
            self.set_decorated(False)
            self.connect("key-press-event", self.on_key_press)
            self.connect("destroy", lambda *_: Gtk.main_quit())
            self.current: list[dict[str, Any]] = []
            self.staged: list[dict[str, Any]] = []
            self.selected = 0
            self.dragging = False
            self.drag_origin: tuple[float, float, int, int] | None = None
            self.build_ui()
            self.reload()

        def build_ui(self) -> None:
            provider = Gtk.CssProvider()
            css = Path(__file__).with_name("style.css")
            provider.load_from_path(str(css))
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            outer.get_style_context().add_class("control-window")
            self.add(outer)

            header = Gtk.Box(spacing=10)
            header.get_style_context().add_class("header")
            header.set_border_width(14)
            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Label(label="Display & Idle")
            title.set_xalign(0)
            title.get_style_context().add_class("title")
            subtitle = Gtk.Label(label="Configure this monitor set and temporary idle protection")
            subtitle.set_xalign(0)
            subtitle.get_style_context().add_class("subtitle")
            title_box.pack_start(title, False, False, 0)
            title_box.pack_start(subtitle, False, False, 0)
            header.pack_start(title_box, True, True, 0)
            for label, callback in (("Reload", self.reload), ("Close", lambda *_: self.destroy())):
                button = Gtk.Button(label=label)
                button.connect("clicked", callback)
                header.pack_start(button, False, False, 0)
            outer.pack_start(header, False, False, 0)

            notebook = Gtk.Notebook()
            notebook.set_border_width(14)
            outer.pack_start(notebook, True, True, 0)
            notebook.append_page(self.build_display_page(), Gtk.Label(label="Displays"))
            notebook.append_page(self.build_idle_page(), Gtk.Label(label="Idle & Sleep"))

        def build_display_page(self) -> Gtk.Widget:
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            quick = Gtk.Box(spacing=7)
            for label, callback in (
                ("Internal only", self.internal_only),
                ("External only", self.external_only),
                ("Extend right", self.extend_right),
                ("Mirror", self.mirror),
            ):
                button = Gtk.Button(label=label)
                button.connect("clicked", callback)
                quick.pack_start(button, False, False, 0)
            root.pack_start(quick, False, False, 0)

            body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            root.pack_start(body, True, True, 0)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.canvas = Gtk.DrawingArea()
            self.canvas.set_size_request(430, 360)
            self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
            self.canvas.connect("draw", self.draw_canvas)
            self.canvas.connect("button-press-event", self.canvas_press)
            self.canvas.connect("button-release-event", self.canvas_release)
            self.canvas.connect("motion-notify-event", self.canvas_motion)
            left.pack_start(self.canvas, True, True, 0)
            self.status = Gtk.Label()
            self.status.set_xalign(0)
            self.status.set_line_wrap(True)
            self.status.get_style_context().add_class("status")
            left.pack_start(self.status, False, False, 0)
            body.pack1(left, True, False)
            self.inspector = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.inspector.set_border_width(4)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.inspector)
            body.pack2(scroll, True, False)

            footer = Gtk.Box(spacing=8)
            footer.set_halign(Gtk.Align.END)
            reset = Gtk.Button(label="Discard changes")
            reset.connect("clicked", lambda *_: self.reload())
            apply_button = Gtk.Button(label="Apply…")
            apply_button.get_style_context().add_class("suggested-action")
            apply_button.connect("clicked", self.apply_clicked)
            footer.pack_start(reset, False, False, 0)
            footer.pack_start(apply_button, False, False, 0)
            root.pack_start(footer, False, False, 0)
            return root

        def build_idle_page(self) -> Gtk.Widget:
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            root.set_border_width(12)
            heading = Gtk.Label(label="Temporary idle timeout")
            heading.set_xalign(0)
            heading.get_style_context().add_class("section-title")
            root.pack_start(heading, False, False, 0)
            description = Gtk.Label(label="Changes apply to this boot only. Display-off is always 30 seconds after lock; suspend is always 5 minutes after display-off. The same sequence applies on battery and AC.")
            description.set_xalign(0)
            description.set_line_wrap(True)
            description.get_style_context().add_class("subtitle")
            root.pack_start(description, False, False, 0)
            self.idle_status = Gtk.Label()
            self.idle_status.set_xalign(0)
            self.idle_status.get_style_context().add_class("status")
            root.pack_start(self.idle_status, False, False, 0)
            presets = Gtk.FlowBox()
            presets.set_selection_mode(Gtk.SelectionMode.NONE)
            for seconds, label in ((60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"), (900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour")):
                button = Gtk.Button(label=label)
                button.connect("clicked", lambda _, value=seconds: self.set_idle(value))
                presets.add(button)
            root.pack_start(presets, False, False, 0)
            custom = Gtk.Box(spacing=8)
            custom.pack_start(Gtk.Label(label="Custom minutes:"), False, False, 0)
            self.custom_idle = Gtk.SpinButton.new_with_range(1, 1440, 1)
            custom.pack_start(self.custom_idle, False, False, 0)
            custom_button = Gtk.Button(label="Apply for this boot")
            custom_button.connect("clicked", lambda *_: self.set_idle(int(self.custom_idle.get_value()) * 60))
            custom.pack_start(custom_button, False, False, 0)
            root.pack_start(custom, False, False, 0)
            actions = Gtk.Box(spacing=8)
            default = Gtk.Button(label="Reset to 2 minutes")
            default.connect("clicked", lambda *_: self.set_idle(None))
            never = Gtk.Button(label="Never until reboot")
            never.get_style_context().add_class("destructive-action")
            never.connect("clicked", self.never_idle)
            actions.pack_start(default, False, False, 0)
            actions.pack_start(never, False, False, 0)
            root.pack_start(actions, False, False, 0)
            root.pack_start(Gtk.Label(), True, True, 0)
            return root

        def reload(self, *_: Any) -> None:
            try:
                self.current = configs_from_monitors(monitor_json())
                self.staged = deepcopy(self.current)
                self.selected = min(self.selected, max(len(self.staged) - 1, 0))
                self.set_status("Loaded current display configuration.")
            except Exception as exc:
                self.set_status(f"Could not load displays: {exc}")
                self.current, self.staged = [], []
            self.rebuild_inspector()
            self.refresh_idle_status()
            self.canvas.queue_draw()

        def set_status(self, message: str) -> None:
            self.status.set_text(message)

        def refresh_idle_status(self) -> None:
            value = get_idle_override()
            if value == "never":
                text = "Current boot: automatic lock, display-off, and suspend are disabled."
            else:
                lock, off, suspend = idle_timeouts() or (0, 0, 0)
                origin = "default" if value is None else "temporary"
                text = f"{origin.capitalize()}: lock after {lock // 60} min; display off at {off // 60} min {off % 60:02d} sec; suspend at {suspend // 60} min {suspend % 60:02d} sec."
            self.idle_status.set_text(text)

        def selected_config(self) -> dict[str, Any] | None:
            return self.staged[self.selected] if self.staged else None

        def add_row(self, grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> None:
            name = Gtk.Label(label=label)
            name.set_xalign(0)
            grid.attach(name, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        def rebuild_inspector(self) -> None:
            for child in self.inspector.get_children():
                self.inspector.remove(child)
            config = self.selected_config()
            if not config:
                return
            title = Gtk.Label(label=short_identity(config))
            title.set_xalign(0)
            title.set_line_wrap(True)
            title.get_style_context().add_class("section-title")
            self.inspector.pack_start(title, False, False, 0)
            details = Gtk.Label(label=f"{config['name']}  •  ID {config['fingerprint'][:8]}")
            details.set_xalign(0)
            details.get_style_context().add_class("subtitle")
            self.inspector.pack_start(details, False, False, 0)
            grid = Gtk.Grid(column_spacing=10, row_spacing=9)
            grid.set_column_homogeneous(False)
            row = 0
            state = Gtk.ComboBoxText()
            for item in ("Extended", "Mirror another display", "Disabled"):
                state.append_text(item)
            state.set_active(2 if not config["enabled"] else (1 if config.get("mirror_of") else 0))
            state.connect("changed", self.state_changed)
            self.add_row(grid, row, "State", state); row += 1

            source = Gtk.ComboBoxText()
            for candidate in self.staged:
                if candidate["name"] != config["name"] and candidate["enabled"] and not candidate.get("mirror_of"):
                    source.append(candidate["name"], short_identity(candidate))
            if config.get("mirror_of"):
                source.set_active_id(str(config["mirror_of"]))
            source.set_sensitive(config["enabled"] and bool(config.get("mirror_of")))
            source.connect("changed", self.mirror_source_changed)
            self.source_combo = source
            self.add_row(grid, row, "Mirror source", source); row += 1

            modes = Gtk.ComboBoxText()
            available = config.get("available_modes") or [config["mode"]]
            for mode in available:
                modes.append(str(mode), str(mode))
            modes.set_active_id(str(config["mode"]))
            modes.set_sensitive(config["enabled"])
            modes.connect("changed", self.mode_changed)
            self.add_row(grid, row, "Resolution / refresh", modes); row += 1

            scales = Gtk.ComboBoxText()
            for scale in valid_scales(str(config["mode"])):
                scales.append(f"{scale:.4g}", f"{scale:.4g}×")
            nearest = min(valid_scales(str(config["mode"])), key=lambda item: abs(item - float(config["scale"])))
            scales.set_active_id(f"{nearest:.4g}")
            scales.set_sensitive(config["enabled"] and not bool(config.get("mirror_of")))
            scales.connect("changed", self.scale_changed)
            self.add_row(grid, row, "Scale", scales); row += 1

            transforms = Gtk.ComboBoxText()
            labels = ("Normal", "90° clockwise", "180°", "270° clockwise", "Flipped", "Flipped 90°", "Flipped 180°", "Flipped 270°")
            for number, label in enumerate(labels):
                transforms.append(str(number), label)
            transforms.set_active_id(str(config.get("transform", 0)))
            transforms.set_sensitive(config["enabled"] and not bool(config.get("mirror_of")))
            transforms.connect("changed", self.transform_changed)
            self.add_row(grid, row, "Rotation", transforms); row += 1

            xy = Gtk.Box(spacing=5)
            x = Gtk.SpinButton.new_with_range(-20000, 20000, 10)
            y = Gtk.SpinButton.new_with_range(-20000, 20000, 10)
            x.set_value(int(config["x"])); y.set_value(int(config["y"]))
            enabled = config["enabled"] and not bool(config.get("mirror_of"))
            x.set_sensitive(enabled); y.set_sensitive(enabled)
            x.connect("value-changed", self.position_changed, "x")
            y.connect("value-changed", self.position_changed, "y")
            xy.pack_start(Gtk.Label(label="X"), False, False, 0); xy.pack_start(x, True, True, 0)
            xy.pack_start(Gtk.Label(label="Y"), False, False, 0); xy.pack_start(y, True, True, 0)
            self.add_row(grid, row, "Logical position", xy); row += 1
            self.inspector.pack_start(grid, False, False, 0)

            relative = Gtk.Box(spacing=5)
            for label, direction in (("←", "left"), ("→", "right"), ("↑", "above"), ("↓", "below")):
                button = Gtk.Button(label=label)
                button.set_sensitive(enabled)
                button.connect("clicked", self.place_relative, direction)
                relative.pack_start(button, True, True, 0)
            self.inspector.pack_start(Gtk.Label(label="Place beside the first extended display"), False, False, 0)
            self.inspector.pack_start(relative, False, False, 0)
            hint = Gtk.Label(label="Drag displays in the map for exact placement. Positions use Hyprland logical pixels.")
            hint.set_xalign(0); hint.set_line_wrap(True); hint.get_style_context().add_class("subtitle")
            self.inspector.pack_start(hint, False, False, 0)
            self.inspector.show_all()

        def state_changed(self, combo: Gtk.ComboBoxText) -> None:
            config = self.selected_config()
            if not config:
                return
            index = combo.get_active()
            config["enabled"] = index != 2
            config["mirror_of"] = None
            if index == 1:
                source = next((candidate for candidate in self.staged if candidate["name"] != config["name"] and candidate["enabled"] and not candidate.get("mirror_of")), None)
                if source:
                    config["mirror_of"] = source["name"]
                else:
                    config["enabled"] = False
                    self.set_status("A mirror needs another enabled extended display.")
            self.rebuild_inspector(); self.canvas.queue_draw()

        def mirror_source_changed(self, combo: Gtk.ComboBoxText) -> None:
            config = self.selected_config()
            if config and combo.get_active_id():
                config["mirror_of"] = combo.get_active_id()
                self.canvas.queue_draw()

        def mode_changed(self, combo: Gtk.ComboBoxText) -> None:
            config = self.selected_config()
            if config and combo.get_active_id():
                config["mode"] = combo.get_active_id()
                valid = valid_scales(config["mode"])
                if config["scale"] not in valid:
                    config["scale"] = min(valid, key=lambda item: abs(item - config["scale"]))
                self.rebuild_inspector(); self.canvas.queue_draw()

        def scale_changed(self, combo: Gtk.ComboBoxText) -> None:
            config = self.selected_config()
            if config and combo.get_active_id():
                config["scale"] = float(combo.get_active_id())
                self.canvas.queue_draw()

        def transform_changed(self, combo: Gtk.ComboBoxText) -> None:
            config = self.selected_config()
            if config and combo.get_active_id():
                config["transform"] = int(combo.get_active_id())
                self.canvas.queue_draw()

        def position_changed(self, spin: Gtk.SpinButton, axis: str) -> None:
            config = self.selected_config()
            if config:
                config[axis] = int(spin.get_value())
                self.canvas.queue_draw()

        def place_relative(self, _: Gtk.Button, direction: str) -> None:
            config = self.selected_config()
            baseline = next((item for item in self.staged if item["enabled"] and not item.get("mirror_of") and item is not config), None)
            if not config or not baseline:
                return
            width, height = logical_size(config)
            base_width, base_height = logical_size(baseline)
            if direction == "left": config["x"], config["y"] = int(baseline["x"]) - width, int(baseline["y"])
            elif direction == "right": config["x"], config["y"] = int(baseline["x"]) + base_width, int(baseline["y"])
            elif direction == "above": config["x"], config["y"] = int(baseline["x"]), int(baseline["y"]) - height
            else: config["x"], config["y"] = int(baseline["x"]), int(baseline["y"]) + base_height
            self.rebuild_inspector(); self.canvas.queue_draw()

        def internal_only(self, *_: Any) -> None:
            for config in self.staged:
                config["enabled"] = config["name"].startswith("eDP")
                config["mirror_of"] = None
                if config["enabled"]: config["x"], config["y"] = 0, 0
            self.rebuild_inspector(); self.canvas.queue_draw()

        def external_only(self, *_: Any) -> None:
            external = [config for config in self.staged if not config["name"].startswith("eDP")]
            if not external:
                self.set_status("No external display is connected."); return
            for config in self.staged:
                config["enabled"] = config in external
                config["mirror_of"] = None
            external[0]["x"], external[0]["y"] = 0, 0
            self.rebuild_inspector(); self.canvas.queue_draw()

        def extend_right(self, *_: Any) -> None:
            position = 0
            for config in self.staged:
                config["enabled"] = True; config["mirror_of"] = None; config["x"], config["y"] = position, 0
                position += logical_size(config)[0]
            self.rebuild_inspector(); self.canvas.queue_draw()

        def mirror(self, *_: Any) -> None:
            if len(self.staged) < 2:
                self.set_status("Connect another display before mirroring."); return
            source = self.staged[0]
            source["enabled"] = True; source["mirror_of"] = None; source["x"], source["y"] = 0, 0
            for config in self.staged[1:]:
                config["enabled"] = True; config["mirror_of"] = source["name"]
            self.rebuild_inspector(); self.canvas.queue_draw()

        def layout_geometry(self) -> tuple[float, float, float, float, float]:
            items = [item for item in self.staged if item["enabled"] and not item.get("mirror_of")]
            if not items:
                return 0, 0, 1, 1, 1
            left = min(int(item["x"]) for item in items); top = min(int(item["y"]) for item in items)
            right = max(int(item["x"]) + logical_size(item)[0] for item in items)
            bottom = max(int(item["y"]) + logical_size(item)[1] for item in items)
            allocation = self.canvas.get_allocation()
            scale = min((allocation.width - 50) / max(right - left, 1), (allocation.height - 50) / max(bottom - top, 1))
            return left, top, right, bottom, max(scale, 0.02)

        def draw_canvas(self, _: Gtk.DrawingArea, context: Any) -> bool:
            allocation = self.canvas.get_allocation()
            context.set_source_rgb(0.118, 0.118, 0.18); context.paint()
            left, top, right, bottom, scale = self.layout_geometry()
            offset_x = (allocation.width - (right - left) * scale) / 2 - left * scale
            offset_y = (allocation.height - (bottom - top) * scale) / 2 - top * scale
            for index, config in enumerate(self.staged):
                if not config["enabled"]:
                    continue
                if config.get("mirror_of"):
                    source = next((item for item in self.staged if item["name"] == config["mirror_of"]), None)
                    if source: x, y = source["x"], source["y"]
                    else: x, y = config["x"], config["y"]
                else: x, y = config["x"], config["y"]
                width, height = logical_size(config)
                px, py, pw, ph = offset_x + int(x) * scale, offset_y + int(y) * scale, width * scale, height * scale
                context.set_source_rgb(*( (0.537, 0.706, 0.98) if index == self.selected else (0.267, 0.278, 0.353) ))
                context.rectangle(px, py, pw, ph); context.fill_preserve()
                context.set_source_rgb(0.804, 0.839, 0.957); context.set_line_width(2); context.stroke()
                context.set_source_rgb(0.804, 0.839, 0.957); context.select_font_face("monospace"); context.set_font_size(12)
                context.move_to(px + 8, py + 19); context.show_text(config["name"])
                context.move_to(px + 8, py + 36); context.show_text("mirror" if config.get("mirror_of") else config["mode"].split("@")[0])
            return False

        def canvas_at(self, event: Any) -> int | None:
            left, top, right, bottom, scale = self.layout_geometry()
            allocation = self.canvas.get_allocation()
            offset_x = (allocation.width - (right - left) * scale) / 2 - left * scale
            offset_y = (allocation.height - (bottom - top) * scale) / 2 - top * scale
            for index, config in reversed(list(enumerate(self.staged))):
                if not config["enabled"] or config.get("mirror_of"):
                    continue
                width, height = logical_size(config)
                x, y = offset_x + config["x"] * scale, offset_y + config["y"] * scale
                if x <= event.x <= x + width * scale and y <= event.y <= y + height * scale:
                    return index
            return None

        def canvas_press(self, _: Gtk.DrawingArea, event: Any) -> bool:
            found = self.canvas_at(event)
            if found is None: return False
            self.selected = found
            config = self.selected_config()
            self.dragging = True
            self.drag_origin = (event.x, event.y, int(config["x"]), int(config["y"])) if config else None
            self.rebuild_inspector(); self.canvas.queue_draw()
            return True

        def canvas_release(self, *_: Any) -> bool:
            self.dragging = False; self.drag_origin = None
            return True

        def canvas_motion(self, _: Gtk.DrawingArea, event: Any) -> bool:
            config = self.selected_config()
            if not self.dragging or not self.drag_origin or not config or config.get("mirror_of"):
                return False
            _, _, _, _, scale = self.layout_geometry()
            start_x, start_y, original_x, original_y = self.drag_origin
            config["x"] = int(round((original_x + (event.x - start_x) / scale) / 10) * 10)
            config["y"] = int(round((original_y + (event.y - start_y) / scale) / 10) * 10)
            self.canvas.queue_draw()
            return True

        def apply_clicked(self, *_: Any) -> None:
            errors = validate_layout(self.staged)
            if errors:
                self.error_dialog("Cannot apply layout", "\n".join(errors)); return
            snapshot = deepcopy(self.current)
            try:
                start_rollback_watch(snapshot)
                apply_layout(self.staged)
                time.sleep(0.25)
                if not layout_matches(self.staged, monitor_json()):
                    raise RuntimeError("Hyprland did not accept the requested layout.")
            except Exception as exc:
                confirm_pending()
                with contextlib.suppress(Exception): apply_layout(snapshot)
                self.error_dialog("Layout was restored", str(exc)); return
            self.confirm_layout(snapshot)

        def confirm_layout(self, snapshot: list[dict[str, Any]]) -> None:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE, text="Keep this display layout?")
            dialog.format_secondary_text(f"The previous layout will be restored automatically in {ROLLBACK_SECONDS} seconds unless you keep it.")
            dialog.add_button("Revert", Gtk.ResponseType.CANCEL)
            dialog.add_button("Keep configuration", Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)
            response = dialog.run(); dialog.destroy()
            if response == Gtk.ResponseType.OK:
                confirm_pending()
                save_profile(self.staged)
                self.current = deepcopy(self.staged)
                self.set_status("Layout saved for this exact physical monitor set.")
            else:
                confirm_pending()
                with contextlib.suppress(Exception): apply_layout(snapshot)
                self.reload()

        def error_dialog(self, title: str, message: str) -> None:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text=title)
            dialog.format_secondary_text(message); dialog.run(); dialog.destroy()

        def set_idle(self, value: int | None) -> None:
            try:
                set_idle_override(value)
                self.refresh_idle_status()
            except Exception as exc:
                self.error_dialog("Could not update idle timeout", str(exc))

        def never_idle(self, *_: Any) -> None:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE, text="Disable automatic idle protection until reboot?")
            dialog.format_secondary_text("This disables automatic lock, display-off, and suspend only for the current boot. Manual locking and suspend remain available.")
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL); dialog.add_button("Disable until reboot", Gtk.ResponseType.OK)
            if dialog.run() == Gtk.ResponseType.OK: self.set_idle("never")
            dialog.destroy()

        def on_key_press(self, _: Gtk.Window, event: Any) -> bool:
            if event.keyval == Gdk.KEY_Escape:
                self.destroy(); return True
            return False

    window = DisplayControl()
    window.show_all()
    Gtk.main()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("panel")
    idle = sub.add_parser("idle-supervisor")
    idle.add_argument("--replace", action="store_true")
    sub.add_parser("profile-watch")
    rollback = sub.add_parser("rollback-watch")
    rollback.add_argument("path", type=Path)
    rollback.add_argument("seconds", type=int)
    args = parser.parse_args()
    if args.command in (None, "panel"): return run_panel()
    if args.command == "idle-supervisor": return idle_supervisor(args.replace)
    if args.command == "profile-watch": return profile_watch()
    if args.command == "rollback-watch": return rollback_watch(args.path, args.seconds)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
