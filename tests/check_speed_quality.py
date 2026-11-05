"""Compare real model outputs across tiers without changing the user's setting."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import project_env
import json
import subprocess
import tempfile
import time
from faster_whisper.audio import decode_audio
import numpy as np


def main():
    audio = decode_audio(str(Path(__file__).parent / "assets" / "jfk.flac"))[:4 * 16000]
    report = {"audio_seconds": len(audio) / 16000, "runs": []}
    launcher = ("import sys;from pathlib import Path;import thermal_control;"
                "thermal_control.SETTINGS=Path(sys.argv[1]);"
                "import stage_runner;stage_runner.main(Path(sys.argv[2]),sys.argv[3])")
    with tempfile.TemporaryDirectory(dir=project_env.DATA / "tmp") as folder:
        root = Path(folder)
        np.save(root / "audio.npy", audio)
        (root / "chunks.json").write_text(json.dumps([{"id": 0, "start_sample": 0, "end_sample": len(audio), "start": 0, "end": len(audio) / 16000}]))
        job = {"model": "mimo-v2.5", "language": "mixed", "review_enabled": True, "vocabulary": ""}
        (root / "job.json").write_text(json.dumps(job))
        reference = {}
        for profile in ["fast", "medium", "slow"]:
            (root / "performance.json").write_text(json.dumps({"profile": profile}))
            for stage in ["primary", "review"]:
                print(f"RUN {profile} {stage}", flush=True)
                started = time.monotonic()
                events = []
                with (root / "worker.log").open("w") as log:
                    with subprocess.Popen([sys.executable, "-u", "-c", launcher, str(root / "performance.json"), str(root / "job.json"), stage],
                                          stdout=subprocess.PIPE, stderr=log, text=True, cwd=project_env.ROOT) as child:
                        last_notice = 0
                        for line in child.stdout:
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            events.append(event)
                            if time.monotonic() - last_notice > 15:
                                last_notice = time.monotonic()
                                print(profile, stage, event.get("throttle_state") or event.get("message"), flush=True)
                        if child.wait() != 0:
                            raise RuntimeError((root / "worker.log").read_text()[-8000:])
                texts = [e[key]["text"] for e in events for key in ["segment" if stage == "primary" else "review"] if key in e]
                metrics = next(e["engine_metric"] for e in events if "engine_metric" in e)
                assert texts and texts[0], events
                if profile == "fast":
                    reference[stage] = texts
                else:
                    assert texts == reference[stage], f"Outputs differ: {profile} {stage}: {texts!r} vs {reference[stage]!r}"
                if profile == "slow":
                    scheduling = metrics["scheduling"]
                    assert scheduling["cooling_seconds"] >= scheduling["work_seconds"] * 15, scheduling
                    assert any(e.get("throttle_state") == "cooling" for e in events)
                result = {"profile": profile, "stage": stage, "text": texts, "seconds": round(time.monotonic()-started, 2), "metrics": metrics}
                report["runs"].append(result)
                print("PASS", profile, stage, texts, metrics["scheduling"], flush=True)
    report["outputs_identical"] = True
    (project_env.ROOT / "docs" / "speed-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: same real MiMo and Whisper outputs across all three scheduling tiers.", flush=True)


if __name__ == "__main__":
    main()
