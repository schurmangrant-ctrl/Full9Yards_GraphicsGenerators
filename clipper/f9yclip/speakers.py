"""Who is talking, from each host's own mic track.

OBS can record every mic to its own audio track (see README). Comparing how
loud each mic is, moment to moment, says who has the floor. That drives
the camera switching and labels the transcript by speaker for Claude.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HOP = 0.1          # seconds per level reading
RATE = 8000        # plenty for loudness; keeps decoding fast
MIN_SHOT = 1.5     # never cut away sooner than this
HOLD = 0.6         # a new speaker must lead this long before we cut to them
LEAD_DB = 4.0      # how far ahead of the others a mic must be to count as talking


class MicTracksMissing(Exception):
    pass


def mic_levels(video: Path, tracks: dict[str, int], cache: Path | None = None) -> tuple[list[str], np.ndarray]:
    """Loudness in dB for each host, one row per HOP, for the whole episode.

    tracks maps host name -> OBS track number (1-based, as OBS shows them).
    Each host's levels are measured against their own loud-speech level, so a
    hot mic and a quiet mic compare fairly.
    """
    names = list(tracks)
    if cache and cache.exists():
        data = np.load(cache)
        if list(data["names"]) == names:
            return names, data["levels"]

    from .render import ffmpeg_exe

    def read(track: int) -> np.ndarray:
        raw = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(video),
             "-map", f"0:a:{track - 1}", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
            capture_output=True, check=True,
        ).stdout
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
        hop = int(RATE * HOP)
        frames = samples[: len(samples) // hop * hop].reshape(-1, hop)
        return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)

    print("Reading each host's mic to see who's talking...")
    try:
        with ThreadPoolExecutor(len(names)) as pool:
            rows = list(pool.map(read, tracks.values()))
    except subprocess.CalledProcessError:
        raise MicTracksMissing(
            "Couldn't read a separate track for each mic, so clips will use the whole shot instead of "
            "following the speaker. Check mic_tracks in settings.json matches the OBS tracks each mic is "
            "recorded to, and that the file kept all its audio tracks."
        )
    n = min(len(r) for r in rows)
    levels = np.stack([r[:n] for r in rows])
    for row in levels:
        loud = np.percentile(row, 95)
        row -= loud
    if cache:
        np.savez(cache, names=np.array(names), levels=levels)
    return names, levels


def talking(levels: np.ndarray) -> np.ndarray:
    """Index of the host clearly louder than everyone else at each reading, or -1."""
    order = np.sort(levels, axis=0)
    top, second = order[-1], order[-2]
    who = levels.argmax(axis=0)
    clear = (top - second >= LEAD_DB) & (top > -30)
    return np.where(clear, who, -1)


def shots(names: list[str], levels: np.ndarray, start: float, end: float) -> list[tuple[float, float, str]]:
    """Camera shots for one stretch: [(from, to, host)] in seconds from `start`."""
    a, b = int(start / HOP), max(int(end / HOP), int(start / HOP) + 1)
    who = talking(levels[:, a:b])
    known = who[who >= 0]
    if len(known) == 0:
        return [(0.0, end - start, names[0])]

    current = int(np.bincount(known[: int(3 / HOP)] if len(known[: int(3 / HOP)]) else known).argmax())
    cuts = [(0, current)]
    streak_who, streak = -1, 0
    for i, w in enumerate(who):
        if w < 0 or w == current:
            streak_who, streak = -1, 0
            continue
        streak = streak + 1 if w == streak_who else 1
        streak_who = w
        cut_at = i - streak + 1
        if streak * HOP >= HOLD and (cut_at - cuts[-1][0]) * HOP >= MIN_SHOT:
            current = int(w)
            cuts.append((cut_at, current))
            streak_who, streak = -1, 0

    out = []
    for n, (i, host) in enumerate(cuts):
        t0 = i * HOP
        t1 = cuts[n + 1][0] * HOP if n + 1 < len(cuts) else end - start
        out.append((round(t0, 2), round(t1, 2), names[host]))
    return out


def label_segments(transcript: dict, names: list[str], levels: np.ndarray) -> None:
    """Tag each transcript segment with whoever talked most during it."""
    who = talking(levels)
    for seg in transcript["segments"]:
        span = who[int(seg["start"] / HOP): int(seg["end"] / HOP) + 1]
        span = span[span >= 0]
        if len(span):
            seg["speaker"] = names[int(np.bincount(span, minlength=len(names)).argmax())]


def boxes(settings: dict, names: list[str], frame_w: int, frame_h: int) -> dict[str, tuple[int, int, int, int]]:
    """Each host's camera box (x, y, w, h) in the recorded frame, left to right in `names` order.

    settings["speaker_switching"]["boxes"] is either "equal" (cameras side by side,
    full height) or each host's fixed seat: {"Grant": [x, y, w, h], ...}. An
    optional "layouts" entry per host count ("2", "3") overrides it with a list
    of boxes, left to right.
    """
    sw = settings.get("speaker_switching", {})
    spec = sw.get("layouts", {}).get(str(len(names)), sw.get("boxes", "equal"))
    if isinstance(spec, str):
        w = frame_w // len(names)
        return {name: (i * w, 0, w, frame_h) for i, name in enumerate(names)}
    if isinstance(spec, dict):
        return {name: tuple(spec[name]) for name in names}
    if len(spec) != len(names):
        raise SystemExit(f"The {len(names)}-host layout in settings.json lists {len(spec)} boxes.")
    return {name: tuple(box) for name, box in zip(names, spec)}


def vertical_crops(box_map: dict, aspect: float = 9 / 16) -> tuple[int, int, dict[str, tuple[int, int]]]:
    """One 9:16 crop size that fits every box, and where it sits in each box."""
    cw = min(min(w, int(h * aspect)) for _, _, w, h in box_map.values())
    ch = int(cw / aspect)
    cw, ch = cw // 2 * 2, ch // 2 * 2
    spots = {name: (x + (w - cw) // 2, y + max((h - ch) // 2, 0)) for name, (x, y, w, h) in box_map.items()}
    return cw, ch, spots
