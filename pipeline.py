"""Chunk boundaries, transcript comparison and provenance; no model imports."""
import difflib
import re
import unicodedata


def normalize(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def english_words(text):
    return re.findall(r"[a-z]+(?:['’][a-z]+)?", text.lower())


def comparison(primary, reference):
    a, b = normalize(primary), normalize(reference)
    similarity = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    # A small English difference can matter much more than a high overall score
    # in a Chinese grammar lesson (is/was, a/an, singular/plural, negation).
    english_difference = english_words(primary) != english_words(reference)
    uncertain = similarity < 0.9 or english_difference or not a or not b
    return {"similarity": round(similarity, 3), "review_state": "needs_review" if uncertain else "consistent", "english_difference": english_difference}


def chunk_ranges(samples, speech, rate=16000, max_seconds=28, overlap_seconds=0.8):
    """Cut near speech boundaries, retain padding, never exceed max_seconds."""
    maximum = int(max_seconds * rate)
    overlap = int(overlap_seconds * rate)
    padded = []
    for span in speech:
        start, end = max(0, span["start"] - int(.25 * rate)), min(samples, span["end"] + int(.25 * rate))
        if padded and start - padded[-1][1] <= rate:
            padded[-1][1] = max(padded[-1][1], end)
        elif end > start:
            padded.append([start, end])
    chunks = []
    for start, end in padded:
        while start < end:
            stop = min(start + maximum, end)
            # Prefer an original pause near the end rather than cutting a word.
            if stop < end:
                candidates = [s["end"] + int(.2 * rate) for s in speech if stop - 3 * rate < s["end"] < stop - overlap]
                if candidates:
                    stop = min(end, max(candidates))
                if 0 < end - stop < 3 * rate:
                    stop = start + (end - start) // 2
            chunks.append({"id": len(chunks), "start_sample": start, "end_sample": stop,
                           "start": round(start / rate, 3), "end": round(stop / rate, 3)})
            if stop == end:
                break
            start = stop - overlap
    return chunks


def trim_overlap(previous, current):
    """Conservative exact overlap trim; keep raw_text for lossless inspection."""
    for size in range(min(80, len(previous), len(current)), 5, -1):
        if previous[-size:] == current[:size]:
            prefix = current[:size]
            if len(english_words(prefix)) >= 3 or len(re.findall(r"[\u4e00-\u9fff]", prefix)) >= 6:
                return current[size:].lstrip(" ,，。；;\n")
    return current


def segment_text(segment):
    return segment.get("resolved_text", segment.get("text", ""))


def review_language(job,segment):
    declared=job.get('language','auto')
    if declared in {'zh','en','mixed'}:return declared
    if declared!='auto':return 'other'
    detected=job.get('detected_language')
    if detected and detected not in {'zh','en'}:return 'other'
    texts=segment.get('text','')+' '+segment.get('review_text','')
    chinese=bool(re.search(r'[\u4e00-\u9fff]',texts))
    latin=bool(re.search(r'[A-Za-z]',texts))
    return 'mixed' if chinese and latin else 'zh' if chinese else 'en' if latin else 'other'


def apply_review_policy(job,segment):
    """Select a complete source, preserve both originals; never splice languages."""
    if job.get('review_policy','manual')!='by_language' or 'review_text' not in segment:return
    if segment.get('resolution') in {'primary','review','confirm'}:return
    if job.get('edited_text') is not None:return
    language=review_language(job,segment)
    choice=job.get('trust',{}).get(language,'primary')
    selected=segment['review_text'] if choice=='review' else segment['text']
    # Silence/refusal/empty review must never erase an audible primary transcript.
    if not selected.strip():
        segment['review_state']='needs_review';segment['auto_selection_warning']='默认来源为空，请人工核对。';return
    segment['comparison_state']=segment['review_state']
    segment.update(resolved_text=selected,review_state='auto_selected',selected_source=choice,selected_language=language)
