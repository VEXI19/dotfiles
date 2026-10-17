#!/usr/bin/env python3
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name("clock.py")
SPEC = importlib.util.spec_from_file_location("clock", SOURCE)
clock = importlib.util.module_from_spec(SPEC); assert SPEC and SPEC.loader; SPEC.loader.exec_module(clock)

class ClockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = clock.ClockDB(Path(self.temp.name) / "clock.db")
    def tearDown(self): self.temp.cleanup()
    def test_concurrent_timers_and_pause(self):
        one = self.db.start_timer("Tea", 120); two = self.db.start_timer("Laundry", 240)
        self.db.timer_action(one, "pause"); snapshot = self.db.snapshot()
        self.assertEqual(len(snapshot["timers"]), 2); self.assertEqual(next(x for x in snapshot["timers"] if x["id"] == one)["state"], "paused")
        self.db.timer_action(one, "resume")
    def test_overdue_timer_is_missed(self):
        timer = self.db.start_timer("Old", 1)
        with self.db.tx() as conn: conn.execute("UPDATE timer_runs SET due_ts=? WHERE id=?", (time.time() - clock.GRACE_SECONDS - 1, timer))
        self.assertEqual(self.db.tick(), []); self.assertEqual(self.db.snapshot()["missed"][0]["label"], "Old")
    def test_snooze_transitions_back_to_alerting(self):
        timer = self.db.start_timer("Soon", 1)
        with self.db.tx() as conn: conn.execute("UPDATE timer_runs SET due_ts=? WHERE id=?", (time.time() - 1, timer))
        occurrence = self.db.tick()[0]; self.db.alert_action(occurrence, "snooze", 300)
        with self.db.tx() as conn: conn.execute("UPDATE occurrences SET snooze_until_ts=? WHERE id=?", (time.time() - 1, occurrence))
        self.assertIn(occurrence, self.db.tick())
    def test_weekday_alarm_advances(self):
        mask = 1 << ((time.localtime().tm_wday + 1) % 7)
        alarm = self.db.add_alarm("Test", "weekdays", None, "12:00", mask)
        self.assertGreater(self.db.snapshot()["alarms"][0]["next_due_ts"], time.time())
    def test_stopwatch_elapsed_survives(self):
        self.db.stopwatch_action("start"); time.sleep(.01); self.assertGreater(self.db.snapshot()["stopwatch"]["elapsed_ms"], 0)

    def test_stopwatch_formatter_has_milliseconds(self):
        self.assertEqual(clock.format_stopwatch(3_723_004), "01:02:03.004")

    def test_stopwatch_elapsed_uses_local_timestamp(self):
        self.assertEqual(clock.stopwatch_elapsed_ms(250, 100.0, now=101.234), 1484)
        self.assertEqual(clock.stopwatch_elapsed_ms(250, None, now=101.234), 250)

if __name__ == "__main__": unittest.main()
