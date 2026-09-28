"""Offline smoke test: fake episode + fake transcript + stubbed Claude, real render.

Run from the clipper folder:  python -m tests.smoke_test
Needs no API key and no whisper model download.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from f9yclip import pick
from f9yclip.render import ffmpeg_exe, render_all

SENTENCES = [
    "Okay so here is my hot take for the week.",
    "The Broncos defense is the best unit in football and it is not close.",
    "No way, you are crazy, look at the pressure rate numbers.",
    "I did look, they are first in EPA per play allowed since week three.",
    "Fine, but the offense is still a bottom ten group.",
    "That part I will give you, the quarterback play has been rough.",
]


def fake_transcript() -> dict:
    t, segments = 0.5, []
    for s in SENTENCES:
        words = []
        for w in s.split():
            words.append({"start": round(t, 2), "end": round(t + 0.32, 2), "word": w})
            t += 0.38
        segments.append({"start": words[0]["start"], "end": words[-1]["end"], "text": s, "words": words})
        t += 0.5
    return {"duration": 30.0, "segments": segments}


class FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        block = mock.Mock(type="text", text=self.text)
        return mock.Mock(stop_reason="end_turn", content=[block])


def main():
    tmp = Path(tempfile.mkdtemp())
    video = tmp / "episode.mp4"
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=30",
         "-f", "lavfi", "-i", "sine=frequency=330:d=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True,
    )
    transcript = fake_transcript()

    # Claude answers with rough times that cut into words; picking must snap them.
    answer = {"clips": [
        {"start": 3.1, "end": 13.9, "title": "Broncos D is the best in football", "why": "Strong take plus a stat",
         "caption": "Best defense in the league?", "hashtags": ["nfl", "broncos"], "score": 9},
        {"start": 14.0, "end": 16.0, "title": "Too short", "why": "", "caption": "", "hashtags": [], "score": 3},
    ]}
    fake_client = mock.Mock()
    fake_client.beta.messages.stream.return_value = FakeStream(json.dumps(answer))
    with mock.patch.object(pick.anthropic, "Anthropic", return_value=fake_client):
        clips = pick.pick_clips(transcript, "guide", tmp / "candidates.json", count=2)

    kwargs = fake_client.beta.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5" and kwargs["fallbacks"] == "default"
    assert "Broncos defense" in kwargs["messages"][0]["content"]
    assert len(clips) == 1, clips  # the 2-second pick is dropped
    words = [w for s in transcript["segments"] for w in s["words"]]
    assert any(abs(clips[0]["start"] - (w["start"] - 0.15)) < 0.01 for w in words), clips[0]

    variants = [dict(clips[0], layout="blur", speaker="Grant"), dict(clips[0], layout="center", title="Crop test")]
    outputs = render_all(video, transcript, variants, tmp / "out", {"handle": "@Full9Yards"})
    for out in outputs:
        probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
        assert "1080x1920" in probe, probe
        print("rendered", out)
    frame = tmp / "frame.png"
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", "2", "-i", str(outputs[0]), "-frames:v", "1", str(frame)], check=True)
    end = tmp / "endcard.png"
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-sseof", "-1", "-i", str(outputs[0]), "-frames:v", "1", str(end)], check=True)
    print("frames:", frame, end)
    print((tmp / "out" / "captions.md").read_text())


if __name__ == "__main__":
    main()
