"""Real bilingual, OCR, dual-GPU-stage, export and provenance acceptance check."""
import json
import time
from pathlib import Path
import httpx
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(Path(__file__).parent / "assets" / "中英混合语法课示例.mp4"))
    parser.add_argument("--min-chunk", type=float, default=0)
    args = parser.parse_args()
    source = Path(args.file)
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=120) as client:
        with source.open("rb") as media:
            response = client.post("/api/jobs", files={"file": (source.name, media, "video/mp4")},
                                   data={"model": "mimo-v2.5", "language": "mixed", "review_enabled": "true", "ocr_enabled": "true"})
        response.raise_for_status()
        job_id = response.json()["id"]
        print("JOB:", job_id, flush=True)
        state = None
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            changed = (job["status"], job.get("stage"), job.get("message"))
            if changed != state:
                print(*changed, flush=True)
                state = changed
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(2)
        assert job["status"] == "completed", json.dumps(job, ensure_ascii=False)
        assert not job.get("warnings"), job["warnings"]
        assert job["segments"]
        assert all("review_text" in s for s in job["segments"])
        assert all(0 <= s["start"] < s["end"] <= job["duration"] + .001 for s in job["segments"])
        assert max(s["end"] - s["start"] for s in job["segments"]) >= args.min_chunk
        text = "\n".join(s["text"] for s in job["segments"])
        print("PRIMARY:", text, flush=True)
        assert any("\u4e00" <= c <= "\u9fff" for c in text)
        assert "school" in text.lower()
        visual_text = "\n".join(v["text"] for v in job["visuals"])
        assert "He go to school every day." in visual_text, visual_text
        assert len(job["engine_metrics"]) == 2
        assert all(m.get("offline_guard") for m in job["engine_metrics"])
        assert all(m["gpu_peak_gb"] < 11 for m in job["engine_metrics"]), job["engine_metrics"]
        for kind in ("txt", "md", "srt", "json", "study"):
            response = client.get(f"/api/jobs/{job_id}/export/{kind}")
            assert response.status_code == 200 and response.text.strip()
            if kind in {"md", "study"}:
                assert "He go to school every day." in response.text
                assert "画面" in response.text
        assert client.get(f"/api/jobs/{job_id}/frames/{job['visuals'][0]['image']}").status_code == 200
        print("METRICS:", json.dumps(job["engine_metrics"], ensure_ascii=False), flush=True)
        print("PASS: mixed speech, two real local model passes, native OCR, wrong example preserved, all exports, frame evidence.", flush=True)


if __name__ == "__main__":
    main()
