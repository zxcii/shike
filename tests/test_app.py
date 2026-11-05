import json
import sys
import tempfile
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from exports import export, study_prompt, timestamp
from fastapi.testclient import TestClient


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=app.project_env.DATA / "tmp")
        self.path_patch = patch.object(app, "JOBS", Path(self.directory.name))
        self.path_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.path_patch.stop()
        self.directory.cleanup()

    def fixture(self, status="completed"):
        job = app.new_job("transcribe", "课程 <测试>.mp4", "base", "zh")
        job.update(status=status, media_file="source.mp4", duration=7, segments=[
            {"start": 0, "end": 2.9996, "text": "原始转写内容"},
            {"start": 3, "end": 7, "text": "第二个知识点"}])
        app.write_job(job)
        (app.job_dir(job["id"]) / "source.mp4").write_bytes(b"test media")
        return job

    def test_save_and_all_exports_use_correct_source(self):
        job = self.fixture()
        base = f"/api/jobs/{job['id']}"
        response = self.client.patch(base + "/text", json={"text": "校对后的课程知识"})
        self.assertEqual(response.status_code, 200)
        for kind in ("txt", "md", "study", "json"):
            response = self.client.get(base + "/export/" + kind)
            self.assertEqual(response.status_code, 200)
            self.assertIn("校对后的课程知识", response.text)
            self.assertIn("filename*=UTF-8", response.headers["content-disposition"])
        subtitles = self.client.get(base + "/export/srt").text
        self.assertIn("原始转写内容", subtitles)
        self.assertNotIn("校对后的", subtitles)
        self.assertIn("00:00:03,000", subtitles)

    def test_upload_validation_and_content(self):
        for filename, content, expected in [("bad.exe", b"x", 422), ("empty.mp4", b"", 422)]:
            response = self.client.post("/api/jobs", files={"file": (filename, content, "video/mp4")})
            self.assertEqual(response.status_code, expected)
        with patch.object(app.TASKS, "put") as task:
            response = self.client.post("/api/jobs", data={"model": "base", "language": "zh"}, files={"file": ("lesson.mp4", b"test video", "video/mp4")})
        self.assertEqual(response.status_code, 200)
        job = response.json()
        self.assertEqual(job["status"], "queued")
        task.assert_called_once_with(job["id"])
        self.assertEqual((app.job_dir(job["id"]) / job["media_file"]).read_bytes(), b"test video")

    def test_cancel_retry_and_delete(self):
        job = self.fixture("queued")
        base = f"/api/jobs/{job['id']}"
        self.assertEqual(self.client.delete(base).status_code, 409)
        self.assertEqual(self.client.post(base + "/cancel").json()["status"], "cancelled")
        with patch.object(app.TASKS, "put"):
            result = self.client.post(base + "/retry").json()
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["status"], "queued")
        self.client.post(base + "/cancel")
        self.assertEqual(self.client.delete(base).status_code, 200)
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertFalse(app.job_dir(job["id"]).exists())

    def test_cross_site_and_invalid_path_are_rejected(self):
        self.assertEqual(self.client.post("/api/models/download", json={"model": "tiny"}, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/jobs/not-a-uuid").status_code, 404)
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 403)

    def test_queue_capacity(self):
        for _ in range(4):
            self.fixture("queued")
        response = self.client.post("/api/models/download", json={"model": "tiny"})
        self.assertEqual(response.status_code, 409)

    def test_fourth_upload_is_allowed_but_fifth_is_rejected(self):
        for _ in range(3):
            self.fixture("queued")
        with patch.object(app.TASKS, "put"):
            fourth = self.client.post("/api/jobs", files={"file": ("four.mp4", b"media")})
        self.assertEqual(fourth.status_code, 200)
        fifth = self.client.post("/api/jobs", files={"file": ("five.mp4", b"media")})
        self.assertEqual(fifth.status_code, 409)

    def test_media_range_and_persisted_history(self):
        job = self.fixture()
        response = self.client.get(f"/api/jobs/{job['id']}/media", headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"test")
        history = self.client.get("/api/jobs").json()
        self.assertEqual(history[0]["segment_count"], 2)
        self.assertNotIn("segments", history[0])
        self.assertEqual(app.read_job(job["id"])["filename"], job["filename"])

    def test_no_edit_until_complete(self):
        job = self.fixture("transcribing")
        self.assertEqual(self.client.patch(f"/api/jobs/{job['id']}/text", json={"text": "test"}).status_code, 409)

    def test_review_resolution_preserves_both_sources(self):
        job = self.fixture()
        job["segments"][0].update(id=0, text="He go.", review_text="He goes.", review_state="needs_review")
        app.write_job(job)
        base = f"/api/jobs/{job['id']}"
        result = self.client.post(base + "/segments/0/resolve", json={"choice": "review"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["segments"][0]["text"], "He go.")
        self.assertEqual(result.json()["segments"][0]["resolved_text"], "He goes.")
        self.assertEqual(result.json()["review_count"], 0)
        self.assertIn("He goes.", self.client.get(base + "/export/txt").text)
        self.assertIn("He go.", self.client.get(base + "/export/srt").text)

    def test_review_cannot_overwrite_manual_full_text(self):
        job = self.fixture()
        job["segments"][0].update(id=0, review_text="other", review_state="needs_review")
        job["edited_text"] = "My carefully edited transcript"
        app.write_job(job)
        base = f"/api/jobs/{job['id']}/segments/0/resolve"
        self.assertEqual(self.client.post(base, json={"choice": "review"}).status_code, 409)
        confirmed = self.client.post(base, json={"choice": "confirm"})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["edited_text"], "My carefully edited transcript")

    def test_frame_access_is_limited_to_job_evidence(self):
        job = self.fixture()
        folder = app.job_dir(job["id"]) / "frames"
        folder.mkdir()
        (folder / "frame-00000.jpg").write_bytes(b"test image")
        job["visuals"] = [{"image": "frame-00000.jpg", "start": 0, "text": "Hello"}]
        app.write_job(job)
        base = f"/api/jobs/{job['id']}/frames/"
        self.assertEqual(self.client.get(base + "frame-00000.jpg").content, b"test image")
        self.assertEqual(self.client.get(base + "job.json").status_code, 404)

    def test_component_cannot_be_selected_as_transcriber(self):
        result = self.client.post("/api/jobs", data={"model": "mimo-tokenizer"}, files={"file": ("video.mp4", b"test")})
        self.assertEqual(result.status_code, 422)

    def test_cancellation_stops_the_child_engine_too(self):
        job = self.fixture("transcribing")
        stopped = Path(self.directory.name) / "child-stopped"
        child = ("import signal,sys,time; from pathlib import Path; "
                 f"signal.signal(signal.SIGTERM, lambda *a: (Path({str(stopped)!r}).write_text('stopped'),sys.exit(0))); "
                 "print('ready',flush=True); time.sleep(60)")
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-u','-c',{child!r}]); time.sleep(60)"
        process = subprocess.Popen([sys.executable, "-u", "-c", parent], start_new_session=True, stdout=subprocess.PIPE, text=True)
        app.CURRENT[job["id"]] = process
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            response = self.client.post(f"/api/jobs/{job['id']}/cancel")
            self.assertEqual(response.json()["status"], "cancelled")
            process.wait(timeout=3)
            deadline = time.monotonic() + 2
            while not stopped.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(stopped.exists(), "GPU child did not receive cancellation")
        finally:
            app.stop_process_group(process)
            process.wait(timeout=3)
            process.stdout.close()
            app.CURRENT.pop(job["id"], None)


class ExportTests(unittest.TestCase):
    def test_timestamp_carry(self):
        self.assertEqual(timestamp(59.9996, True), "00:01:00,000")
        self.assertEqual(timestamp(3599.9996, True), "01:00:00,000")
        self.assertEqual(timestamp(-1, True), "00:00:00,000")

    def test_intentionally_empty_edit_is_preserved(self):
        job = {"filename": "课程.mp4", "segments": [{"start": 0, "end": 1, "text": "原文"}], "edited_text": ""}
        self.assertEqual(export(job, "txt"), "")
        self.assertIn("<课程转写>\n\n</课程转写>", study_prompt(job))


if __name__ == "__main__":
    unittest.main(verbosity=2)
