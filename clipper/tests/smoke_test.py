"""Offline smoke test of the whole pipeline with a generated video.

Run from the clipper folder:  python -m tests.smoke_test
Claude, Whisper and the review page are stood in for, so it needs no API
key, no model download and no browser. Rendering and file handling are real.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from f9yclip import __main__ as cli
from f9yclip import pick
from f9yclip.render import ffmpeg_exe

SENTENCES = [
    "Okay so here is my hot take for the week.",
    "JJ McCarthy is so good on this Giants team.",
    "No way, you are crazy, look at the pressure rate numbers.",
    "I did look, they are first in EPA per play allowed since week three.",
    "Fine, but the offense is still a bottom ten group.",
    "That part I will give you, the quarterback play has been rough.",
]


def fake_words() -> list[dict]:
    t, words = 0.5, []
    for s in SENTENCES:
        for w in s.split():
            words.append({"start": round(t, 2), "end": round(t + 0.32, 2), "word": w})
            t += 0.38
        t += 0.5
    return words


WORDS = fake_words()


def fake_words_for_range(video, start, end, model_size="small"):
    return [w for w in WORDS if start - 0.05 <= w["start"] and w["end"] <= end + 0.05]


def write_srt(path: Path) -> None:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    blocks, i = [], 0
    for n, s in enumerate(SENTENCES, 1):
        ws = WORDS[i:i + len(s.split())]
        i += len(ws)
        blocks.append(f"{n}\n{ts(ws[0]['start'])} --> {ts(ws[-1]['end'])}\n{s}\n")
    path.write_text("\n".join(blocks))


class FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text=self.text)])


def fake_claude(clips):
    client = mock.Mock()
    client.beta.messages.stream.return_value = FakeStream(json.dumps({"clips": clips}))
    return mock.patch.object(pick.anthropic, "Anthropic", return_value=client), client


def run_cli(argv):
    with mock.patch.object(sys, "argv", ["f9yclip", *argv]):
        cli.main()


def check_video(path: Path, min_seconds: float) -> None:
    probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    assert "1080x1920" in probe, probe
    h, m, s = probe.split("Duration: ")[1].split(",")[0].split(":")
    assert int(h) * 3600 + int(m) * 60 + float(s) >= min_seconds, probe


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
    patches = [
        mock.patch.object(cli, "words_for_range", side_effect=fake_words_for_range),
        mock.patch.object(cli, "review", side_effect=lambda v, t, clips, s, port: [dict(c, keep=True) for c in clips if (c.get("score") or 9) >= 7]),
    ]
    for p in patches:
        p.start()

    # 1. Claude picks from an imported .srt: hook opener + two team logos.
    srt = tmp / "episode.srt"
    write_srt(srt)
    hook_line = next(w for w in WORDS if w["word"] == "JJ")
    patch, client = fake_claude([
        {"start": 0.9, "end": 16.2, "title": "JJ McCarthy is HIM", "hook_start": hook_line["start"],
         "hook_end": hook_line["start"] + 3.0, "teams": [{"name": "New York Giants", "at": hook_line["start"] + 2},
                                                        {"name": "Minnesota Vikings", "at": 12.0},
                                                        {"name": "Not A Team", "at": 3.0}],
         "why": "Bold take", "caption": "Is JJ that guy?", "hashtags": ["nfl"], "score": 9},
        {"start": 20.0, "end": 22.0, "title": "Too short", "hook_start": 0, "hook_end": 0, "teams": [],
         "why": "", "caption": "", "hashtags": [], "score": 3},
    ])
    sections = tmp / "sections.txt"
    sections.write_text("0:00 NFL recap\n0:10 CFB preview\n")
    with patch:
        run_cli([str(video), "--transcript", str(srt), "--sections", str(sections), "--first-post", "2026-10-01"])
    prompt = client.beta.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "JJ McCarthy is so good" in prompt and "New York Giants" in prompt
    assert prompt.index("SECTION: NFL recap") < prompt.index("JJ McCarthy") < prompt.index("SECTION: CFB preview"), prompt
    out = tmp / "episode_clips"
    approved = json.loads((out / "approved.json").read_text())
    assert len(approved) == 1 and approved[0]["use_hook"] and approved[0]["section"] == "NFL recap", approved
    assert [t["name"] for t in approved[0]["teams"]] == ["New York Giants", "Minnesota Vikings"], approved[0]["teams"]
    rendered = sorted(out.glob("*.mp4"))
    assert rendered[0].name.startswith("2026-10-01-Thu_01-jj-mccarthy"), rendered
    clip_len = approved[0]["end"] - approved[0]["start"]
    check_video(rendered[0], clip_len + 3.0 + 1.5)  # hook + clip + end card
    for sec, name in [(1.0, "hook.png"), (4.0, "opening.png")]:
        subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", str(sec), "-i", str(rendered[0]),
                        "-frames:v", "1", str(tmp / name)], check=True)
    print("mode 1 ok:", rendered[0].name)

    # 2. Hosts name their own clips; Claude only writes titles, hooks and teams.
    ranges = tmp / "picks.txt"
    ranges.write_text("0:01-0:09 My own title\n\nnot a range\n0:14.5 - 0:25\n")
    patch, client = fake_claude([
        {"start": 1, "end": 9, "title": "ignored", "hook_start": 0, "hook_end": 0, "teams": [],
         "why": "", "caption": "Take one", "hashtags": ["nfl"], "score": 8},
        {"start": 14.5, "end": 25, "title": "Offense is bottom ten", "hook_start": 0, "hook_end": 0,
         "teams": [{"name": "denver broncos", "at": 15}], "why": "", "caption": "Take two", "hashtags": [], "score": 8},
    ])
    out2 = tmp / "second"
    video2 = tmp / "second.mp4"
    video2.write_bytes(video.read_bytes())
    with patch:
        run_cli([str(video2), "--ranges", str(ranges), "--no-review"])
    approved = json.loads((tmp / "second_clips" / "approved.json").read_text())
    assert [c["title"] for c in approved] == ["My own title", "Offense is bottom ten"], approved
    assert approved[1]["teams"][0]["name"] == "Denver Broncos"
    assert len(list((tmp / "second_clips").glob("*.mp4"))) == 2
    print("mode 2 ok")

    # 3. Three 1080p webcams in a 4K grid (Grant top left, Noah top right,
    #    Caden bottom left), each host's mic on its own track: the camera
    #    should follow whoever is loudest. Grant talks 0-6s, Noah 6-12s,
    #    Caden 12-20s; each mic also hears the others faintly.
    video3 = tmp / "three.mkv"
    loud = {2: "lt(t,6)", 3: "between(t,6,12)", 4: "gte(t,12)"}
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "color=c=red:s=1920x1080:d=20:r=30",
           "-f", "lavfi", "-i", "color=c=lime:s=1920x1080:d=20:r=30",
           "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=20:r=30",
           "-f", "lavfi", "-i", "sine=frequency=300:d=20"]
    for track in (2, 3, 4):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={100 * track + 200}:d=20"]
    graph = "[0:v][1:v][2:v][2:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:fill=black,drawbox=x=1920:y=1080:w=1920:h=1080:c=black:t=fill[v];" + ";".join(
        f"[{i + 2}:a]volume='if({loud[i]},1,0.05)':eval=frame[a{i}]" for i in (2, 3, 4))
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "3:a", "-map", "[a2]", "-map", "[a3]", "-map", "[a4]",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video3)]
    subprocess.run(cmd, check=True)
    ranges3 = tmp / "picks3.txt"
    ranges3.write_text("0:02-0:18 Speaker test\n")
    patch, client = fake_claude([
        {"start": 2, "end": 18, "title": "x", "hook_start": 13.0, "hook_end": 16.0, "teams": [],
         "why": "", "caption": "", "hashtags": [], "score": 8},
    ])
    with patch:
        run_cli([str(video3), "--ranges", str(ranges3), "--no-review"])
    clip = json.loads((tmp / "three_clips" / "approved.json").read_text())[0]
    hosts = [h for _, _, h in clip["shots"]]
    assert hosts == ["Grant", "Noah", "Caden"], clip["shots"]
    assert abs(clip["shots"][1][0] - 4.0) < 0.8 and abs(clip["shots"][2][0] - 10.0) < 0.8, clip["shots"]
    assert [h for _, _, h in clip["hook_shots"]] == ["Caden"], clip["hook_shots"]
    out3 = next((tmp / "three_clips").glob("*.mp4"))
    hook_len = 3.0
    for t, want in [(1.0, "blue"), (hook_len + 2.0, "red"), (hook_len + 6.0, "green"), (hook_len + 12.0, "blue")]:
        rgb = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-ss", str(t), "-i", str(out3), "-frames:v", "1",
                              "-vf", "crop=10:10:900:300,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        got = ["red", "green", "blue"][max(range(3), key=lambda k: rgb[k])]
        assert got == want, (t, want, list(rgb))
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", str(hook_len + 4.5), "-i", str(out3),
                    "-frames:v", "1", str(tmp / "speaker.png")], check=True)
    print("mode 3 ok:", clip["shots"], "frame:", tmp / "speaker.png")
    print("frames:", tmp / "hook.png", tmp / "opening.png")


if __name__ == "__main__":
    main()
