"""Transcripts: import one you already have, or make one with Whisper.

For picking clips, segment-level times are enough, so an exported
transcript (SRT or VTT) from your recording or editing app works and skips
the slow step. Word-level times are only needed for captions, and only for
the clips you keep, so those few minutes of audio are transcribed on demand.
"""

import json
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


def load_transcript_file(path: Path, duration: float) -> dict:
    """Read an .srt, .vtt or timestamped .txt file into segments (no word times)."""
    text = path.read_text(encoding="utf-8-sig")
    if "-->" not in text:
        return _load_timestamped_text(text, path, duration)
    stamp = r"(\d+:)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
    pattern = re.compile(rf"({stamp})\s*-->\s*({stamp})[^\n]*\n(.*?)(?:\n\s*\n|\Z)", re.S)
    segments = []
    for m in pattern.finditer(text.replace("\r\n", "\n")):
        body = re.sub(r"<[^>]+>", "", m.group(11)).strip()
        body = " ".join(line.strip() for line in body.splitlines() if line.strip())
        if body:
            segments.append({"start": _secs(m.group(1)), "end": _secs(m.group(6)), "text": body, "words": []})
    if not segments:
        raise SystemExit(f"Couldn't read any timed lines from {path.name}. Export it as .srt or .vtt.")
    return {"duration": duration, "segments": segments}


SPOKEN_TIME = re.compile(r"^(?=\d+ (?:hour|minute|second))(?:\d+ hours?,? ?)?(?:\d+ minutes?,? ?)?(?:\d+ seconds?)?")


def _load_timestamped_text(text: str, path: Path, duration: float) -> dict:
    """YouTube's transcript as text: a time like 1:02:03 or 4:05 on its own line or
    starting a line, then what was said until the next time."""
    stamp = re.compile(r"^\s*\[?((?:\d+:)?\d{1,2}:\d{2})\]?\s*(.*)$")
    marks = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = stamp.match(line)
        if m:
            # Copied from YouTube's transcript panel, each time is followed by its
            # spoken form with no space ("0:088 seconds", "1:02:031 hour, 2 minutes, 3 seconds").
            said = SPOKEN_TIME.sub("", m.group(2), count=1).strip()
            marks.append([to_secs(m.group(1)), said])
        elif line.strip() and marks:
            marks[-1][1] = (marks[-1][1] + " " + line.strip()).strip()
    if not marks:
        raise SystemExit(f"{path.name} has no timestamps, so there's no way to line it up with the video. "
                         "Run without --transcript and the tool will make its own.")
    segments = []
    for i, (t, said) in enumerate(marks):
        end = min(marks[i + 1][0], t + 30) if i + 1 < len(marks) else min(t + 5, duration)
        if said and end > t:
            segments.append({"start": t, "end": end, "text": said, "words": []})
    return {"duration": duration, "segments": segments}


def to_secs(stamp: str) -> float:
    total = 0.0
    for part in stamp.split(":"):
        total = total * 60 + float(part)
    return total


def transcribe(video: Path, out: Path, model_size: str = "small") -> dict:
    """Transcribe the whole episode. Slow on a laptop, so prefer --transcript."""
    if out.exists():
        print(f"Using cached transcript: {out}")
        return json.loads(out.read_text())

    print(f"Transcribing all of {video.name} with Whisper. This is the slow step; --transcript skips it.")
    result = {"duration": media_duration(video), "segments": _whisper_segments(video, model_size, 0.0)}
    out.write_text(json.dumps(result))
    return result


def words_for_range(video: Path, start: float, end: float, model_size: str = "small") -> list[dict]:
    """Word-level times for one clip, transcribing only that stretch of audio."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "clip.wav"
        from .render import ffmpeg_exe

        subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}",
             "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
        segments = _whisper_segments(wav, model_size, start)
    return [w for seg in segments for w in seg["words"]]


def all_words(transcript: dict) -> list[dict]:
    return [w for seg in transcript["segments"] for w in seg.get("words", [])]


def has_word_times(transcript: dict) -> bool:
    return any(seg.get("words") for seg in transcript["segments"])


def media_duration(video: Path) -> float:
    from .render import ffmpeg_exe

    probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe)
    if not m:
        raise SystemExit(f"Couldn't read the length of {video.name}. Is it a video file?")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def video_size(video: Path) -> tuple[int, int]:
    from .render import ffmpeg_exe

    probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
    m = re.search(r"Video: .*?(\d{3,5})x(\d{3,5})", probe)
    if not m:
        raise SystemExit(f"Couldn't read the frame size of {video.name}.")
    return int(m.group(1)), int(m.group(2))


@lru_cache(maxsize=2)
def _model(model_size: str, device: str = "auto"):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type="auto" if device == "auto" else "int8")


_DEVICE = ["auto"]


def _whisper_segments(media: Path, model_size: str, offset: float) -> list[dict]:
    try:
        segments = _run_whisper(media, model_size, _DEVICE[0])
    except (RuntimeError, OSError) as e:
        # An NVIDIA card without NVIDIA's CUDA libraries installed fails here; the processor still works.
        if _DEVICE[0] == "cpu" or not re.search(r"cuda|cublas|cudnn|\.dll|\.so", str(e), re.I):
            raise
        print("Note: the graphics card can't run Whisper here, so captions are timed on the processor (a bit slower).")
        _DEVICE[0] = "cpu"
        segments = _run_whisper(media, model_size, "cpu")
    out = []
    for seg in segments:
        words = [
            {"start": round(w.start + offset, 2), "end": round(w.end + offset, 2), "word": w.word.strip()}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        if words:
            out.append({"start": round(seg.start + offset, 2), "end": round(seg.end + offset, 2), "text": seg.text.strip(), "words": words})
    return out


def _run_whisper(media: Path, model_size: str, device: str) -> list:
    segments, _ = _model(model_size, device).transcribe(str(media), language="en", word_timestamps=True, vad_filter=True)
    return list(segments)


def _secs(stamp: str) -> float:
    parts = re.split(r"[:.,]", stamp)
    frac = parts[-1]
    nums = [int(p) for p in parts[:-1]]
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, s = nums
    return h * 3600 + m * 60 + s + int(frac) / (10 ** len(frac))
