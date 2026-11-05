import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thermal_control as control
from fastapi.testclient import TestClient
import app


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = lambda: None

    def clock(self):
        return self.now

    def sleep(self, duration):
        self.now += duration
        self.on_sleep()


class SchedulingTests(unittest.TestCase):
    def governor(self, profile="slow", thermal=lambda: 0):
        timer = FakeTime()
        events = []
        governor = control.Governor(lambda **event: events.append(event), profile_reader=lambda: profile,
                                    thermal_reader=thermal, clock=timer.clock, sleeper=timer.sleep)
        return governor, timer, events

    def test_three_duty_ratios(self):
        for profile, expected in [("fast", 0), ("medium", 1/.35-1), ("slow", 19)]:
            with self.subTest(profile=profile):
                governor, timer, events = self.governor(profile)
                timer.now = 1
                governor.checkpoint()
                self.assertAlmostEqual(governor.cooling_seconds, expected, places=3)
                self.assertAlmostEqual(governor.work_seconds, 1, places=3)
                self.assertEqual(governor.summary()["speed_profile"], profile)

    def test_live_switch_interrupts_a_long_cooldown(self):
        governor, timer, events = self.governor()
        profile = ["slow"]
        governor.profile_reader = lambda: profile[0]
        timer.now = 1
        timer.on_sleep = lambda: profile.__setitem__(0, "fast")
        governor.checkpoint()
        self.assertLessEqual(governor.cooling_seconds, .25)
        self.assertEqual(events[-1]["speed_profile"], "fast")

    def test_heat_pressure_extends_cooling_until_recovered(self):
        governor, timer, events = self.governor("slow")
        governor.thermal_reader = lambda: 1 if timer.now < 3 else 0
        timer.now = .06
        governor.checkpoint()
        self.assertGreaterEqual(timer.now, 3)
        self.assertTrue(any(e["throttle_state"] == "thermal_wait" for e in events))
        self.assertEqual(events[-1]["throttle_state"], "working")

    def test_fast_also_stops_for_serious_thermal_pressure(self):
        governor, timer, events = self.governor("fast")
        governor.thermal_reader = lambda: 2 if timer.now < 3 else 0
        timer.now = .1
        governor.checkpoint()
        self.assertGreaterEqual(timer.now, 3)

    def test_unavailable_sensor_still_enforces_slow_duty(self):
        governor, timer, _ = self.governor("slow", lambda: None)
        timer.now = 1
        governor.checkpoint()
        self.assertAlmostEqual(governor.cooling_seconds, 19)

    def test_async_gpu_work_is_drained_before_sleep_and_hooks_restore(self):
        governor, timer, _ = self.governor()
        class FakeMLX:
            pending = False
            def eval(self, *args, **kwargs):
                self.pending = True
                timer.now += 1
            def async_eval(self, *args, **kwargs):
                self.pending = True
            def synchronize(self):
                self.pending = False
        mx = FakeMLX()
        original_eval, original_async = mx.eval, mx.async_eval
        timer.on_sleep = lambda: self.assertFalse(mx.pending, "GPU was still working during cooling")
        with self.assertRaisesRegex(RuntimeError, "test"):
            with control.throttle_mlx(mx, governor, "mlx-whisper"):
                mx.async_eval("same tensors")
                self.assertAlmostEqual(governor.cooling_seconds, 19)
                raise RuntimeError("test")
        self.assertEqual(mx.eval, original_eval)
        self.assertEqual(mx.async_eval, original_async)


class SettingsTests(unittest.TestCase):
    def test_late_cooling_events_do_not_leave_a_finished_job_paused(self):
        job = {"status":"completed", "segments":[]}
        app.apply_worker_event(job, {"throttle_state":"cooling", "cooldown_remaining":1.5})
        self.assertEqual(job["throttle_state"], "idle")
        self.assertEqual(job["cooldown_remaining"], 0)

    def test_missing_or_invalid_config_is_low_heat_by_default(self):
        with tempfile.TemporaryDirectory(dir=app.project_env.DATA / "tmp") as folder:
            path = Path(folder) / "performance.json"
            self.assertEqual(control.read_profile(path), "slow")
            path.write_text('{"profile":"typo"}')
            self.assertEqual(control.read_profile(path), "slow")
            path.write_text('broken')
            self.assertEqual(control.read_profile(path), "slow")

    def test_api_persists_three_modes_and_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory(dir=app.project_env.DATA / "tmp") as folder:
            path = Path(folder) / "performance.json"
            jobs = Path(folder) / "jobs"
            jobs.mkdir()
            with patch.object(control, "SETTINGS", path), patch.object(app, "JOBS", jobs), TestClient(app.app) as client:
                self.assertEqual(client.get("/api/performance").json()["profile"], "slow")
                for profile in ["fast", "medium", "slow"]:
                    response = client.patch("/api/performance", json={"profile": profile})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.get("/api/performance").json()["profile"], profile)
                    self.assertEqual(app.new_job("transcribe", "lesson.mp4", "mimo-v2.5")["speed_profile"], profile)
                self.assertEqual(client.patch("/api/performance", json={"profile":"invalid"}).status_code, 422)
                self.assertEqual(client.patch("/api/performance", json={"profile":"fast"}, headers={"Origin":"https://example.com"}).status_code, 403)
                self.assertEqual(control.read_profile(), "slow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
