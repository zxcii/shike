"""Real media integration check. Run with the local server already started."""
import argparse
import json
import sys
import time
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--file", default=str(Path(__file__).parent / "assets" / "jfk.flac"))
    parser.add_argument("--model", default="base")
    parser.add_argument("--language", default="en")
    parser.add_argument("--expect", default="country")
    args = parser.parse_args()
    source = Path(args.file)
    with httpx.Client(base_url=args.url, timeout=120) as client:
        with source.open("rb") as file:
            response = client.post("/api/jobs", data={"model": args.model, "language": args.language}, files={"file": (source.name, file)})
        response.raise_for_status()
        job_id = response.json()["id"]
        print(f"Created job: {job_id}", flush=True)
        last_status = None
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] != last_status:
                print(job["status"], job["message"], flush=True)
                last_status = job["status"]
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        assert job["status"] == "completed", json.dumps(job, ensure_ascii=False)
        text = " ".join(segment["text"] for segment in job["segments"])
        print("Transcript:", text[:400] + ("…" if len(text) > 400 else ""), flush=True)
        assert args.expect.lower() in text.lower(), text
        assert job["duration"] > 5
        assert all(0 <= segment["start"] < segment["end"] <= job["duration"] + 0.001 for segment in job["segments"])
        for kind in ("txt", "md", "srt", "json", "study"):
            result = client.get(f"/api/jobs/{job_id}/export/{kind}")
            assert result.status_code == 200 and result.text.strip(), kind
        media = client.get(f"/api/jobs/{job_id}/media", headers={"Range": "bytes=0-127"})
        assert media.status_code == 206 and len(media.content) == 128
        print("PASS: actual transcription, all five exports, media range playback, saved history.", flush=True)


if __name__ == "__main__":
    main()
