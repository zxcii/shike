"""Unchanged CPU recognition parameters; the parent limits execution time."""
import json
import os
import resource
import time


def run(job_path, emit):
    import numpy as np
    from faster_whisper import WhisperModel
    import project_env
    from thermal_control import read_profile
    job = json.loads(job_path.read_text())
    audio = np.load(job_path.parent / "audio.npy", mmap_mode="r")
    duration = len(audio) / 16000
    started = time.monotonic()
    # Keep one CPU thread even after live changes; only scheduling changes,
    # no need to reload a model midway through a transcript.
    model = WhisperModel(str(project_env.MODELS / job["model"]), device="cpu", compute_type="int8", cpu_threads=1, num_workers=1, local_files_only=True)
    segments, info = model.transcribe(
        audio, language=None if job["language"] in {"auto", "mixed"} else job["language"],
        multilingual=job["language"] == "mixed", beam_size=5, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=(("以下是普通话课程的简体中文转写。" if job["language"] == "zh" else "") + job.get("vocabulary", "")) or None,
        condition_on_previous_text=False)
    emit(status="transcribing", stage="primary", progress=0, duration=duration, detected_language=info.language, message="正在本机识别讲课内容…")
    count = 0
    for segment in segments:
        text = segment.text.strip()
        start = max(0, min(duration, segment.start))
        end = max(start, min(duration, segment.end))
        if not text or end <= start:
            continue
        count += 1
        emit(segment={"start": round(start, 3), "end": round(end, 3), "text": text}, progress=round(min(99, end / duration * 100), 1),
             message=f"正在转写 · 已识别 {count} 个片段")
    emit(engine_metric={"stage": "primary", "model": job["model"], "gpu_peak_gb": 0,
                        "rss_peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3, 2),
                        "seconds": round(time.monotonic() - started, 1), "scheduling": {"speed_profile": read_profile()}})
    emit(status="completed", progress=100, throttle_state="idle", cooldown_remaining=0,
         message="转写完成，可以校对并导出。" if count else "没有检测到语音，请检查音轨或更换语言设置。")


def run_chunks(job_path,emit,stage,name):
    import numpy as np
    import project_env
    from faster_whisper import WhisperModel
    from pipeline import trim_overlap
    job=json.loads(job_path.read_text())
    chunks=json.loads((job_path.parent/'chunks.json').read_text())
    audio=np.load(job_path.parent/'audio.npy',mmap_mode='r')
    model=WhisperModel(str(project_env.MODELS/name),device='cpu',compute_type='int8',cpu_threads=1,num_workers=1,local_files_only=True)
    previous='';previous_end=0
    for index,chunk in enumerate(chunks):
        pieces,info=model.transcribe(np.array(audio[chunk['start_sample']:chunk['end_sample']],copy=True),language=None if job['language'] in {'mixed','auto'} else job['language'],
            multilingual=job['language']=='mixed',beam_size=5,vad_filter=True,vad_parameters={'min_silence_duration_ms':500},initial_prompt=job.get('vocabulary') or None,condition_on_previous_text=False)
        raw=' '.join(piece.text.strip() for piece in pieces).strip()
        text=trim_overlap(previous,raw) if previous_end>chunk['start'] else raw
        previous,previous_end=raw,chunk['end']
        payload={'id':chunk['id'],'start':chunk['start'],'end':chunk['end'],'text':text,'raw_text':raw,'engine':name}
        if stage=='review':emit(review={**payload,'segments':[]})
        else:payload['review_state']='pending' if job.get('review_enabled') else 'not_requested';emit(segment=payload)
        emit(status='transcribing',stage=stage,detected_language=info.language,progress=round((60 if stage=='review' else 20)+(index+1)/max(1,len(chunks))*(39 if stage=='review' or job.get('review_enabled') else 79),1),message=f"{name} · {index+1}/{len(chunks)} 段")
