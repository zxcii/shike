import project_env
import json
import logging
import os
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from exports import export
import quiz
import library
import ai_service
from quiz import router as quiz_router
from pipeline import comparison
from thermal_control import PROFILES, read_profile, write_profile

ROOT = project_env.ROOT
JOBS = project_env.DATA / "jobs"
MAX_BYTES = 2 * 1024**3
ACTIVE = {"queued", "downloading", "preparing", "transcribing"}
EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif", ".opus", ".mpeg", ".mpg"}
LOCK = threading.RLock()
TASKS = queue.Queue()
CURRENT = {}
STOP = threading.Event()
LOGGER = logging.getLogger("whisper-study")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id):
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(404, "记录不存在")
    return JOBS / job_id


def read_job(job_id):
    with LOCK:
        path = job_dir(job_id) / "job.json"
        if not path.is_file():
            raise HTTPException(404, "记录不存在")
        return json.loads(path.read_text(encoding="utf-8"))


def write_job(job):
    with LOCK:
        folder = job_dir(job["id"])
        folder.mkdir(exist_ok=True)
        temporary = folder / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "job.json")


def update_job(job_id, **changes):
    with LOCK:
        job = read_job(job_id)
        job.update(changes)
        job["updated_at"] = utcnow()
        write_job(job)
        return job


def list_jobs():
    with LOCK:
        jobs = []
        for path in JOBS.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                jobs.append(job)
            except (ValueError, OSError):
                LOGGER.exception("Cannot read %s", path)
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)


def ensure_capacity(exclude=None):
    if sum(j["id"] != exclude and j["status"] in ACTIVE | {"uploading"} for j in list_jobs()) >= 4:
        raise HTTPException(409, "最多同时排队 4 个任务，请等待或取消现有任务。")


def new_job(kind, filename, model, language="auto", vocabulary=""):
    return dict(id=str(uuid.uuid4()), kind=kind, filename=filename, model=model,
                language=language, vocabulary=vocabulary, status="queued", progress=0,
                message="已加入队列，等待处理…", created_at=utcnow(), updated_at=utcnow(),
                segments=[], visuals=[], subtitles=[], warnings=[], engine_metrics=[], edited_text=None, duration=None,
                review_enabled=False, ocr_enabled=False, stage=None, speed_profile=read_profile())


def stop_process_group(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            # A native CPU engine may currently be stopped for its cool-down.
            # Deliver its pending termination immediately, not at the next tick.
            os.killpg(process.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


def apply_worker_event(current, event):
    event = dict(event)
    for key, collection in (("segment", "segments"), ("visual", "visuals"), ("subtitle", "subtitles"),
                            ("warning", "warnings"), ("engine_metric", "engine_metrics")):
        if key in event:
            current.setdefault(collection, []).append(event.pop(key))
    if "review" in event:
        review = event.pop("review")
        for segment in current["segments"]:
            if segment.get("id") == review["id"]:
                segment.update(review_text=review["text"], review_raw_text=review["raw_text"], review_segments=review["segments"])
                segment.update(comparison(segment["text"], review["text"]))
                break
    current.update(event)
    if current.get("status") in {"completed", "failed", "cancelled"}:
        current.update(throttle_state="idle", cooldown_remaining=0)
    current["review_count"] = sum(s.get("review_state") in {"needs_review", "pending"} for s in current["segments"])
    return current


def work_loop():
    while not STOP.is_set():
        try:
            job_id = TASKS.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            with LOCK:
                job = read_job(job_id)
                if job["status"] != "queued":
                    continue
                update_job(job_id, status="preparing", message="正在启动本地识别引擎…", started_at=time.time())
                log = (job_dir(job_id) / "worker.log").open("a", encoding="utf-8")
                process = subprocess.Popen([sys.executable, "-u", str(ROOT / "worker.py"), str(job_dir(job_id) / "job.json")],
                                           cwd=ROOT, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", start_new_session=True)
                CURRENT[job_id] = process
            try:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with LOCK:
                        current = read_job(job_id)
                        if current["status"] == "cancelled":
                            break
                        apply_worker_event(current, event)
                        current["updated_at"] = utcnow()
                        if current["status"] in {"completed", "failed"}:
                            current["elapsed"] = round(time.time() - current["started_at"], 1)
                        if current["status"] == "failed":
                            current["error_detail"] = current["message"]
                            if "downloading" == job.get("status") or any(word in current["message"].lower() for word in ["connect", "network", "hugging", "timeout", "http", "offline"]):
                                current["message"] = "模型下载失败，请检查网络后重试。也可以在模型管理中先下载模型。"
                        write_job(current)
                process.wait()
                with LOCK:
                    current = read_job(job_id)
                    if current["status"] in ACTIVE:
                        update_job(job_id, status="failed", message="识别进程意外退出。可重试或尝试更小的模型，详细日志保存在本任务的 worker.log。")
            finally:
                if process.poll() is None:
                    stop_process_group(process)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                process.stdout.close()
                log.close()
                with LOCK:
                    CURRENT.pop(job_id, None)
        except Exception:
            LOGGER.exception("Job failed: %s", job_id)
            try:
                update_job(job_id, status="failed", message="任务处理失败，请重试并查看 data/server.log。")
            except Exception:
                pass
        finally:
            TASKS.task_done()


@asynccontextmanager
async def lifespan(app):
    await ai_service.startup()
    STOP.clear()
    for job in list_jobs():
        if job["status"] in ACTIVE | {"uploading"}:
            update_job(job["id"], status="failed", message="上次运行已中断。点击重试可重新转写。")
    worker = threading.Thread(target=work_loop, daemon=True, name="transcription-queue")
    worker.start()
    yield
    await ai_service.shutdown()
    STOP.set()
    with LOCK:
        for job_id, process in list(CURRENT.items()):
            update_job(job_id, status="cancelled", message="应用已关闭，可重试。")
            if process.poll() is None:
                stop_process_group(process)
    worker.join(timeout=7)


app = FastAPI(title="拾课 · 本地学习工作台", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    return response


@app.get("/api/health")
def health():
    return {"app": "whisper-study", "version": 3, "ok": True, "project": str(ROOT)}


class PerformanceEdit(BaseModel):
    profile: str


@app.get("/api/performance")
def performance():
    return {"profile": read_profile(), "profiles": [{"id": key, **value} for key, value in PROFILES.items()]}


@app.patch("/api/performance")
def set_performance(body: PerformanceEdit):
    if body.profile not in PROFILES:
        raise HTTPException(422, "请选择快、中、慢三档")
    with LOCK:
        write_profile(body.profile)
    return performance()


class ShutdownRequest(BaseModel):
    project: str


@app.post("/api/shutdown")
def shutdown(body: ShutdownRequest):
    if body.project != str(ROOT):
        raise HTTPException(403, "项目不匹配")

    def stop_server():
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)

    return JSONResponse({"ok": True}, background=BackgroundTask(stop_server))


@app.get("/api/models")
def models():
    jobs = list_jobs()
    return [dict(id=name, **info, ready=project_env.model_ready(name),
                 downloading=any((j["model"] == name or name in project_env.MODEL_CATALOG.get(j["model"], {}).get("dependencies", []) or (name == "whisper-large-v3-mlx" and j.get("review_enabled"))) and j["status"] in ACTIVE for j in jobs)) for name, info in project_env.MODEL_CATALOG.items()]


class DownloadRequest(BaseModel):
    model: str


@app.post("/api/models/download")
def download_model(body: DownloadRequest):
    if body.model not in project_env.MODEL_CATALOG:
        raise HTTPException(422, "不支持的模型")
    with LOCK:
        ensure_capacity()
        for job in list_jobs():
            if job["model"] == body.model and job["status"] in ACTIVE:
                return job
        job = new_job("download", f"下载模型 · {body.model}", body.model)
        write_job(job)
        TASKS.put(job["id"])
    return job


@app.get("/api/jobs")
def history():
    return [{k: v for k, v in job.items() if k not in {"segments", "visuals", "subtitles", "edited_text", "error_detail", "vocabulary"}} | {"segment_count": len(job["segments"]), "visual_count": len(job.get("visuals", []))} for job in list_jobs()]


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), model: str = Form("mimo-v2.5"), language: str = Form("mixed"), vocabulary: str = Form(""), review_enabled: bool = Form(True), ocr_enabled: bool = Form(True), book_id: str = Form(""), category_id: str = Form("")):
    if model not in project_env.MODEL_CATALOG or not project_env.MODEL_CATALOG[model].get("selectable", True) or language not in {"auto", "mixed", "zh", "en", "ja", "ko", "fr", "de", "es"}:
        raise HTTPException(422, "模型或语言无效")
    engine = project_env.MODEL_CATALOG[model].get("engine")
    if engine == "mimo" and language not in {"auto", "mixed", "zh", "en"}:
        raise HTTPException(422, "MiMo 支持中文、英文及中英混合；其他语言请选择 Whisper。")
    if len(vocabulary) > 2000:
        raise HTTPException(422, "术语提示最多 2000 字")
    if book_id or category_id:
        with library.database() as connection:
            library.location(connection, book_id or None, category_id or None)
    filename = (file.filename or "video.mp4").replace("\\", "/").split("/")[-1][:220]
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(422, "不支持此格式，请导入常见视频或音频文件。")
    with LOCK:
        ensure_capacity()
        job = new_job("transcribe", filename, model, language, vocabulary)
        job.update(review_enabled=review_enabled and engine == "mimo", ocr_enabled=ocr_enabled and engine in {"mimo", "mlx-whisper"})
        job["status"] = "uploading"
        job["media_file"] = "source" + suffix
        write_job(job)
    folder = job_dir(job["id"])
    size = 0
    try:
        with (folder / job["media_file"]).open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "文件超过 2 GB，请先压缩或分段。")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "文件是空的，请重新选择。")
        with LOCK:
            ensure_capacity(exclude=job["id"])
            job.update(status="queued", size=size)
            write_job(job)
            if book_id or category_id:
                library.assign(job["id"], book_id or None, category_id or None)
            TASKS.put(job["id"])
        return job
    except BaseException:
        shutil.rmtree(folder)
        raise
    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def detail(job_id: str):
    return read_job(job_id)


@app.get("/api/jobs/{job_id}/media")
def media(job_id: str):
    job = read_job(job_id)
    if job["kind"] != "transcribe":
        raise HTTPException(404, "无媒体文件")
    return FileResponse(job_dir(job_id) / job["media_file"])


@app.get("/api/jobs/{job_id}/frames/{filename}")
def frame_image(job_id: str, filename: str):
    job = read_job(job_id)
    if filename not in {v["image"] for v in job.get("visuals", [])} or Path(filename).name != filename:
        raise HTTPException(404, "画面不存在")
    return FileResponse(job_dir(job_id) / "frames" / filename, media_type="image/jpeg")


class ReviewEdit(BaseModel):
    choice: str


@app.post("/api/jobs/{job_id}/segments/{segment_id}/resolve")
def resolve_review(job_id: str, segment_id: int, body: ReviewEdit):
    if body.choice not in {"primary", "review", "confirm"}:
        raise HTTPException(422, "无效的校对选择")
    with LOCK:
        job = read_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "请等待处理完成后校对")
        segment = next((s for s in job["segments"] if s.get("id") == segment_id), None)
        if segment is None:
            raise HTTPException(404, "片段不存在")
        if body.choice == "review":
            if job.get("edited_text") is not None:
                raise HTTPException(409, "已有全文校对，请在课程全文中修改，随后标记该片段已核对。")
            if "review_text" not in segment:
                raise HTTPException(409, "此片段还没有复核结果")
            segment["resolved_text"] = segment["review_text"]
        elif body.choice == "primary":
            segment["resolved_text"] = segment["text"]
        segment.update(review_state="confirmed", resolution=body.choice)
        job["review_count"] = sum(s.get("review_state") in {"needs_review", "pending"} for s in job["segments"])
        write_job(job)
        return job


class TextEdit(BaseModel):
    text: str = Field(max_length=2_000_000)


@app.patch("/api/jobs/{job_id}/text")
def edit(job_id: str, body: TextEdit):
    with LOCK:
        job = read_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "转写完成后才能编辑")
        return update_job(job_id, edited_text=body.text)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    with LOCK:
        job = read_job(job_id)
        if job["status"] in ACTIVE:
            job = update_job(job_id, status="cancelled", message="任务已取消，已转写片段仍可导出。")
            process = CURRENT.get(job_id)
            if process and process.poll() is None:
                stop_process_group(process)
        return job


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str):
    with LOCK:
        job = read_job(job_id)
        if job["status"] not in {"failed", "cancelled"} or job_id in CURRENT:
            raise HTTPException(409, "该任务仍在运行或已完成，请稍后重试。")
        ensure_capacity()
        shutil.rmtree(job_dir(job_id) / "frames", ignore_errors=True)
        job = update_job(job_id, status="queued", segments=[], visuals=[], subtitles=[], warnings=[], engine_metrics=[], review_count=0, edited_text=None, progress=0, message="已重新加入队列…", error_detail=None, duration=None, stage=None)
        TASKS.put(job_id)
        return job


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    with LOCK:
        job = read_job(job_id)
        if job["status"] in ACTIVE or job_id in CURRENT:
            raise HTTPException(409, "请先取消任务，等待进程停止后再删除。")
        shutil.rmtree(job_dir(job_id))
        if library.DB.exists():
            library.forget(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/export/{kind}")
def download(job_id: str, kind: str):
    job = read_job(job_id)
    if kind not in {"txt", "md", "srt", "json", "study"}:
        raise HTTPException(404, "不支持此导出格式")
    if kind == "study" and library.DB.exists():
        with library.database() as connection:
            placement = connection.execute('SELECT title FROM courses WHERE job_id=?', (job_id,)).fetchone()
            if placement and placement['title']:
                job['title'] = placement['title']
    extension = "md" if kind == "study" else kind
    name = f"{Path(job['filename']).stem}{'-出题提示词' if kind == 'study' else ''}.{extension}"
    return Response(export(job, kind), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


library.job_reader = read_job
quiz.source_resolver = read_job
app.include_router(library.router)
app.include_router(quiz_router)
app.include_router(ai_service.router)

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
