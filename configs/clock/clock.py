#!/usr/bin/env python3
"""Persistent local Clock: timers, alarms, stopwatch, daemon, and alerts."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


APP_ID = "dominik-clock"
APP_TITLE = "Clock"
GRACE_SECONDS = 3600
MAX_LABEL = 80
MAX_REQUEST = 32 * 1024
SNOOZES = (300, 600, 900)


def data_dir() -> Path:
    path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_ID
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"unsafe data directory: {path}")
    os.chmod(path, 0o700)
    return path


def runtime_dir() -> Path:
    path = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / APP_ID
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"unsafe runtime directory: {path}")
    os.chmod(path, 0o700)
    return path


def db_path() -> Path:
    path = data_dir() / "clock.db"
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"refusing symbolic-link database: {path}")
    return path


def socket_path() -> Path:
    return runtime_dir() / "daemon.sock"


def clean_label(value: Any, field: str = "Label") -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_LABEL or "\x00" in text:
        raise ValueError(f"{field} must contain 1–{MAX_LABEL} characters")
    return text


def local_timezone() -> ZoneInfo:
    timezone_file = Path("/etc/localtime")
    with contextlib.suppress(OSError, ValueError):
        target = str(timezone_file.resolve())
        marker = "/zoneinfo/"
        if marker in target:
            return ZoneInfo(target.split(marker, 1)[1])
    return ZoneInfo(os.environ.get("TZ", "UTC"))


TZ = local_timezone()


class ClockDB:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.lock = threading.RLock()
        self.migrate()

    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def migrate(self) -> None:
        created = not self.path.exists()
        with self.lock, self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Clock database was created by a newer version")
            if version == 0:
                conn.executescript(
                    """
                    CREATE TABLE timer_presets (
                        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        duration_sec INTEGER NOT NULL CHECK(duration_sec BETWEEN 1 AND 86400),
                        created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE TABLE timer_runs (
                        id INTEGER PRIMARY KEY, preset_id INTEGER REFERENCES timer_presets(id) ON DELETE SET NULL,
                        label TEXT NOT NULL, total_sec INTEGER NOT NULL, due_ts REAL,
                        remaining_sec INTEGER, state TEXT NOT NULL CHECK(state IN ('running','paused','completed','cancelled','missed')),
                        created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL
                    );
                    CREATE INDEX timer_runs_due ON timer_runs(state, due_ts);
                    CREATE TABLE alarms (
                        id INTEGER PRIMARY KEY, label TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                        kind TEXT NOT NULL CHECK(kind IN ('once','weekdays')), local_date TEXT,
                        local_time TEXT NOT NULL, weekdays INTEGER NOT NULL DEFAULT 0,
                        timezone TEXT NOT NULL, next_due_ts REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE INDEX alarms_due ON alarms(enabled, next_due_ts);
                    CREATE TABLE occurrences (
                        id INTEGER PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('timer','alarm')),
                        source_id INTEGER NOT NULL, label TEXT NOT NULL, scheduled_ts REAL NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('alerting','snoozed','dismissed','missed')),
                        snooze_until_ts REAL, presented_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        UNIQUE(source_type, source_id, scheduled_ts)
                    );
                    CREATE INDEX occurrences_due ON occurrences(state, snooze_until_ts);
                    CREATE TABLE stopwatch (
                        id INTEGER PRIMARY KEY CHECK(id = 1), accumulated_ms INTEGER NOT NULL DEFAULT 0,
                        started_ts REAL, updated_at REAL NOT NULL
                    );
                    INSERT INTO stopwatch(id, accumulated_ms, started_ts, updated_at) VALUES(1, 0, NULL, 0);
                    CREATE TABLE stopwatch_laps (
                        id INTEGER PRIMARY KEY, elapsed_ms INTEGER NOT NULL, created_at REAL NOT NULL
                    );
                    PRAGMA user_version = 1;
                    """
                )
        if created:
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def tx(self):
        with self.lock, self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock, self.connection() as conn:
            rows = lambda sql, args=(): [dict(row) for row in conn.execute(sql, args)]
            stopwatch = dict(conn.execute("SELECT * FROM stopwatch WHERE id=1").fetchone())
            if stopwatch["started_ts"] is not None:
                stopwatch["elapsed_ms"] = stopwatch["accumulated_ms"] + round((now - stopwatch["started_ts"]) * 1000)
            else:
                stopwatch["elapsed_ms"] = stopwatch["accumulated_ms"]
            return {
                "now": now,
                "presets": rows("SELECT * FROM timer_presets ORDER BY name COLLATE NOCASE"),
                "timers": rows("SELECT * FROM timer_runs WHERE state IN ('running','paused') ORDER BY due_ts, id"),
                "alarms": rows("SELECT * FROM alarms ORDER BY enabled DESC, next_due_ts, id"),
                "stopwatch": stopwatch,
                "laps": rows("SELECT * FROM stopwatch_laps ORDER BY id DESC"),
                "missed": rows("SELECT * FROM occurrences WHERE state='missed' ORDER BY scheduled_ts DESC LIMIT 20"),
            }

    def add_preset(self, label: str, duration: int) -> int:
        now = time.time()
        with self.tx() as conn:
            cursor = conn.execute("INSERT INTO timer_presets(name,duration_sec,created_at,updated_at) VALUES(?,?,?,?)", (label, duration, now, now))
            return int(cursor.lastrowid)

    def delete_preset(self, preset_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM timer_presets WHERE id=?", (preset_id,))

    def start_timer(self, label: str, duration: int, preset_id: int | None = None) -> int:
        now = time.time()
        with self.tx() as conn:
            cursor = conn.execute(
                "INSERT INTO timer_runs(preset_id,label,total_sec,due_ts,remaining_sec,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (preset_id, label, duration, now + duration, duration, "running", now, now),
            )
            return int(cursor.lastrowid)

    def timer_action(self, timer_id: int, action: str) -> None:
        now = time.time()
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM timer_runs WHERE id=?", (timer_id,)).fetchone()
            if not row:
                raise ValueError("Timer no longer exists")
            row = dict(row)
            if action == "pause" and row["state"] == "running":
                remaining = max(0, round(float(row["due_ts"]) - now))
                conn.execute("UPDATE timer_runs SET state='paused',remaining_sec=?,due_ts=NULL,updated_at=? WHERE id=?", (remaining, now, timer_id))
            elif action == "resume" and row["state"] == "paused":
                conn.execute("UPDATE timer_runs SET state='running',due_ts=?,updated_at=? WHERE id=?", (now + int(row["remaining_sec"]), now, timer_id))
            elif action == "restart":
                conn.execute("UPDATE timer_runs SET state='running',due_ts=?,remaining_sec=?,completed_at=NULL,updated_at=? WHERE id=?", (now + int(row["total_sec"]), row["total_sec"], now, timer_id))
            elif action == "cancel":
                conn.execute("UPDATE timer_runs SET state='cancelled',updated_at=? WHERE id=?", (now, timer_id))
            else:
                raise ValueError("That timer action is not available")

    def alarm_next_due(self, kind: str, local_date: str | None, local_time: str, weekdays: int, now: float | None = None) -> float:
        now = time.time() if now is None else now
        try:
            hour, minute = map(int, local_time.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59): raise ValueError
        except ValueError as error:
            raise ValueError("Time must use 24-hour HH:MM format") from error
        current = dt.datetime.fromtimestamp(now, TZ)
        if kind == "once":
            try: date = dt.date.fromisoformat(str(local_date))
            except ValueError as error: raise ValueError("Date must use YYYY-MM-DD format") from error
            candidate = dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=TZ)
            return candidate.timestamp()
        if weekdays <= 0 or weekdays > 127:
            raise ValueError("Choose at least one weekday")
        for offset in range(8):
            date = current.date() + dt.timedelta(days=offset)
            candidate = dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=TZ)
            if weekdays & (1 << date.weekday()) and candidate.timestamp() > now:
                return candidate.timestamp()
        raise RuntimeError("Could not calculate next alarm")

    def add_alarm(self, label: str, kind: str, local_date: str | None, local_time: str, weekdays: int) -> int:
        if kind not in ("once", "weekdays"): raise ValueError("Invalid alarm kind")
        now = time.time(); next_due = self.alarm_next_due(kind, local_date, local_time, weekdays, now)
        with self.tx() as conn:
            cursor = conn.execute(
                "INSERT INTO alarms(label,enabled,kind,local_date,local_time,weekdays,timezone,next_due_ts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (label, 1, kind, local_date, local_time, weekdays, str(TZ), next_due, now, now),
            )
            return int(cursor.lastrowid)

    def alarm_toggle(self, alarm_id: int, enabled: bool) -> None:
        now = time.time()
        with self.tx() as conn:
            alarm = conn.execute("SELECT * FROM alarms WHERE id=?", (alarm_id,)).fetchone()
            if not alarm: raise ValueError("Alarm no longer exists")
            due = self.alarm_next_due(alarm["kind"], alarm["local_date"], alarm["local_time"], alarm["weekdays"], now) if enabled else alarm["next_due_ts"]
            conn.execute("UPDATE alarms SET enabled=?,next_due_ts=?,updated_at=? WHERE id=?", (int(enabled), due, now, alarm_id))

    def delete_alarm(self, alarm_id: int) -> None:
        with self.tx() as conn: conn.execute("DELETE FROM alarms WHERE id=?", (alarm_id,))

    def stopwatch_action(self, action: str) -> None:
        now = time.time()
        with self.tx() as conn:
            row = dict(conn.execute("SELECT * FROM stopwatch WHERE id=1").fetchone())
            if action == "start" and row["started_ts"] is None:
                conn.execute("UPDATE stopwatch SET started_ts=?,updated_at=? WHERE id=1", (now, now))
            elif action == "pause" and row["started_ts"] is not None:
                accumulated = row["accumulated_ms"] + round((now - row["started_ts"]) * 1000)
                conn.execute("UPDATE stopwatch SET accumulated_ms=?,started_ts=NULL,updated_at=? WHERE id=1", (accumulated, now))
            elif action == "lap" and row["started_ts"] is not None:
                elapsed = row["accumulated_ms"] + round((now - row["started_ts"]) * 1000)
                conn.execute("INSERT INTO stopwatch_laps(elapsed_ms,created_at) VALUES(?,?)", (elapsed, now))
            elif action == "reset":
                conn.execute("UPDATE stopwatch SET accumulated_ms=0,started_ts=NULL,updated_at=? WHERE id=1", (now,))
                conn.execute("DELETE FROM stopwatch_laps")
            else: raise ValueError("That stopwatch action is not available")

    def alert_action(self, occurrence_id: int, action: str, seconds: int | None = None) -> None:
        now = time.time()
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if not row or row["state"] not in ("alerting", "snoozed"): raise ValueError("Alert is no longer active")
            if action == "dismiss":
                conn.execute("UPDATE occurrences SET state='dismissed',updated_at=? WHERE id=?", (now, occurrence_id))
            elif action == "snooze" and seconds in SNOOZES:
                conn.execute("UPDATE occurrences SET state='snoozed',snooze_until_ts=?,presented_at=NULL,updated_at=? WHERE id=?", (now + int(seconds), now, occurrence_id))
            else: raise ValueError("Invalid alert action")

    def alert_info(self, occurrence_id: int) -> dict[str, Any]:
        with self.lock, self.connection() as conn:
            row = conn.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if not row: raise ValueError("Alert no longer exists")
            return dict(row)

    def tick(self) -> list[int]:
        now = time.time(); launch: list[int] = []
        with self.tx() as conn:
            for timer in conn.execute("SELECT * FROM timer_runs WHERE state='running' AND due_ts<=?", (now,)).fetchall():
                state = "alerting" if now - timer["due_ts"] <= GRACE_SECONDS else "missed"
                conn.execute("UPDATE timer_runs SET state=?,remaining_sec=0,completed_at=?,updated_at=? WHERE id=?", ("completed" if state == "alerting" else "missed", now, now, timer["id"]))
                cursor = conn.execute("INSERT OR IGNORE INTO occurrences(source_type,source_id,label,scheduled_ts,state,created_at,updated_at) VALUES('timer',?,?,?,?,?,?)", (timer["id"], timer["label"], timer["due_ts"], state, now, now))
                if state == "alerting" and cursor.rowcount: launch.append(int(cursor.lastrowid))
            for alarm in conn.execute("SELECT * FROM alarms WHERE enabled=1 AND next_due_ts<=?", (now,)).fetchall():
                state = "alerting" if now - alarm["next_due_ts"] <= GRACE_SECONDS else "missed"
                cursor = conn.execute("INSERT OR IGNORE INTO occurrences(source_type,source_id,label,scheduled_ts,state,created_at,updated_at) VALUES('alarm',?,?,?,?,?,?)", (alarm["id"], alarm["label"], alarm["next_due_ts"], state, now, now))
                if state == "alerting" and cursor.rowcount: launch.append(int(cursor.lastrowid))
                if alarm["kind"] == "weekdays":
                    next_due = self.alarm_next_due("weekdays", None, alarm["local_time"], alarm["weekdays"], now)
                    conn.execute("UPDATE alarms SET next_due_ts=?,updated_at=? WHERE id=?", (next_due, now, alarm["id"]))
                else:
                    conn.execute("UPDATE alarms SET enabled=0,updated_at=? WHERE id=?", (now, alarm["id"]))
            for occurrence in conn.execute("SELECT id FROM occurrences WHERE state='snoozed' AND snooze_until_ts<=?", (now,)).fetchall():
                conn.execute("UPDATE occurrences SET state='alerting',snooze_until_ts=NULL,presented_at=NULL,updated_at=? WHERE id=?", (now, occurrence["id"]))
                launch.append(int(occurrence["id"]))
        return launch

    def mark_presented(self, occurrence_id: int) -> bool:
        now = time.time()
        with self.tx() as conn:
            cursor = conn.execute("UPDATE occurrences SET presented_at=?,updated_at=? WHERE id=? AND state='alerting'", (now, now, occurrence_id))
            return bool(cursor.rowcount)

    def unresolved_alerts(self) -> list[int]:
        with self.lock, self.connection() as conn:
            return [int(row[0]) for row in conn.execute("SELECT id FROM occurrences WHERE state='alerting'")]

    def dismiss_if_alerting(self, occurrence_id: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE occurrences SET state='dismissed',updated_at=? WHERE id=? AND state='alerting'", (time.time(), occurrence_id))


class Daemon:
    def __init__(self) -> None:
        self.db = ClockDB(); self.running = True
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def dispatch(self, request: dict[str, Any]) -> Any:
        operation = request.get("op"); params = request.get("params") or {}
        if not isinstance(params, dict): raise ValueError("params must be an object")
        if operation == "snapshot": return self.db.snapshot()
        if operation == "preset_add": return {"id": self.db.add_preset(clean_label(params.get("name"), "Preset name"), bounded_int(params.get("duration"), 1, 86400, "Duration"))}
        if operation == "preset_delete": self.db.delete_preset(bounded_int(params.get("id"), 1, 10**9, "Preset")); return {}
        if operation == "timer_start": return {"id": self.db.start_timer(clean_label(params.get("label")), bounded_int(params.get("duration"), 1, 86400, "Duration"), optional_int(params.get("preset_id")))}
        if operation == "timer_action": self.db.timer_action(bounded_int(params.get("id"), 1, 10**9, "Timer"), str(params.get("action"))); return {}
        if operation == "alarm_add":
            return {"id": self.db.add_alarm(clean_label(params.get("label")), str(params.get("kind")), params.get("date"), str(params.get("time")), bounded_int(params.get("weekdays"), 0, 127, "Weekdays"))}
        if operation == "alarm_toggle": self.db.alarm_toggle(bounded_int(params.get("id"), 1, 10**9, "Alarm"), bool(params.get("enabled"))); return {}
        if operation == "alarm_delete": self.db.delete_alarm(bounded_int(params.get("id"), 1, 10**9, "Alarm")); return {}
        if operation == "stopwatch": self.db.stopwatch_action(str(params.get("action"))); return {}
        if operation == "alert_action": self.db.alert_action(bounded_int(params.get("id"), 1, 10**9, "Alert"), str(params.get("action")), optional_int(params.get("seconds"))); return {}
        if operation == "alert_info": return self.db.alert_info(bounded_int(params.get("id"), 1, 10**9, "Alert"))
        raise ValueError("Unknown operation")

    def serve_client(self, client: socket.socket) -> None:
        with client:
            client.settimeout(4); raw = client.recv(MAX_REQUEST + 1)
            if not raw or len(raw) > MAX_REQUEST: return
            try:
                request = json.loads(raw.decode("utf-8")); result = self.dispatch(request)
                response = {"ok": True, "id": request.get("id"), "result": result}
            except (ValueError, sqlite3.Error, json.JSONDecodeError) as error:
                response = {"ok": False, "error": str(error)[:240]}
            client.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8"))

    def spawn_alert(self, occurrence_id: int) -> None:
        if not self.db.mark_presented(occurrence_id): return
        process = subprocess.Popen([sys.executable, os.path.abspath(__file__), "alert", str(occurrence_id)], close_fds=True)
        def reap() -> None:
            # A normal alert process ends only after snooze/dismiss. A process
            # that found an already-open alert returns 3 and must not dismiss
            # that other visible alert.
            if process.wait() != 3:
                with contextlib.suppress(Exception): self.db.dismiss_if_alerting(occurrence_id)
        threading.Thread(target=reap, daemon=True).start()

    def run(self) -> int:
        lock_file = runtime_dir() / "daemon.lock"; handle = lock_file.open("w")
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        path = socket_path()
        with contextlib.suppress(FileNotFoundError): path.unlink()
        self.socket.bind(str(path)); os.chmod(path, 0o600); self.socket.listen(8); self.socket.settimeout(1)
        for occurrence_id in self.db.unresolved_alerts(): self.spawn_alert(occurrence_id)
        try:
            while self.running:
                for occurrence_id in self.db.tick(): self.spawn_alert(occurrence_id)
                try:
                    client, _ = self.socket.accept(); threading.Thread(target=self.serve_client, args=(client,), daemon=True).start()
                except TimeoutError: pass
        finally:
            self.socket.close()
            with contextlib.suppress(FileNotFoundError): path.unlink()
        return 0


def bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    try: result = int(value)
    except (TypeError, ValueError) as error: raise ValueError(f"{name} must be a number") from error
    if not minimum <= result <= maximum: raise ValueError(f"{name} is outside the allowed range")
    return result


def optional_int(value: Any) -> int | None:
    return None if value is None else bounded_int(value, 1, 10**9, "ID")


def request(operation: str, params: dict[str, Any] | None = None) -> Any:
    payload = json.dumps({"v": 1, "id": uuid.uuid4().hex, "op": operation, "params": params or {}}).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3); client.connect(str(socket_path())); client.sendall(payload); response = json.loads(client.recv(MAX_REQUEST).decode("utf-8"))
    if not response.get("ok"): raise RuntimeError(response.get("error", "Clock daemon failed"))
    return response.get("result")


def ensure_daemon() -> None:
    try: request("snapshot"); return
    except (OSError, RuntimeError): pass
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"], close_fds=True)
    for _ in range(25):
        time.sleep(0.08)
        try: request("snapshot"); return
        except (OSError, RuntimeError): pass
    raise RuntimeError("Clock daemon did not start")


def format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds)); hours, seconds = divmod(seconds, 3600); minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def format_stopwatch(milliseconds: int | float) -> str:
    """Format stopwatch precision as HH:MM:SS.mmm."""
    milliseconds = max(0, round(milliseconds))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def stopwatch_elapsed_ms(accumulated_ms: int | float, started_ts: float | None, now: float | None = None) -> int:
    """Calculate a stopwatch value without needing to query the database again."""
    if started_ts is None:
        return round(accumulated_ms)
    return round(accumulated_ms + max(0, (time.time() if now is None else now) - started_ts) * 1000)


def format_datetime(timestamp: float | None) -> str:
    return "—" if timestamp is None else dt.datetime.fromtimestamp(timestamp, TZ).strftime("%a %d %b %H:%M")


def run_alert(occurrence_id: int) -> int:
    lock_path = runtime_dir() / f"alert-{occurrence_id}.lock"; lock = lock_path.open("w")
    try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return 3
    try: info = request("alert_info", {"id": occurrence_id})
    except Exception: return 0
    if info.get("state") != "alerting": return 0
    import gi
    gi.require_version("Gdk", "3.0"); gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    stop_sound = threading.Event()

    def audio_loop() -> None:
        sound = Path("/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga")
        until = time.monotonic() + 60
        while time.monotonic() < until and not stop_sound.is_set():
            command = ["pw-play", str(sound)] if sound.is_file() and shutil_which("pw-play") else ["canberra-gtk-play", "--id=alarm-clock-elapsed"]
            with contextlib.suppress(OSError):
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(50):
                    if stop_sound.wait(0.1): process.terminate(); return
                    if process.poll() is not None: break
            stop_sound.wait(4)

    window = Gtk.Window(title="Clock Alert")
    window.set_wmclass(APP_ID, APP_ID); window.set_default_size(440, 230); window.set_position(Gtk.WindowPosition.CENTER); window.set_decorated(False)
    provider = Gtk.CssProvider(); provider.load_from_path(str(Path(__file__).with_name("style.css")))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11); outer.set_border_width(18); outer.get_style_context().add_class("clock-window"); window.add(outer)
    title = Gtk.Label(label="Timer finished" if info["source_type"] == "timer" else "Alarm")
    title.set_xalign(0); title.get_style_context().add_class("title"); outer.pack_start(title, False, False, 0)
    label = Gtk.Label(label=str(info["label"])); label.set_xalign(0); label.set_line_wrap(True); label.get_style_context().add_class("alert-label"); outer.pack_start(label, False, False, 0)
    late = max(0, time.time() - float(info["scheduled_ts"])); detail = "Due now" if late < 60 else f"Due {format_duration(late)} ago"
    note = Gtk.Label(label=detail); note.set_xalign(0); note.get_style_context().add_class("subtitle"); outer.pack_start(note, False, False, 0)
    actions = Gtk.Box(spacing=7); outer.pack_start(actions, False, False, 0)
    muted = {"value": False}
    handled = {"value": False}
    def finish(action: str, seconds: int | None = None) -> None:
        handled["value"] = True
        stop_sound.set()
        with contextlib.suppress(Exception): request("alert_action", {"id": occurrence_id, "action": action, "seconds": seconds})
        window.destroy()
    for seconds in SNOOZES:
        button = Gtk.Button(label=f"Snooze {seconds // 60}m"); button.connect("clicked", lambda _, value=seconds: finish("snooze", value)); actions.pack_start(button, False, False, 0)
    mute = Gtk.Button(label="Mute")
    def mute_sound(_: Any) -> None: stop_sound.set(); mute.set_sensitive(False); muted["value"] = True
    mute.connect("clicked", mute_sound); actions.pack_start(mute, False, False, 0)
    dismiss = Gtk.Button(label="Dismiss"); dismiss.get_style_context().add_class("suggested-action"); dismiss.connect("clicked", lambda *_: finish("dismiss")); actions.pack_end(dismiss, False, False, 0)
    def destroyed(*_: Any) -> None:
        stop_sound.set()
        # A compositor-initiated close is equivalent to dismissing the alert;
        # otherwise an invisible alert could remain locked in alerting state.
        if not handled["value"]:
            with contextlib.suppress(Exception): request("alert_action", {"id": occurrence_id, "action": "dismiss"})
        Gtk.main_quit()
    window.connect("destroy", destroyed); window.connect("key-press-event", lambda _, event: (finish("dismiss") or True) if event.keyval == Gdk.KEY_Escape else False)
    threading.Thread(target=audio_loop, daemon=True).start(); window.show_all(); Gtk.main()
    return 0


def fallback_notification(occurrence_id: int) -> int:
    """Leave a visible alert behind if GTK cannot create an alert surface."""
    try:
        info = ClockDB().alert_info(occurrence_id)
        subprocess.run(
            ["notify-send", "-u", "critical", "Clock alert", str(info["label"])],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return 0


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK): return str(candidate)
    return None


def run_ui() -> int:
    ensure_daemon()
    import gi
    gi.require_version("Gdk", "3.0"); gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    class Clock(Gtk.Window):
        def __init__(self) -> None:
            super().__init__(title=APP_TITLE); self.set_wmclass(APP_ID, APP_ID); self.set_default_size(720, 680); self.set_position(Gtk.WindowPosition.CENTER); self.set_decorated(False)
            self.connect("destroy", lambda *_: Gtk.main_quit()); self.connect("key-press-event", self.keypress)
            provider = Gtk.CssProvider(); provider.load_from_path(str(Path(__file__).with_name("style.css")))
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); self.root.get_style_context().add_class("clock-window"); self.add(self.root)
            self.stopwatch_accumulated_ms = 0
            self.stopwatch_started_ts: float | None = None
            self.stopwatch_tick_id: int | None = None
            self.build(); self.refresh()
            # Full refreshes perform IPC and rebuild lists. The stopwatch only
            # needs its label updated, so render it independently at 30 FPS
            # only while it is running.
            GLib.timeout_add(1000, self.refresh)

        def build(self) -> None:
            header = Gtk.Box(); header.set_border_width(16); header.get_style_context().add_class("header")
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            self.now_label = Gtk.Label(); self.now_label.set_xalign(0); self.now_label.get_style_context().add_class("clock-time")
            self.date_label = Gtk.Label(); self.date_label.set_xalign(0); self.date_label.get_style_context().add_class("subtitle")
            left.pack_start(self.now_label, False, False, 0); left.pack_start(self.date_label, False, False, 0); header.pack_start(left, True, True, 0)
            close = Gtk.Button(label="Close"); close.connect("clicked", lambda *_: self.destroy()); header.pack_end(close, False, False, 0); self.root.pack_start(header, False, False, 0)
            self.notebook = Gtk.Notebook(); self.notebook.set_border_width(14); self.root.pack_start(self.notebook, True, True, 0)
            self.timer_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10); self.alarm_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10); self.stopwatch_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.notebook.append_page(self.timer_page, Gtk.Label(label="Timers")); self.notebook.append_page(self.alarm_page, Gtk.Label(label="Alarms")); self.notebook.append_page(self.stopwatch_page, Gtk.Label(label="Stopwatch"))
            self.build_timers(); self.build_alarms(); self.build_stopwatch()

        def message(self, text: str) -> None:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text="Clock")
            dialog.format_secondary_text(text); dialog.run(); dialog.destroy()

        def row_list(self, page: Any) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5); scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.add(box); page.pack_start(scroll, True, True, 0); return box

        def build_timers(self) -> None:
            add = Gtk.Box(spacing=7); self.timer_label = Gtk.Entry(); self.timer_label.set_placeholder_text("Timer label")
            self.timer_hours = Gtk.SpinButton.new_with_range(0, 99, 1)
            self.timer_minutes = Gtk.SpinButton.new_with_range(0, 59, 1); self.timer_minutes.set_value(5)
            self.timer_seconds = Gtk.SpinButton.new_with_range(0, 59, 1)
            start = Gtk.Button(label="Start timer"); start.get_style_context().add_class("suggested-action"); start.connect("clicked", self.start_quick_timer)
            save = Gtk.Button(label="Save preset"); save.connect("clicked", self.save_preset)
            add.pack_start(self.timer_label, True, True, 0)
            for label, spin in (("h", self.timer_hours), ("m", self.timer_minutes), ("s", self.timer_seconds)):
                add.pack_start(spin, False, False, 0); add.pack_start(Gtk.Label(label=label), False, False, 0)
            add.pack_start(start, False, False, 0); add.pack_start(save, False, False, 0); self.timer_page.pack_start(add, False, False, 0)
            self.timer_page.pack_start(section("Running timers"), False, False, 0); self.timer_runs = self.row_list(self.timer_page)
            self.timer_page.pack_start(section("Saved presets"), False, False, 0); self.presets = self.row_list(self.timer_page)

        def build_alarms(self) -> None:
            add = Gtk.Grid(column_spacing=7, row_spacing=7); self.alarm_label = Gtk.Entry(); self.alarm_label.set_placeholder_text("Alarm label"); self.alarm_time = Gtk.Entry(); self.alarm_time.set_text(dt.datetime.now(TZ).strftime("%H:%M")); self.alarm_date = Gtk.Entry(); self.alarm_date.set_text(dt.date.today().isoformat())
            add.attach(self.alarm_label, 0, 0, 2, 1); add.attach(Gtk.Label(label="Time"), 2, 0, 1, 1); add.attach(self.alarm_time, 3, 0, 1, 1); add.attach(Gtk.Label(label="Date"), 0, 1, 1, 1); add.attach(self.alarm_date, 1, 1, 2, 1)
            self.days: list[Any] = []; day_box = Gtk.Box(spacing=3)
            for index, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
                button = Gtk.ToggleButton(label=name); self.days.append(button); day_box.pack_start(button, False, False, 0)
            add.attach(day_box, 0, 2, 4, 1); button = Gtk.Button(label="Add alarm"); button.get_style_context().add_class("suggested-action"); button.connect("clicked", self.add_alarm); add.attach(button, 3, 3, 1, 1)
            hint = Gtk.Label(label="Leave weekdays clear for a one-time alarm. Selecting days creates a recurring weekly alarm."); hint.set_xalign(0); hint.set_line_wrap(True); hint.get_style_context().add_class("subtitle"); self.alarm_page.pack_start(add, False, False, 0); self.alarm_page.pack_start(hint, False, False, 0); self.alarm_page.pack_start(section("Alarms"), False, False, 0); self.alarms = self.row_list(self.alarm_page)

        def build_stopwatch(self) -> None:
            self.stopwatch_label = Gtk.Label(label="00:00:00.000"); self.stopwatch_label.get_style_context().add_class("stopwatch-time"); self.stopwatch_page.pack_start(self.stopwatch_label, False, False, 12)
            actions = Gtk.Box(spacing=7); self.sw_start = Gtk.Button(label="Start"); self.sw_pause = Gtk.Button(label="Pause"); lap = Gtk.Button(label="Lap"); reset = Gtk.Button(label="Reset")
            self.sw_start.connect("clicked", lambda *_: self.sw("start")); self.sw_pause.connect("clicked", lambda *_: self.sw("pause")); lap.connect("clicked", lambda *_: self.sw("lap")); reset.connect("clicked", lambda *_: self.sw("reset"))
            for button in (self.sw_start, self.sw_pause, lap, reset): actions.pack_start(button, False, False, 0)
            self.stopwatch_page.pack_start(actions, False, False, 0); self.stopwatch_page.pack_start(section("Laps"), False, False, 0); self.laps = self.row_list(self.stopwatch_page)

        def clear(self, box: Any) -> None:
            for child in box.get_children(): box.remove(child)

        def refresh_stopwatch(self) -> bool:
            if self.stopwatch_started_ts is None:
                self.stopwatch_tick_id = None
                return False
            self.stopwatch_label.set_text(format_stopwatch(stopwatch_elapsed_ms(self.stopwatch_accumulated_ms, self.stopwatch_started_ts)))
            return True

        def sync_stopwatch_tick(self) -> None:
            if self.stopwatch_started_ts is not None and self.stopwatch_tick_id is None:
                self.stopwatch_tick_id = GLib.timeout_add(33, self.refresh_stopwatch)
            elif self.stopwatch_started_ts is None and self.stopwatch_tick_id is not None:
                GLib.source_remove(self.stopwatch_tick_id)
                self.stopwatch_tick_id = None

        def refresh(self) -> bool:
            try: snapshot = request("snapshot")
            except Exception: return True
            now = time.time(); local = dt.datetime.now(TZ); self.now_label.set_text(local.strftime("%H:%M:%S")); self.date_label.set_text(local.strftime("%A, %d %B %Y"))
            self.clear(self.timer_runs)
            for timer in snapshot["timers"]:
                remain = timer["remaining_sec"] if timer["state"] == "paused" else max(0, timer["due_ts"] - now)
                row = Gtk.Box(spacing=7); text = Gtk.Label(label=f"{timer['label']}  •  {format_duration(remain)}  ({timer['state']})"); text.set_xalign(0); row.pack_start(text, True, True, 0)
                action = Gtk.Button(label="Resume" if timer["state"] == "paused" else "Pause"); action.connect("clicked", lambda _, ident=timer["id"], act="resume" if timer["state"] == "paused" else "pause": self.timer_action(ident, act)); row.pack_end(action, False, False, 0)
                restart = Gtk.Button(label="Restart"); restart.connect("clicked", lambda _, ident=timer["id"]: self.timer_action(ident, "restart")); row.pack_end(restart, False, False, 0)
                cancel = Gtk.Button(label="Cancel"); cancel.connect("clicked", lambda _, ident=timer["id"]: self.timer_action(ident, "cancel")); row.pack_end(cancel, False, False, 0); self.timer_runs.pack_start(row, False, False, 0)
            self.clear(self.presets)
            for preset in snapshot["presets"]:
                row = Gtk.Box(spacing=7); text = Gtk.Label(label=f"{preset['name']}  •  {format_duration(preset['duration_sec'])}"); text.set_xalign(0); row.pack_start(text, True, True, 0)
                start = Gtk.Button(label="Start"); start.connect("clicked", lambda _, item=preset: self.start_preset(item)); delete = Gtk.Button(label="Delete"); delete.connect("clicked", lambda _, ident=preset["id"]: self.call("preset_delete", {"id": ident})); row.pack_end(delete, False, False, 0); row.pack_end(start, False, False, 0); self.presets.pack_start(row, False, False, 0)
            self.clear(self.alarms)
            for alarm in snapshot["alarms"]:
                days = weekdays_label(alarm["weekdays"]) if alarm["kind"] == "weekdays" else str(alarm["local_date"])
                row = Gtk.Box(spacing=7); text = Gtk.Label(label=f"{alarm['label']}  •  {alarm['local_time']}  •  {days}  •  next {format_datetime(alarm['next_due_ts'])}"); text.set_xalign(0); text.set_line_wrap(True); row.pack_start(text, True, True, 0)
                enabled = Gtk.Switch(); enabled.set_active(bool(alarm["enabled"])); enabled.connect("state-set", lambda _, state, ident=alarm["id"]: (self.call("alarm_toggle", {"id": ident, "enabled": state}) or False)); row.pack_end(enabled, False, False, 0)
                delete = Gtk.Button(label="Delete"); delete.connect("clicked", lambda _, ident=alarm["id"]: self.call("alarm_delete", {"id": ident})); row.pack_end(delete, False, False, 0); self.alarms.pack_start(row, False, False, 0)
            stopwatch = snapshot["stopwatch"]
            self.stopwatch_accumulated_ms = stopwatch["accumulated_ms"]
            self.stopwatch_started_ts = stopwatch["started_ts"]
            self.stopwatch_label.set_text(format_stopwatch(stopwatch_elapsed_ms(self.stopwatch_accumulated_ms, self.stopwatch_started_ts)))
            self.sync_stopwatch_tick()
            running = self.stopwatch_started_ts is not None; self.sw_start.set_sensitive(not running); self.sw_pause.set_sensitive(running)
            self.clear(self.laps)
            for number, lap in enumerate(snapshot["laps"], start=1):
                label = Gtk.Label(label=f"Lap {len(snapshot['laps']) - number + 1}: {format_stopwatch(lap['elapsed_ms'])}"); label.set_xalign(0); self.laps.pack_start(label, False, False, 0)
            for box in (self.timer_runs, self.presets, self.alarms, self.laps): box.show_all()
            return True

        def call(self, operation: str, params: dict[str, Any]) -> Any:
            try: return request(operation, params)
            except Exception as error: self.message(str(error)); return None

        def start_quick_timer(self, *_: Any) -> None:
            label = self.timer_label.get_text().strip() or "Timer"; result = self.call("timer_start", {"label": label, "duration": self.timer_input_duration()});
            if result is not None: self.timer_label.set_text(""); self.refresh()

        def save_preset(self, *_: Any) -> None:
            result = self.call("preset_add", {"name": self.timer_label.get_text(), "duration": self.timer_input_duration()});
            if result is not None: self.refresh()

        def timer_input_duration(self) -> int:
            return (
                int(self.timer_hours.get_value()) * 3600
                + int(self.timer_minutes.get_value()) * 60
                + int(self.timer_seconds.get_value())
            )

        def start_preset(self, preset: dict[str, Any]) -> None:
            if self.call("timer_start", {"label": preset["name"], "duration": preset["duration_sec"], "preset_id": preset["id"]}) is not None: self.refresh()

        def timer_action(self, ident: int, action: str) -> None:
            if self.call("timer_action", {"id": ident, "action": action}) is not None: self.refresh()

        def add_alarm(self, *_: Any) -> None:
            mask = sum((1 << index) for index, button in enumerate(self.days) if button.get_active()); kind = "weekdays" if mask else "once"
            result = self.call("alarm_add", {"label": self.alarm_label.get_text(), "kind": kind, "date": self.alarm_date.get_text(), "time": self.alarm_time.get_text(), "weekdays": mask})
            if result is not None: self.alarm_label.set_text(""); self.refresh()

        def sw(self, action: str) -> None:
            if self.call("stopwatch", {"action": action}) is not None: self.refresh()

        def keypress(self, _: Any, event: Any) -> bool:
            if event.keyval == Gdk.KEY_Escape: self.destroy(); return True
            return False

    window = Clock(); window.show_all(); Gtk.main(); return 0


def section(text: str) -> Any:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    label = Gtk.Label(label=text); label.set_xalign(0); label.get_style_context().add_class("section-title"); return label


def weekdays_label(mask: int) -> str:
    return " ".join(name for index, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")) if mask & (1 << index))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("ui", "daemon", "alert"), nargs="?", default="ui"); parser.add_argument("occurrence", nargs="?", type=int); args = parser.parse_args()
    if args.command == "daemon": return Daemon().run()
    if args.command == "alert":
        if not args.occurrence: parser.error("alert requires an occurrence ID")
        try: return run_alert(args.occurrence)
        except Exception: return fallback_notification(args.occurrence)
    return run_ui()


if __name__ == "__main__": raise SystemExit(main())
