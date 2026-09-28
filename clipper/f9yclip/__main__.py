"""Run the whole pipeline: python -m f9yclip path/to/episode.mp4"""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from .pick import pick_clips
from .render import render_all
from .review import review
from .transcribe import transcribe

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(prog="f9yclip", description="Turn a podcast episode into vertical clips.")
    parser.add_argument("video", type=Path, help="The full episode video file")
    parser.add_argument("--count", type=int, default=12, help="How many candidates to ask for (default 12)")
    parser.add_argument("--no-review", action="store_true", help="Skip the review page and render every candidate scored 7+")
    parser.add_argument("--repick", action="store_true", help="Ask Claude for fresh candidates instead of reusing the last picks")
    parser.add_argument("--prep", action="store_true", help="Only transcribe and pick, then stop. Run it overnight, review later.")
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

    work = video.parent / f"{video.stem}_clips"
    work.mkdir(exist_ok=True)
    picks_file = work / "candidates.json"
    if args.repick and picks_file.exists():
        picks_file.unlink()

    transcript = transcribe(video, work / "transcript.json", settings.get("whisper_model", "small"))
    candidates = pick_clips(transcript, guide, picks_file, count=args.count)
    if args.prep:
        print(f"\nReady for review. When you have 10 minutes, run the same command without --prep.")
        return

    if args.no_review:
        chosen = [dict(c, layout="blur") for c in candidates if c.get("score", 0) >= 7]
    else:
        chosen = review(video, transcript, candidates, settings, port=args.port)
    (work / "approved.json").write_text(json.dumps(chosen, indent=2))

    per_day = max(args.per_day, 1)
    post_dates = [(args.first_post + timedelta(days=i // per_day)).strftime("%Y-%m-%d-%a") for i in range(len(chosen))]
    outputs = render_all(video, transcript, chosen, work, settings, post_dates)
    print(f"\nDone. {len(outputs)} clips and captions.md are in {work}")


if __name__ == "__main__":
    main()
