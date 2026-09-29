"""Ask Claude to find complete takes, or to dress up takes you picked yourself.

Either way each clip comes back with an on-screen title, a hook line to open
on, the teams it's about, and a post caption.
"""

import json
from pathlib import Path

import anthropic

from .transcribe import all_words

MODEL = "claude-opus-5-5"
TEAMS = json.loads((Path(__file__).parent / "teams.json").read_text())

CLIP_FIELDS = {
    "start": {"type": "number", "description": "Start time in seconds"},
    "end": {"type": "number", "description": "End time in seconds"},
    "title": {"type": "string", "description": "On-screen title, max 8 words"},
    "hook_start": {"type": "number", "description": "Start of the single punchiest line inside the clip, in seconds"},
    "hook_end": {"type": "number", "description": "End of that line; 2 to 6 seconds after hook_start. Equal to hook_start if no line works as a hook."},
    "teams": {
        "type": "array",
        "description": "Teams the clip is about, each with the time it is first named. Use names exactly as in the team list.",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "at": {"type": "number"}},
            "required": ["name", "at"],
            "additionalProperties": False,
        },
    },
    "games": {
        "type": "array",
        "description": "Specific games already played whose result the clip talks about (\"the Giants beat Dallas\", "
                       "\"that Ohio State loss\"), with the time each is first mentioned. opponent is empty if it isn't "
                       "said. Leave out upcoming games and general talk about a team.",
        "items": {
            "type": "object",
            "properties": {"team": {"type": "string"}, "opponent": {"type": "string"}, "at": {"type": "number"}},
            "required": ["team", "opponent", "at"],
            "additionalProperties": False,
        },
    },
    "players": {
        "type": "array",
        "description": "Up to 3 players named in the clip: full name, their team, and the time first named.",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "team": {"type": "string"}, "at": {"type": "number"}},
            "required": ["name", "team", "at"],
            "additionalProperties": False,
        },
    },
    "why": {"type": "string", "description": "One sentence on why this clip works"},
    "caption": {"type": "string", "description": "Post caption, 1-2 sentences, ends with a question that invites comments"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "score": {"type": "integer", "description": "1-10, how confident you are this clip will perform"},
}
CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": CLIP_FIELDS,
                "required": list(CLIP_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

HOOK_AND_TEAMS = (
    "For each clip also choose the hook: the single most attention-grabbing line inside it, 2 to 6 "
    "seconds long, such as a bold claim or a funny reaction. The video opens on that line before playing "
    "the whole clip, so it must make sense on its own. List the teams the clip is about with the time "
    "each is first named, including teams implied by a player's name. Flag the specific finished games whose "
    "result comes up, and the players named, so a score card or player card can pop up at that moment. "
    "Use only team names from this list:\n"
    + "; ".join(t["name"] for t in TEAMS)
)


def format_transcript(transcript: dict, sections: list[tuple[float, str]] = ()) -> str:
    lines, upcoming = [], list(sections)
    for seg in transcript["segments"]:
        while upcoming and upcoming[0][0] <= seg["start"] + 0.5:
            lines.append(f"=== SECTION: {upcoming.pop(0)[1]} ===")
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] " + (f"{seg['speaker']}: " if seg.get("speaker") else "") + seg["text"])
    return "\n".join(lines)


def section_at(sections: list[tuple[float, str]], t: float) -> str:
    return next((name for start, name in reversed(sections) if start <= t + 0.5), "")


def pick_clips(transcript: dict, guide: str, out: Path, count: int = 12, sections: list[tuple[float, str]] = ()) -> list[dict]:
    if out.exists():
        print(f"Using cached clip picks: {out}")
        return json.loads(out.read_text())

    print(f"Asking Claude for about {count} clip candidates...")
    prompt = (
        f"{guide}\n\n---\n\n"
        "Below is the full transcript of one episode. Each line is a transcript segment with its start and "
        "end time in seconds. "
        + ("Lines are labeled with who was talking, detected from each host's mic, so treat the labels as "
           "a strong hint rather than certain.\n\n" if any(s.get("speaker") for s in transcript["segments"])
           else "Speaker names are not labeled, so infer turns from context.\n\n")
        + 
        f"Find the {count} best clips in the episode, following the guide above. Start each clip at the first "
        "word of the take and end it at the last word. Clips must not overlap. Order them best first.\n\n"
        + ("The episode is split into the sections marked in the transcript. Spread the clips across every "
           "section so each one gets its best moments, rather than taking them all from the longest.\n\n"
           if sections else "")
        + f"{HOOK_AND_TEAMS}\n\n<transcript>\n{format_transcript(transcript, sections)}\n</transcript>"
    )
    clips = _ask(prompt)

    words = all_words(transcript)
    picked = []
    for clip in clips:
        if words:
            clip["start"], clip["end"] = snap_to_words(words, clip["start"], clip["end"])
        else:
            clip["start"], clip["end"] = snap_to_segments(transcript["segments"], clip["start"], clip["end"])
        clip["end"] = min(clip["end"], transcript["duration"])
        if clip["end"] - clip["start"] >= 5:
            clip["section"] = section_at(sections, clip["start"])
            picked.append(tidy(clip))

    out.write_text(json.dumps(picked, indent=2))
    print(f"Got {len(picked)} candidates.")
    return picked


def dress_clips(clips: list[dict], guide: str) -> list[dict]:
    """Write titles, hooks, teams and captions for clips the hosts chose themselves."""
    print(f"Asking Claude to write titles and hooks for your {len(clips)} clips...")
    listing = "\n\n".join(
        f"<clip index=\"{i}\" start=\"{c['start']:.1f}\" end=\"{c['end']:.1f}\">\n"
        + " ".join(f"[{w['start']:.1f}] {w['word']}" for w in c["words"])
        + "\n</clip>"
        for i, c in enumerate(clips)
    )
    prompt = (
        f"{guide}\n\n---\n\n"
        "The hosts already chose these clips. Each is shown with word times in seconds. Return one entry per "
        "clip, in the same order, keeping each clip's start and end exactly as given. Write the title, caption "
        "and hashtags following the guide above.\n\n"
        f"{HOOK_AND_TEAMS}\n\n{listing}"
    )
    answers = _ask(prompt)
    dressed = []
    for clip, ans in zip(clips, answers):
        ans.update(start=clip["start"], end=clip["end"])
        dressed.append(tidy(ans))
    return dressed


def tidy(clip: dict) -> dict:
    """Drop hooks and teams that fall outside the clip or aren't in the team list."""
    known = {t["name"].lower(): t["name"] for t in TEAMS}
    clip["teams"] = [
        {"name": known[t["name"].lower()], "at": t["at"]}
        for t in clip.get("teams", [])
        if t["name"].lower() in known and clip["start"] - 1 <= t["at"] <= clip["end"]
    ]
    inside = lambda x: clip["start"] - 1 <= x.get("at", -1) <= clip["end"]
    clip["games"] = [
        {"team": known[g["team"].lower()], "opponent": known.get(g.get("opponent", "").lower(), ""), "at": g["at"]}
        for g in clip.get("games", []) if g["team"].lower() in known and inside(g)
    ]
    clip["players"] = [
        {"name": p["name"].strip(), "team": known.get(p.get("team", "").lower(), ""), "at": p["at"]}
        for p in clip.get("players", []) if p.get("name", "").strip() and inside(p)
    ][:3]
    hs, he = clip.get("hook_start", 0), clip.get("hook_end", 0)
    clip["use_hook"] = clip["start"] <= hs < he <= clip["end"] and 1.5 <= he - hs <= 8 and hs - clip["start"] > 2
    return clip


def _ask(prompt: str) -> list[dict]:
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": CLIP_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise SystemExit("Claude declined this request. Try re-running.")
    if response.stop_reason == "max_tokens":
        raise SystemExit("Claude's answer was cut off. Try again with fewer clips (--count).")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["clips"]


def snap_to_words(words: list[dict], start: float, end: float) -> tuple[float, float]:
    """Move start back to the first word it cuts into, and end forward to the last."""
    first = next((w for w in words if w["end"] > start), None)
    last = next((w for w in reversed(words) if w["start"] < end), None)
    if first is None or last is None:
        return start, end
    # A small lead-in and tail keep the first and last word from sounding clipped.
    return round(max(first["start"] - 0.15, 0), 2), round(last["end"] + 0.35, 2)


def snap_to_segments(segments: list[dict], start: float, end: float) -> tuple[float, float]:
    first = next((s for s in segments if s["end"] > start), None)
    last = next((s for s in reversed(segments) if s["start"] < end), None)
    if first is None or last is None:
        return start, end
    return round(max(first["start"] - 0.15, 0), 2), round(last["end"] + 0.35, 2)
