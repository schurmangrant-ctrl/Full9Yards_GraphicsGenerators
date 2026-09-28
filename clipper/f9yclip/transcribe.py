"""Speech-to-text with word-level timestamps, run locally with faster-whisper.

The result is cached next to the episode as <work>/transcript.json so a
re-run skips straight to picking clips.
"""

import json
from pathlib import Path


def transcribe(video: Path, out: Path, model_size: str = "small") -> dict:
    if out.exists():
        print(f"Using cached transcript: {out}")
        return json.loads(out.read_text())

    from faster_whisper import WhisperModel

    print(f"Transcribing {video.name} with whisper '{model_size}' (this is the slow step)...")
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, info = model.transcribe(
        str(video),
        language="en",
        word_timestamps=True,
        vad_filter=True,
    )

    result = {"duration": info.duration, "segments": []}
    for seg in segments:
        words = [
            {"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        if not words:
            continue
        result["segments"].append(
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "words": words,
            }
        )
        print(f"  {_ts(seg.end)} / {_ts(info.duration)}", end="\r", flush=True)
    print()

    out.write_text(json.dumps(result))
    return result


def all_words(transcript: dict) -> list[dict]:
    return [w for seg in transcript["segments"] for w in seg["words"]]


def text_between(transcript: dict, start: float, end: float) -> str:
    return " ".join(
        w["word"] for w in all_words(transcript) if w["start"] >= start - 0.05 and w["end"] <= end + 0.05
    )


def _ts(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
