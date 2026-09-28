"""Ask Claude to read the whole transcript and shortlist complete takes.

The picker returns rough times; they are then snapped to whole words so a
clip never starts or ends mid-word.
"""

import json
from pathlib import Path

import anthropic

from .transcribe import all_words

MODEL = "claude-opus-5-5"

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "Start time in seconds"},
                    "end": {"type": "number", "description": "End time in seconds"},
                    "title": {"type": "string", "description": "On-screen hook text, max 8 words"},
                    "why": {"type": "string", "description": "One sentence on why this clip works"},
                    "caption": {"type": "string", "description": "Post caption, 1-2 sentences, ends with a question that invites comments"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "score": {"type": "integer", "description": "1-10, how confident you are this clip will perform"},
                },
                "required": ["start", "end", "title", "why", "caption", "hashtags", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


def format_transcript(transcript: dict) -> str:
    return "\n".join(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}" for seg in transcript["segments"])


def pick_clips(transcript: dict, guide: str, out: Path, count: int = 12) -> list[dict]:
    if out.exists():
        print(f"Using cached clip picks: {out}")
        return json.loads(out.read_text())

    print(f"Asking Claude for about {count} clip candidates...")
    client = anthropic.Anthropic()
    prompt = (
        f"{guide}\n\n"
        "---\n\n"
        f"Below is the full transcript of one episode. Each line is a transcript segment with its "
        f"start and end time in seconds. Speaker names are not labeled, so infer turns from context.\n\n"
        f"Find the {count} best clips in the episode, following the guide above. Use the segment "
        f"times to set start and end, starting at the first word of the take and ending at the last "
        f"word of it. Clips must not overlap. Order them best first.\n\n"
        f"<transcript>\n{format_transcript(transcript)}\n</transcript>"
    )

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": CLIP_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise SystemExit("Claude declined to pick clips from this transcript. Try re-running.")
    if response.stop_reason == "max_tokens":
        raise SystemExit("Claude's answer was cut off. Try re-running with fewer clips (--count).")

    text = next(b.text for b in response.content if b.type == "text")
    clips = json.loads(text)["clips"]

    words = all_words(transcript)
    duration = transcript["duration"]
    picked = []
    for clip in clips:
        start, end = snap_to_words(words, clip["start"], clip["end"])
        if end - start < 5:
            continue
        clip["start"], clip["end"] = start, min(end, duration)
        picked.append(clip)

    out.write_text(json.dumps(picked, indent=2))
    print(f"Got {len(picked)} candidates.")
    return picked


def snap_to_words(words: list[dict], start: float, end: float) -> tuple[float, float]:
    """Move start back to the first word it cuts into, and end forward to the last."""
    first = next((w for w in words if w["end"] > start), None)
    last = next((w for w in reversed(words) if w["start"] < end), None)
    if first is None or last is None:
        return start, end
    # A small lead-in and tail keep the first and last word from sounding clipped.
    return round(max(first["start"] - 0.15, 0), 2), round(last["end"] + 0.35, 2)
