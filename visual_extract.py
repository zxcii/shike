"""On-device Apple Vision OCR. No language correction of teaching examples."""
import project_env
import json
import re
from pathlib import Path


def recognize_image(path):
    import Vision
    from Foundation import NSURL
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["en-US", "zh-Hans"])
    request.setAutomaticallyDetectsLanguage_(True)
    request.setUsesLanguageCorrection_(False)
    # macOS 27 rejects an empty options dictionary here; nil means no options.
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(Path(path).resolve())), None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"macOS OCR 失败：{error}")
    lines = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)
        if candidate:
            box = observation.boundingBox()
            lines.append({"text": str(candidate[0].string()), "confidence": round(float(candidate[0].confidence()), 3),
                          "box": [round(float(v), 4) for v in (box.origin.x, box.origin.y, box.size.width, box.size.height)]})
    return lines


def clean_subtitle(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if text.startswith("Dialogue:"):
        text = text.split(",", 9)[-1]
    elif text.count(",") >= 8:
        text = text.split(",", 8)[-1]
    text = re.sub(r"\{[^}]*\}", "", text)
    return text.replace("\\N", "\n").replace("\\n", "\n").strip()


def embedded_subtitles(source, emit):
    import av
    count = 0
    with av.open(str(source), metadata_errors="ignore") as container:
        streams = list(container.streams.subtitles)
        if not streams:
            return 0
        for packet in container.demux(*streams):
            if packet.pts is None or not packet.size:
                continue
            try:
                decoded = packet.decode()
                texts = []
                for item in decoded:
                    rectangles = list(item) if hasattr(item, "__iter__") else [item]
                    for rectangle in rectangles:
                        value = getattr(rectangle, "text", None) or getattr(rectangle, "ass", None)
                        if value:
                            texts.append(clean_subtitle(value))
                text = "\n".join(t for t in texts if t)
                if text:
                    start = max(0, float(packet.pts * packet.time_base))
                    end = start + max(.1, float((packet.duration or 0) * packet.time_base))
                    emit(subtitle={"start": round(start, 3), "end": round(end, 3), "text": text, "source": "embedded", "track": packet.stream.index})
                    count += 1
            except (av.error.FFmpegError, TypeError):
                continue  # Bitmap subtitle tracks are covered by frame OCR.
    return count


def extract_visuals(source, directory, duration, emit, interval=1.0, governor=None, recognizer=None, source_label="vision-ocr"):
    import av
    import numpy as np
    import objc
    frames = directory / "frames"
    frames.mkdir(exist_ok=True)
    count = 0
    last_image = None
    last_text = None
    next_time = 0.0
    last_saved = -10.0
    with av.open(str(source), metadata_errors="ignore") as container:
        if not container.streams.video:
            return 0
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        from thermal_control import PROFILES, read_profile
        stream.codec_context.thread_count = PROFILES[read_profile()]["threads"]
        # Seek between samples instead of decoding every frame of a long video.
        for step in range(int(duration / interval) + 1):
            requested = step * interval
            if requested >= duration:
                break
            container.seek(int(requested / float(stream.time_base)), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.time is None or frame.time + .03 < requested:
                    continue
                moment = max(0, float(frame.time))
                image = frame.to_image()
                # Exclude the common top danmaku strip. Keep the full original
                # screenshot as evidence; OCR uses the lower 85% of the frame.
                crop = image.crop((0, int(image.height * .15), image.width, image.height))
                thumb = np.asarray(crop.convert("L").resize((160, 90)), dtype=np.float32)
                changed = last_image is None or float(np.abs(thumb - last_image).mean()) > .55
                last_image = thumb
                if not changed and moment - last_saved < 8:
                    break
                filename = f"frame-{count:05d}.jpg"
                temporary = directory / "ocr-input.jpg"
                crop.thumbnail((1920, 1080))
                crop.save(temporary, quality=92)
                with objc.autorelease_pool():
                    lines = recognizer(temporary) if recognizer else recognize_image(temporary)
                last_saved = moment
                text = "\n".join(line["text"] for line in lines)
                if not text.strip() or text == last_text:
                    break
                image.thumbnail((1280, 720))
                image.save(frames / filename, quality=85)
                emit(visual={"id": count, "start": round(moment, 3), "text": text, "lines": lines,
                             "image": filename, "source": source_label})
                last_saved, last_text = moment, text
                count += 1
                break
            emit(status="transcribing", stage="ocr", progress=round(min(19, requested / max(duration, 1) * 19), 1),
                 message=f"正在提取画面文字 · {requested:.0f}/{duration:.0f} 秒 · {count} 张文字画面")
            if governor:
                governor.checkpoint()
    (directory / "ocr-input.jpg").unlink(missing_ok=True)
    return count
