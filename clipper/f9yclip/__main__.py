"""Run the whole pipeline.

    python -m f9yclip episode.mp4 --transcript episode.srt   # Claude finds the clips
    python -m f9yclip episode.mp4 --ranges picks.txt         # you name the clips
"""

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

from .cards import attach_cards
from .pick import TEAMS, dress_clips, pick_clips, place_written
from .render import render_all
from .review import review
from .speakers import HOP, MicTracksMissing, boxes, label_segments, mic_levels, shots, talking, vertical_crops
from .transcribe import all_words, has_word_times, load_transcript_file, media_duration, transcribe, video_size, words_for_range

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(prog="f9yclip", description="Turn a podcast episode into vertical clips.")
    parser.add_argument("video", type=Path, help="The full episode video file")
    parser.add_argument("--transcript", type=Path, help="An .srt or .vtt transcript of the episode. Skips the slow full transcription.")
    parser.add_argument("--ranges", type=Path, help="A text file of clips you picked, one per line: 12:30-13:45 optional title")
    parser.add_argument("--sections", type=Path, help="A text file of where each part of the show starts, one per line: 0:00 CFB recap")
    parser.add_argument("--hosts", help="Who's on this episode, left to right on screen, e.g. Grant,Noah (default: everyone in settings.json)")
    parser.add_argument("--count", type=int, default=12, help="How many candidates to ask for (default 12)")
    parser.add_argument("--no-review", action="store_true", help="Skip the review page. Renders your ranges, or every candidate Claude scored 7+.")
    parser.add_argument("--repick", action="store_true", help="Ask Claude for fresh candidates instead of reusing the last picks")
    parser.add_argument("--first-post", type=date.fromisoformat, default=date.today() + timedelta(days=1),
                        help="Date of the first post, YYYY-MM-DD (default tomorrow). Clips are spread one per day from here.")
    parser.add_argument("--per-day", type=int, default=1, help="Clips to post per day (default 1)")
    parser.add_argument("--played", type=date.fromisoformat,
                        help="Date the episode was recorded, YYYY-MM-DD, for looking up recent scores (default: the file's date)")
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

    # Each host's camera window in the recording. With each host's mic on its own
    # track, the camera follows whoever is talking; without, each clip stays on
    # one host, picked on the review page (or with [Name] in a ranges file).
    switching = settings.get("speaker_switching") or {}
    tracks = switching.get("mic_tracks") or {}
    on_air = [h.strip() for h in args.hosts.split(",")] if args.hosts else list(tracks or settings.get("hosts", []))
    mics = crop = split_crop = cameras = None
    if tracks:
        unknown = [h for h in on_air if h not in tracks]
        if unknown:
            raise SystemExit(f"No mic track set for {', '.join(unknown)} in settings.json.")
        try:
            mics = mic_levels(video, {h: tracks[h] for h in on_air}, work / "mic_levels.npz")
        except MicTracksMissing as e:
            print(f"Note: {e}")
    frame_w, frame_h = video_size(video)
    # A grid of webcams is at least two 1280-wide cameras across; anything
    # narrower is a single shot, so there are no windows to crop.
    grid = switching.get("boxes", "grid") != "grid" or frame_w >= 2560
    if len(on_air) > 1 and grid:
        box_map = boxes(settings, on_air, frame_w, frame_h)
        framing_file = HERE / "framing.json"
        framing = json.loads(framing_file.read_text()) if framing_file.exists() else {}
        stills = still_times(*mics) if mics else {h: media_duration(video) / 4 for h in on_air}
        cameras = {"boxes": box_map, "crop_w": vertical_crops(box_map)[0], "framing": framing,
                   "stills": stills, "follow": bool(mics)}
        crop = vertical_crops(box_map, framing)
        split_crop = vertical_crops(box_map, framing, aspect=9 / 8)

    if args.ranges:
        clips = read_ranges(args.ranges)
        for c in clips:
            print(f"Transcribing your clip at {c['start']:.0f}s for captions...")
            c["words"] = words_for_range(video, c["start"], c["end"], whisper)
        titles = {id(c): c["title"] for c in clips}
        # A picks file that already has the hooks and captions written needs no API key.
        written = all(c.get("caption") for c in clips)
        dressed = [place_written(dict(c)) for c in clips] if written else dress_clips(clips, guide)
        for mine, d in zip(clips, dressed):
            d["words"] = mine["words"]
            if titles[id(mine)]:
                d["title"] = titles[id(mine)]
            if mine.get("layout"):
                d.setdefault("layout", mine["layout"])
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
        sections = read_sections(args.sections) if args.sections else []
        candidates = pick_clips(transcript, guide, picks_file, count=args.count, sections=sections)

    for c in candidates:
        c.setdefault("layout", "speaker" if mics else f"host:{on_air[0]}" if cameras else "blur")
    if any(c.get("games") or c.get("players") for c in candidates) and not all("cards" in c for c in candidates):
        print("Looking up scores and headshots...")
        played = args.played or date.fromtimestamp(video.stat().st_mtime)
        attach_cards(candidates, played, HERE / "cache")
        if not args.ranges:
            (work / "candidates.json").write_text(json.dumps(candidates, indent=2))

    if args.no_review:
        chosen = [c for c in candidates if c.get("keep") or (c.get("score") or 0) >= 7]
    else:
        chosen = review(video, transcript, candidates, settings, port=args.port, cameras=cameras, games=game_files(video))
        if cameras:
            (HERE / "framing.json").write_text(json.dumps(cameras["framing"], indent=2))
            crop = vertical_crops(cameras["boxes"], cameras["framing"])
            split_crop = vertical_crops(cameras["boxes"], cameras["framing"], aspect=9 / 8)

    known = {t["name"].lower(): t["name"] for t in TEAMS}
    for c in chosen:
        if c.get("game"):
            path = video.parent / GAME_FOLDER / c["game"]
            if path.exists():
                c["game_path"] = str(path.resolve())
            else:
                print(f"Note: can't find {path}, so {c['title']!r} renders without game footage.")
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
        if c.get("layout") == "cuts" and cameras and c.get("cuts"):
            # Cuts placed by hand: whoever the last cut before each moment names.
            c["layout"] = "speaker"
            c["shots"] = cut_shots(c["cuts"], c["start"], c["end"], cameras["boxes"])
            c["hook_shots"] = cut_shots(c["cuts"], c["hook_start"], c["hook_end"], cameras["boxes"])
        elif c.get("layout", "").startswith("host:") and cameras and c["layout"][5:] in cameras["boxes"]:
            # One host for the whole clip: the same crop, without following the mics.
            host = c["layout"][5:]
            c["layout"] = "speaker"
            c["shots"] = [(0.0, c["end"] - c["start"], host)]
            c["hook_shots"] = [(0.0, c["hook_end"] - c["hook_start"], host)]
        elif c.get("layout", "").startswith("host:"):
            c["layout"] = "blur"
        elif c.get("layout") == "speaker" and mics:
            c["shots"] = shots(*mics, c["start"], c["end"])
            if c.get("use_hook"):
                c["hook_shots"] = shots(*mics, c["hook_start"], c["hook_end"])
    (work / "approved.json").write_text(json.dumps(chosen, indent=2))

    per_day = max(args.per_day, 1)
    post_dates = [(args.first_post + timedelta(days=i // per_day)).strftime("%Y-%m-%d-%a") for i in range(len(chosen))]
    outputs = render_all(video, chosen, work, settings, post_dates, crop, split_crop)
    print(f"\nDone. {len(outputs)} clips and captions.md are in {work}")


GAME_FOLDER = "game_footage"
VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def game_files(video: Path) -> list[str]:
    """Downloaded game clips in the game_footage folder next to the episode."""
    folder = video.parent / GAME_FOLDER
    if not folder.is_dir():
        return []
    return sorted(f.name for f in folder.iterdir() if f.suffix.lower() in VIDEO_TYPES)


def still_times(names: list[str], levels) -> dict[str, float]:
    """A moment each host is talking, for the framing stills on the review page."""
    who = talking(levels)
    times = {}
    for i, name in enumerate(names):
        hits = (who == i).nonzero()[0]
        times[name] = float(hits[len(hits) // 2] * HOP) if len(hits) else levels.shape[1] * HOP / 2
    return times


def covers(words, start: float, end: float) -> bool:
    return bool(words) and words[0]["start"] <= start + 1 and words[-1]["end"] >= end - 1


def read_ranges(path: Path) -> list[dict]:
    """Lines like "12:30-13:45", "1:02:10 - 1:03:00 Giants QB take", or with the host
    to show when there are no separate mic tracks: "12:30-13:45 [Caden] Bama is back".
    A .json file holds clips already written up (see place_written)."""
    if path.suffix.lower() == ".json":
        clips = json.loads(path.read_text())
        for c in clips:
            c["start"], c["end"] = to_seconds(str(c["start"])), to_seconds(str(c["end"]))
            if c.get("host"):
                c["layout"] = f"host:{c.pop('host')}"
            c.setdefault("title", "")
        return clips
    stamp = r"(\d+(?::\d{1,2}){0,2}(?:\.\d+)?)"
    clips = []
    for line in path.read_text().splitlines():
        m = re.match(rf"\s*{stamp}\s*-\s*{stamp}\s*(.*)$", line)
        if not m:
            continue
        start, end = to_seconds(m.group(1)), to_seconds(m.group(2))
        if end > start:
            rest = m.group(3).strip()
            clip = {"start": start, "end": end}
            who = re.match(r"\[([^\]]+)\]\s*(.*)$", rest)
            if who:
                clip["layout"], rest = f"host:{who.group(1).strip()}", who.group(2)
            clip["title"] = rest.strip()
            clips.append(clip)
    if not clips:
        raise SystemExit(f"No time ranges found in {path.name}. Write one per line, like 12:30-13:45.")
    return clips


def cut_shots(cuts: list[dict], start: float, end: float, hosts) -> list[tuple]:
    """Shots between start and end, relative to start, from cuts like {"at": 1512.4, "host": "Caden"}."""
    cuts = sorted((c for c in cuts if c.get("host") in hosts), key=lambda c: c["at"])
    if not cuts or end <= start:
        return [(0.0, max(end - start, 0.0), next(iter(hosts)))]
    who = next((c["host"] for c in reversed(cuts) if c["at"] <= start), cuts[0]["host"])
    shots, t = [], start
    for c in cuts:
        if start < c["at"] < end and c["host"] != who:
            shots.append((round(t - start, 2), round(c["at"] - start, 2), who))
            t, who = c["at"], c["host"]
    shots.append((round(t - start, 2), round(end - start, 2), who))
    return shots


def read_sections(path: Path) -> list[tuple[float, str]]:
    """Lines like "0:00 CFB recap" or "47:30 NFL preview"."""
    sections = []
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(\d+(?::\d{1,2}){0,2})\s+(.+)$", line)
        if m:
            sections.append((to_seconds(m.group(1)), m.group(2).strip()))
    if not sections:
        raise SystemExit(f"No sections found in {path.name}. Write one per line, like 47:30 NFL preview.")
    return sorted(sections)


def to_seconds(stamp: str) -> float:
    total = 0.0
    for part in stamp.split(":"):
        total = total * 60 + float(part)
    return total


if __name__ == "__main__":
    main()
