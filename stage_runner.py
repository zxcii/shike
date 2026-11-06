"""One GPU stage per process: exit releases all model and Metal allocations."""
import project_env
import contextlib
import json
import os
import resource
import sys
import time
from pathlib import Path
EVENT_STREAM = sys.stdout


def emit(**event):
    EVENT_STREAM.write(json.dumps(event, ensure_ascii=False) + "\n")
    EVENT_STREAM.flush()


def main(job_path, stage):
    from thermal_control import Governor, prepare_environment, throttle_mlx
    prepare_environment()
    if stage == "legacy":
        from cpu_stage import run
        run(job_path, emit)
        return
    job = json.loads(job_path.read_text())
    chunks = json.loads((job_path.parent / "chunks.json").read_text())
    name = job.get('review_model','whisper-large-v3-mlx') if stage=='review' else job['model']
    engine = project_env.MODEL_CATALOG[name].get('engine','faster-whisper')
    if engine=='faster-whisper':
        from cpu_stage import run_chunks
        run_chunks(job_path,emit,stage,name)
        return
    # Local GPU stages never fetch or contact a provider.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    def offline_only(event, args):
        if event == "socket.getaddrinfo" or (event == "socket.connect" and isinstance(args[1], tuple)):
            raise RuntimeError("本地识别阶段禁止网络连接；请先完整下载模型和所需资源。")
    sys.addaudithook(offline_only)
    import numpy as np
    import mlx.core as mx
    mx.set_cache_limit(256 * 1024**2)
    recommended = mx.device_info().get("max_recommended_working_set_size", 10 * 1024**3)
    mx.set_memory_limit(min(int(recommended * .9), 10 * 1024**3))
    audio = np.load(job_path.parent / "audio.npy", mmap_mode="r")
    folder = project_env.MODELS / name
    started = time.monotonic()
    governor = Governor(emit)
    emit(status="transcribing", stage=stage, progress=60 if stage == "review" else 20,
         message=f"正在加载 {name} · Apple GPU 本地推理…")
    with throttle_mlx(mx, governor, engine):
        execute(job_path, job, chunks, audio, engine, name, folder, stage, mx)
    emit(engine_metric={"stage": stage, "model": name, "gpu_peak_gb": round(mx.get_peak_memory() / 1024**3, 2),
                        "rss_peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3, 2),
                        "seconds": round(time.monotonic() - started, 1), "offline_guard": True,
                        "scheduling": governor.summary()})


def execute(job_path, job, chunks, audio, engine, name, folder, stage, mx):
    import numpy as np
    if engine == "mimo":
        from mlx_audio.stt import load
        # Third-party loaders may print informational text: stdout is JSON only.
        with contextlib.redirect_stdout(sys.stderr):
            model = load(str(folder), model_type="mimo_v2_asr")
    else:
        import mlx_whisper
    language = None if job["language"] in {"auto", "mixed"} else job["language"]
    previous_text = ""
    previous_end = 0
    from pipeline import trim_overlap
    for index, chunk in enumerate(chunks):
        waveform = np.array(audio[chunk["start_sample"]:chunk["end_sample"]], copy=True)
        if engine == "mimo":
            from scipy.signal import resample_poly
            waveform = resample_poly(waveform, 3, 2).astype(np.float32)
            with contextlib.redirect_stdout(sys.stderr):
                result = model.generate(waveform, language=language, max_tokens=1024, temperature=0, verbose=False)
            text = result.text.strip()
            details = []
            truncated = result.generation_tokens >= 1024
        else:
            with contextlib.redirect_stdout(sys.stderr):
                result = mlx_whisper.transcribe(waveform, path_or_hf_repo=str(folder), language=language,
                                               task="transcribe", temperature=0.0, condition_on_previous_text=False,
                                               initial_prompt=job.get("vocabulary") or None, verbose=None, fp16=True)
            text = result["text"].strip()
            details = [{"start": round(min(chunk["end"], chunk["start"] + s["start"]), 3),
                        "end": round(min(chunk["end"], chunk["start"] + s["end"]), 3), "text": s["text"].strip()} for s in result.get("segments", [])]
            truncated = False
        raw = text
        if previous_end > chunk["start"]:
            text = trim_overlap(previous_text, text)
        previous_text, previous_end = raw, chunk["end"]
        payload = {"id": chunk["id"], "start": chunk["start"], "end": chunk["end"], "text": text,
                   "raw_text": raw, "engine": name, "truncated": truncated}
        if stage == "review":
            emit(review={"id": chunk["id"], "text": text, "raw_text": raw, "segments": details, "engine":name})
        else:
            payload["review_state"] = "pending" if job.get("review_enabled") else "not_requested"
            emit(segment=payload)
        base, width = (60, 39) if stage == "review" else (20, 39 if job.get("review_enabled") else 79)
        emit(status="transcribing", stage=stage, progress=round(base + (index + 1) / max(1, len(chunks)) * width, 1),
             message=f"{name+' 独立复核' if stage == 'review' else '本地转写'} · {index + 1}/{len(chunks)} 段")
        mx.clear_cache()


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]), sys.argv[2])
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit(stage_error=str(error))
        sys.exit(1)
