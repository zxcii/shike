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
        job=json.loads(job_path.read_text())
        stage_model=job.get('review_model','whisper-large-v3-mlx') if stage=='review' else job['model']
        cpu=stage_model in project_env.MODEL_CATALOG and not project_env.MODEL_CATALOG[stage_model].get('engine')
        limiter = CPUProcessLimiter(process, emit) if stage=='legacy' or cpu else None
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


def accurate_pipeline(job_path, job, audio, duration, remote=None):
    import gc
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    from pipeline import chunk_ranges
    governor = Governor(emit) if job.get('uses_local_compute',True) else None
    options = VadOptions(threshold=.35, min_speech_duration_ms=80, min_silence_duration_ms=450, speech_pad_ms=250)
    speech = get_speech_timestamps(audio, options)
    chunks = chunk_ranges(len(audio), speech)
    np.save(job_path.parent / "audio.npy", audio)
    (job_path.parent / "chunks.json").write_text(json.dumps(chunks))
    del audio
    gc.collect()
    if governor:governor.checkpoint()
    emit(duration=duration, chunk_count=len(chunks), timestamp_precision="chunk", stage="ocr", progress=0,
         status="transcribing", message="正在提取画面文字与字幕…")
    if job.get("ocr_enabled", True):
        from visual_extract import embedded_subtitles, extract_visuals
        try:
            embedded_subtitles(job_path.parent / job["media_file"], emit)
            visual=job.get('vision_binding',{'source':'local'})
            recognizer=(lambda path:remote.recognize(path,visual,job)) if visual['source']=='provider' else None
            extract_visuals(job_path.parent / job["media_file"], job_path.parent, duration, emit, governor=governor if visual['source']=='local' else None,
                            recognizer=recognizer,source_label='provider-vision' if recognizer else 'vision-ocr')
        except Exception as error:
            print(f"OCR warning: {error}", file=sys.stderr)
            emit(warning=f"画面提取未完整完成：{error}。语音识别继续；可在任务详情中查看。")
    if chunks:
        import numpy as np
        for stage in ['primary']+(['review'] if job.get('review_enabled') else []):
            binding=job.get('review_binding',{'source':'local'}) if stage=='review' else job.get('speech_binding',{'source':'local'})
            if binding['source']=='provider':
                from pipeline import trim_overlap
                loaded=np.load(job_path.parent/'audio.npy',mmap_mode='r');previous='';previous_end=0
                for index,chunk in enumerate(chunks):
                    raw,label=remote.transcribe(np.array(loaded[chunk['start_sample']:chunk['end_sample']],copy=True),binding,job)
                    text=trim_overlap(previous,raw) if previous_end>chunk['start'] else raw
                    previous,previous_end=raw,chunk['end']
                    payload={'id':chunk['id'],'start':chunk['start'],'end':chunk['end'],'text':text,'raw_text':raw,'engine':label}
                    if stage=='review':emit(review={**payload,'segments':[]})
                    else:payload['review_state']='pending' if job.get('review_enabled') else 'not_requested';emit(segment=payload)
                    emit(status='transcribing',stage=stage,progress=round((60 if stage=='review' else 20)+(index+1)/len(chunks)*(39 if stage=='review' or job.get('review_enabled') else 79),1),message=f"供应商语音{'复核' if stage=='review' else '转写'} · {index+1}/{len(chunks)} 段",throttle_state='idle')
                del loaded
            else:run_stage(job_path,stage)
    emit(status="completed", progress=100, stage="completed", throttle_state="idle", cooldown_remaining=0,
         message="转写与复核完成，请核对有分歧的片段。" if chunks and job.get("review_enabled") else "转写完成。" if chunks else "没有检测到语音；若视频有画面文字，可在画面资料中查看。")


def run(job_path):
    job = json.loads(job_path.read_text())
    if job['model'] in project_env.MODEL_CATALOG:get_model(job['model'])
    if job["kind"] == "download":
        emit(status="completed", progress=100, message="模型已下载，可离线使用。")
        return
    if job.get("review_enabled"):
        review=job.get('review_model','whisper-large-v3-mlx')
        if review in project_env.MODEL_CATALOG:get_model(review)
    emit(status="preparing", message="正在本机读取音轨…", progress=None)
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
    if job["model"].startswith("provider:") or job.get("review_enabled") or job.get("ocr_enabled") or project_env.MODEL_CATALOG[job["model"]].get("engine") in {"mimo", "mlx-whisper"}:
        try:
            from remote_inference import RemoteSession
            with RemoteSession() as remote:
                accurate_pipeline(job_path, job, audio, duration, remote)
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
