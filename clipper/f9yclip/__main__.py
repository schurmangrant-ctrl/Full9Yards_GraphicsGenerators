"""Run the whole pipeline.

    python -m f9yclip episode.mp4 --transcript episode.srt   # Claude finds the clips
    python -m f9yclip episode.mp4 --ranges picks.txt         # you name the clips
"""

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

from .pick import TEAMS, dress_clips, pick_clips
from .render import render_all
from .review import review
from .speakers import MicTracksMissing, boxes, label_segments, mic_levels, shots, vertical_crops
from .transcribe import all_words, has_word_times, load_transcript_file, media_duration, transcribe, video_size, words_for_range

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(prog="f9yclip", description="Turn a podcast episode into vertical clips.")
    parser.add_argument("video", type=Path, help="The full episode video file")
    parser.add_argument("--transcript", type=Path, help="An .srt or .vtt transcript of the episode. Skips the slow full transcription.")
    parser.add_argument("--ranges", type=Path, help="A text file of clips you picked, one per line: 12:30-13:45 optional title")
    parser.add_argument("--hosts", help="Who's on this episode, left to right on screen, e.g. Grant,Noah (default: everyone in settings.json)")
    parser.add_argument("--count", type=int, default=12, help="How many candidates to ask for (default 12)")
    parser.add_argument("--no-review", action="store_true", help="Skip the review page. Renders your ranges, or every candidate Claude scored 7+.")
    parser.add_argument("--repick", action="store_true", help="Ask Claude for fresh candidates instead of reusing the last picks")
    parser.add_argument("--first-post", type=date.fromisoformat, default=date.today() + timedelta(days=1),
                        help="Date of the first post, YYYY-MM-DD (default tomorrow). Clips are spread one per day from here.")
    parser.add_argument("--per-day", type=int, default=1, help="Clips to post per day (default 1)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    video = args.video.resolve()
    if not video.exists():
        raise SystemExit(f"Can't find {video}")

    settings = json.loads((HERE / "settings.json").read_text())
    guide = (HERE / "clip_guide.md").read_text()
    whisper = settings.get("whisper_model", "small")
    work = video.parent / f"{video.stem}_clips"
    work.mkdir(exist_ok=True)

    # With each host's mic on its own track, the camera follows whoever is talking.
    switching = settings.get("speaker_switching")
    mics = crop = None
    if switching and switching.get("mic_tracks"):
        try:
            on_air = [h.strip() for h in args.hosts.split(",")] if args.hosts else list(switching["mic_tracks"])
            unknown = [h for h in on_air if h not in switching["mic_tracks"]]
            if unknown:
                raise SystemExit(f"No mic track set for {', '.join(unknown)} in settings.json.")
            mics = mic_levels(video, {h: switching["mic_tracks"][h] for h in on_air}, work / "mic_levels.npz")
            crop = vertical_crops(boxes(settings, mics[0], *video_size(video)))
        except MicTracksMissing as e:
            print(f"Note: {e}")

    if args.ranges:
        clips = read_ranges(args.ranges)
        for c in clips:
            print(f"Transcribing your clip at {c['start']:.0f}s for captions...")
            c["words"] = words_for_range(video, c["start"], c["end"], whisper)
        titles = {id(c): c["title"] for c in clips}
        dressed = dress_clips(clips, guide)
        for mine, d in zip(clips, dressed):
            d["words"] = mine["words"]
            if titles[id(mine)]:
                d["title"] = titles[id(mine)]
            d["score"] = None
            d["keep"] = True
        candidates = dressed
        segments = [{"start": w["start"], "end": w["end"], "text": w["word"]} for c in candidates for w in c["words"]]
        transcript = {"duration": media_duration(video), "segments": segments}
    else:
        if args.transcript:
            transcript = load_transcript_file(args.transcript, media_duration(video))
        else:
            transcript = transcribe(video, work / "transcript.json", whisper)
        picks_file = work / "candidates.json"
        if args.repick and picks_file.exists():
            picks_file.unlink()
        if mics:
            label_segments(transcript, *mics)
        candidates = pick_clips(transcript, guide, picks_file, count=args.count)

    for c in candidates:
        c.setdefault("layout", "speaker" if crop else "blur")

    if args.no_review:
        chosen = [c for c in candidates if c.get("keep") or (c.get("score") or 0) >= 7]
    else:
        chosen = review(video, transcript, candidates, settings, port=args.port)

    known = {t["name"].lower(): t["name"] for t in TEAMS}
    for c in chosen:
        c["teams"] = [dict(t, name=known[t["name"].lower()]) for t in c.get("teams", []) if t["name"].lower() in known]

    # Word times for captions: reuse the transcript's if it has them, otherwise
    # transcribe just the kept clips (a few minutes of audio, not the episode).
    for c in chosen:
        lo = min(c["start"], c["hook_start"]) if c.get("use_hook") else c["start"]
        if has_word_times(transcript):
            c["words"] = [w for w in all_words(transcript) if lo - 1 <= w["start"] <= c["end"] + 1]
        elif not covers(c.get("words"), lo, c["end"]):
            print(f"Timing captions for {c['title']!r}...")
            c["words"] = words_for_range(video, max(lo - 0.5, 0), c["end"] + 0.5, whisper)
        if c.get("layout") == "speaker" and mics:
            c["shots"] = shots(*mics, c["start"], c["end"])
            if c.get("use_hook"):
                c["hook_shots"] = shots(*mics, c["hook_start"], c["hook_end"])
    (work / "approved.json").write_text(json.dumps(chosen, indent=2))

    per_day = max(args.per_day, 1)
    post_dates = [(args.first_post + timedelta(days=i // per_day)).strftime("%Y-%m-%d-%a") for i in range(len(chosen))]
    outputs = render_all(video, chosen, work, settings, post_dates, crop)
    print(f"\nDone. {len(outputs)} clips and captions.md are in {work}")


def covers(words, start: float, end: float) -> bool:
    return bool(words) and words[0]["start"] <= start + 1 and words[-1]["end"] >= end - 1


def read_ranges(path: Path) -> list[dict]:
    """Lines like "12:30-13:45" or "1:02:10 - 1:03:00 Giants QB take"."""
    stamp = r"(\d+(?::\d{1,2}){0,2}(?:\.\d+)?)"
    clips = []
    for line in path.read_text().splitlines():
        m = re.match(rf"\s*{stamp}\s*-\s*{stamp}\s*(.*)$", line)
        if not m:
            continue
        start, end = to_seconds(m.group(1)), to_seconds(m.group(2))
        if end > start:
            clips.append({"start": start, "end": end, "title": m.group(3).strip()})
    if not clips:
        raise SystemExit(f"No time ranges found in {path.name}. Write one per line, like 12:30-13:45.")
    return clips


def to_seconds(stamp: str) -> float:
    total = 0.0
    for part in stamp.split(":"):
        total = total * 60 + float(part)
    return total


if __name__ == "__main__":
    main()
