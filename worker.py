"""Isolated inference process: terminating this process really cancels a job."""
import project_env  # Set cache locations before third-party imports.
import json
import os
import subprocess
import threading
import sys
from pathlib import Path
from thermal_control import Governor, prepare_environment, CPUProcessLimiter

prepare_environment()
EMIT_LOCK = threading.Lock()


def emit(**event):
    with EMIT_LOCK:
        print(json.dumps(event, ensure_ascii=False), flush=True)


def get_model(name):
    from model_manager import ensure_model
    return ensure_model(name, emit)


def run_stage(job_path, stage):
    with subprocess.Popen([sys.executable, "-u", str(project_env.ROOT / "stage_runner.py"), str(job_path), stage],
                          stdout=subprocess.PIPE, text=True, encoding="utf-8") as process:
        limiter = CPUProcessLimiter(process, emit) if stage == "legacy" else None
        if limiter:
            limiter.start()
        error = None
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    print(line, file=sys.stderr, end="")
                    continue
                if "stage_error" in event:
                    error = event["stage_error"]
                else:
                    emit(**event)
            if process.wait() != 0:
                raise RuntimeError(error or f"{stage} 进程退出（可能内存不足），详见 worker.log。")
        finally:
            if limiter:
                limiter.close()


def accurate_pipeline(job_path, job, audio, duration):
    import gc
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    from pipeline import chunk_ranges
    governor = Governor(emit)
    options = VadOptions(threshold=.35, min_speech_duration_ms=80, min_silence_duration_ms=450, speech_pad_ms=250)
    speech = get_speech_timestamps(audio, options)
    chunks = chunk_ranges(len(audio), speech)
    np.save(job_path.parent / "audio.npy", audio)
    (job_path.parent / "chunks.json").write_text(json.dumps(chunks))
    del audio
    gc.collect()
    governor.checkpoint()
    emit(duration=duration, chunk_count=len(chunks), timestamp_precision="chunk", stage="ocr", progress=0,
         status="transcribing", message="正在提取画面文字与字幕…")
    if job.get("ocr_enabled", True):
        from visual_extract import embedded_subtitles, extract_visuals
        try:
            embedded_subtitles(job_path.parent / job["media_file"], emit)
            extract_visuals(job_path.parent / job["media_file"], job_path.parent, duration, emit, governor=governor)
        except Exception as error:
            print(f"OCR warning: {error}", file=sys.stderr)
            emit(warning=f"画面提取未完整完成：{error}。语音识别继续；可在任务详情中查看。")
    if chunks:
        run_stage(job_path, "primary")
        if job.get("review_enabled"):
            run_stage(job_path, "review")
    emit(status="completed", progress=100, stage="completed", throttle_state="idle", cooldown_remaining=0,
         message="转写与复核完成，请核对有分歧的片段。" if chunks and job.get("review_enabled") else "转写完成。" if chunks else "没有检测到语音；若视频有画面文字，可在画面资料中查看。")


def run(job_path):
    job = json.loads(job_path.read_text())
    folder = get_model(job["model"])
    if job["kind"] == "download":
        emit(status="completed", progress=100, message="模型已下载，可离线使用。")
        return
    if job.get("review_enabled"):
        get_model("whisper-large-v3-mlx")
    emit(status="preparing", message="正在读取音轨并加载本地模型…", progress=None)
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    import av
    source = job_path.parent / job["media_file"]
    with av.open(str(source), metadata_errors="ignore") as container:
        if not container.streams.audio:
            raise ValueError("视频没有音轨，请选择带有讲课声音的视频。")
        if container.duration and container.duration / av.time_base > 7201:
            raise ValueError("目前支持最长 2 小时的视频，请先剪辑后导入。")
    audio = decode_audio(str(source), sampling_rate=16000)
    duration = len(audio) / 16000
    if not duration:
        raise ValueError("没有可识别的音频，请检查视频是否有声音。")
    if duration > 7200:
        raise ValueError("目前支持最长 2 小时的视频，请先剪辑后导入。")
    if project_env.MODEL_CATALOG[job["model"]].get("engine") in {"mimo", "mlx-whisper"}:
        try:
            accurate_pipeline(job_path, job, audio, duration)
        finally:
            (job_path.parent / "audio.npy").unlink(missing_ok=True)
        return
    import numpy as np
    np.save(job_path.parent / "audio.npy", audio)
    del audio
    try:
        run_stage(job_path, "legacy")
    finally:
        (job_path.parent / "audio.npy").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        run(Path(sys.argv[1]))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        emit(status="failed", progress=None, message=f"{type(exc).__name__}: {exc}")
        sys.exit(1)
