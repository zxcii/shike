"""Throttle scheduling only: model, precision, audio and decoding stay unchanged."""
import contextlib
import json
import os
import signal
import threading
import time
from pathlib import Path

import project_env

SETTINGS = project_env.DATA / "performance.json"
PROFILES = {
    "fast": {"label": "快", "duty": 1.0, "threads": 8, "description": "连续计算，优先完成任务"},
    "medium": {"label": "中", "duty": .35, "threads": 2, "description": "约 35% 工作占空比，计算与冷却交替"},
    "slow": {"label": "慢", "duty": .05, "threads": 1, "description": "约 5% 工作占空比，低热优先，耗时明显增加"},
}


def read_profile(path=None):
    try:
        value = json.loads(Path(path or SETTINGS).read_text()).get("profile")
        return value if value in PROFILES else "slow"
    except (OSError, ValueError, TypeError):
        return "slow"


def write_profile(profile, path=None):
    if profile not in PROFILES:
        raise ValueError("请选择快、中、慢三档")
    path = Path(path or SETTINGS)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"profile": profile}))
    temporary.replace(path)


def prepare_environment(profile=None):
    """Run before importing numerical libraries; no global system settings."""
    count = str(PROFILES[profile or read_profile()]["threads"])
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = count
    # Nice only this child. All tiers use the same process priority so live
    # switches do not require privileges to raise a previously lowered priority.
    try:
        os.nice(10)
    except OSError:
        pass


def system_thermal_state():
    try:
        from Foundation import NSProcessInfo
        return int(NSProcessInfo.processInfo().thermalState())
    except Exception:
        return None


class Governor:
    def __init__(self, emit=lambda **event: None, *, profile_reader=read_profile,
                 thermal_reader=system_thermal_state, clock=time.monotonic, sleeper=time.sleep):
        self.emit = emit
        self.profile_reader = profile_reader
        self.thermal_reader = thermal_reader
        self.clock, self.sleeper = clock, sleeper
        self.last_resume = clock()
        self.pending_work = 0.0
        self.work_seconds = 0.0
        self.cooling_seconds = 0.0
        self.last_report = float("-inf")
        self.current_state = None
        self.last_profile = None
        self.last_thermal_check = float("-inf")
        self.thermal = None
        self.inside = False

    def thermal_state(self):
        if self.clock() - self.last_thermal_check >= 2:
            self.thermal = self.thermal_reader()
            self.last_thermal_check = self.clock()
        return self.thermal

    def report(self, state, profile, remaining=0.0):
        thermal_transition = (state == "thermal_wait") != (self.current_state == "thermal_wait")
        if self.current_state is None or profile != self.last_profile or thermal_transition or self.clock() - self.last_report >= 1:
            self.current_state, self.last_report = state, self.clock()
            self.last_profile = profile
            self.emit(speed_profile=profile, throttle_state=state, cooldown_remaining=round(max(0, remaining), 1),
                      duty_target=PROFILES[profile]["duty"], thermal_state=self.thermal,
                      throttle_work_seconds=round(self.work_seconds, 3), throttle_cooling_seconds=round(self.cooling_seconds, 3))

    def checkpoint(self):
        """Call only after actual work is finished (GPU queue drained first)."""
        now = self.clock()
        work = max(0, now - self.last_resume)
        self.pending_work += work
        self.work_seconds += work
        self.last_resume = now
        profile = self.profile_reader()
        state = self.thermal_state()
        # Fair already stops low-heat tiers; serious/critical stops every tier.
        threshold = 2 if profile == "fast" else 1
        needs_thermal_rest = state is not None and state >= threshold
        if not needs_thermal_rest and (profile == "fast" or self.pending_work < .05):
            if profile == "fast":
                self.pending_work = 0
            self.report("working", profile)
            return
        earned_work = self.pending_work
        self.pending_work = 0
        rest_started = self.clock()
        while True:
            profile = self.profile_reader()  # Live changes interrupt long rests.
            duty = PROFILES[profile]["duty"]
            elapsed = self.clock() - rest_started
            remaining = earned_work * (1 / duty - 1) - elapsed
            state = self.thermal_state()
            threshold = 2 if profile == "fast" else 1
            thermal_wait = state is not None and state >= threshold
            if remaining <= .001 and not thermal_wait:
                break
            self.report("thermal_wait" if thermal_wait else "cooling", profile, remaining)
            pause = .25 if thermal_wait else min(.25, max(.001, remaining))
            before = self.clock()
            self.sleeper(pause)
            self.cooling_seconds += max(0, self.clock() - before)
        self.last_resume = self.clock()
        self.report("working", profile)

    def summary(self):
        return {"speed_profile": self.profile_reader(), "work_seconds": round(self.work_seconds, 3),
                "cooling_seconds": round(self.cooling_seconds, 3)}


@contextlib.contextmanager
def throttle_mlx(mx, governor, engine):
    """Drain async GPU work before resting, including each decoding token.

    No model/token/precision parameters change. The third-party packages are
    pinned; hooks live in this child process and are restored on exit.
    """
    evaluate, async_evaluate = mx.eval, mx.async_eval

    def finish(*arrays):
        if governor.inside:
            return
        governor.inside = True
        try:
            if arrays:
                evaluate(*arrays)
            mx.synchronize()
            governor.checkpoint()
        finally:
            governor.inside = False

    def controlled_eval(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        finish()
        return result

    def controlled_async(*args, **kwargs):
        if governor.profile_reader() == "fast":
            result = async_evaluate(*args, **kwargs)
            # Even fast must stop when macOS reports serious thermal pressure.
            if governor.thermal_state() is not None and governor.thermal >= 2:
                finish()
            return result
        result = evaluate(*args, **kwargs)
        finish()
        return result

    mx.eval, mx.async_eval = controlled_eval, controlled_async
    sampler_class = None
    original_sample = None
    try:
        if engine == "mimo":
            from mlx_audio.stt.models.mimo_v2_asr.model import MiMoSampler
            sampler_class, original_sample = MiMoSampler, MiMoSampler.sample

            def sample(self, *args, **kwargs):
                result = original_sample(self, *args, **kwargs)
                finish(result)
                return result
            sampler_class.sample = sample
        governor.checkpoint()
        yield finish
        finish()
    finally:
        mx.eval, mx.async_eval = evaluate, async_evaluate
        if sampler_class:
            sampler_class.sample = original_sample


class CPUProcessLimiter:
    """Pause native CPU kernels too; Python checkpoints cannot preempt them."""
    def __init__(self, process, emit=lambda **event: None):
        self.process, self.emit = process, emit
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def send(self, sig):
        if self.process.poll() is not None:
            return False
        try:
            os.kill(self.process.pid, sig)
            return True
        except ProcessLookupError:
            return False

    def start(self):
        self.thread.start()

    def run(self):
        last_report = 0
        try:
            while not self.stopped.is_set() and self.process.poll() is None:
                profile = read_profile()
                thermal = system_thermal_state()
                threshold = 2 if profile == "fast" else 1
                if thermal is not None and thermal >= threshold:
                    if not self.send(signal.SIGSTOP):
                        break
                    self.emit(speed_profile=profile, throttle_state="thermal_wait", thermal_state=thermal)
                    self.stopped.wait(.5)
                    continue
                if not self.send(signal.SIGCONT):
                    break
                self.stopped.wait(.1)
                if profile == "fast":
                    if time.monotonic() - last_report >= 1:
                        last_report = time.monotonic()
                        self.emit(speed_profile=profile, throttle_state="working", cooldown_remaining=0)
                    continue
                if not self.send(signal.SIGSTOP):
                    break
                started = time.monotonic()
                while not self.stopped.is_set():
                    profile = read_profile()
                    target = .1 * (1 / PROFILES[profile]["duty"] - 1)
                    remaining = target - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    if time.monotonic() - last_report >= 1:
                        last_report = time.monotonic()
                        self.emit(speed_profile=profile, throttle_state="cooling", thermal_state=thermal,
                                  cooldown_remaining=round(remaining, 1), duty_target=PROFILES[profile]["duty"])
                    self.stopped.wait(min(.1, remaining))
        finally:
            self.send(signal.SIGCONT)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        self.send(signal.SIGCONT)
