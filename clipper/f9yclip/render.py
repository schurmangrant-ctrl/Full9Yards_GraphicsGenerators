"""Cut approved clips into vertical 9:16 videos with captions and an end card.

Everything drawn on the video (captions, hook title, host name, end card
text) is an ASS subtitle file rendered by ffmpeg's libass filter, so the only
dependency is an ffmpeg build with libass (the standard builds have it).
"""

import re
import shutil
import subprocess
from pathlib import Path

from .transcribe import all_words

ASSETS = Path(__file__).parent / "assets"
FONT = "Barlow Condensed Black"
W, H, FPS = 1080, 1920, 30
END_CARD_SECONDS = 2.0

# ASS colors are &HAABBGGRR. Brand: black #101311, green #3F7954, gold #D4AF37.
WHITE = "&H00FFFFFF"
BLACK = "&H00111310"
GREEN = "&H0054793F"
GOLD = "&H0037AFD4"


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def render_all(video: Path, transcript: dict, clips: list[dict], out_dir: Path, settings: dict) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    build = out_dir / ".build"
    build.mkdir(exist_ok=True)
    shutil.copy(ASSETS / "BarlowCondensed-Black.ttf", build / "BarlowCondensed-Black.ttf")
    shutil.copy(ASSETS / "logo-mark.png", build / "logo.png")
    (build / "endcard.ass").write_text(end_card_ass(settings))

    words = all_words(transcript)
    outputs = []
    posts = ["# Clips ready to post\n"]
    for n, clip in enumerate(clips, 1):
        name = f"{n:02d}-{slug(clip['title'])}"
        print(f"Rendering {name} ({clip['end'] - clip['start']:.0f}s)...")
        ass_name = f"{name}.ass"
        (build / ass_name).write_text(clip_ass(clip, words, settings))
        target = out_dir / f"{name}.mp4"
        render_one(video.resolve(), clip, build, ass_name, target.resolve())
        outputs.append(target)
        tags = " ".join("#" + t.lstrip("#") for t in clip.get("hashtags", []))
        posts.append(f"## {name}\n\n{clip.get('caption', '')}\n\n{tags}\n")

    (out_dir / "captions.md").write_text("\n".join(posts))
    shutil.rmtree(build, ignore_errors=True)
    return outputs


def render_one(video: Path, clip: dict, build: Path, ass_name: str, target: Path) -> None:
    start, end = clip["start"], clip["end"]
    layout = clip.get("layout", "blur")

    if layout == "blur":
        clip_chain = (
            "[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.12[bg];"
            f"[b]scale={W}:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        x = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[layout]
        clip_chain = f"[0:v]crop=ih*9/16:ih:{x}:0,scale={W}:{H}"

    norm_v = f"fps={FPS},setsar=1,format=yuv420p"
    norm_a = "aformat=sample_rates=48000:channel_layouts=stereo"
    graph = (
        f"{clip_chain},{norm_v},ass={ass_name}:fontsdir=.[cv];"
        f"[2:v]scale=420:-1[logo];"
        f"[1:v][logo]overlay=(W-w)/2:(H-h)/2-160,ass=endcard.ass:fontsdir=.,{norm_v}[ev];"
        f"[0:a]{norm_a}[ca];[3:a]{norm_a}[ea];"
        "[cv][ca][ev][ea]concat=n=2:v=1:a=1[v][a]"
    )
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video),
        "-f", "lavfi", "-i", f"color=c=0x101311:s={W}x{H}:r={FPS}:d={END_CARD_SECONDS}",
        "-loop", "1", "-t", f"{END_CARD_SECONDS}", "-i", "logo.png",
        "-f", "lavfi", "-t", f"{END_CARD_SECONDS}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(target),
    ]
    # Run inside the build folder so the subtitle and font paths stay relative;
    # absolute Windows paths (C:\...) break ffmpeg's filter syntax.
    subprocess.run(cmd, cwd=build, check=True)


def clip_ass(clip: dict, words: list[dict], settings: dict) -> str:
    start, end = clip["start"], clip["end"]
    length = end - start
    events = []

    title = clip.get("title", "").strip().upper()
    if title:
        events.append(dialogue("Hook", 0, length, title))

    speaker = clip.get("speaker", "").strip()
    if speaker:
        events.append(dialogue("Name", 0, min(length, 6), speaker.upper()))

    clip_words = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    for chunk in caption_chunks(clip_words):
        for i, w in enumerate(chunk):
            t0 = w["start"] - start
            t1 = (chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk[-1]["end"] + 0.25) - start
            text = " ".join(
                (f"{{\\c{GOLD}}}{clean(x['word'])}{{\\c{WHITE}}}" if j == i else clean(x["word"]))
                for j, x in enumerate(chunk)
            )
            events.append(dialogue("Caption", max(t0, 0), min(t1, length), text))

    return ass_file(
        [
            style("Caption", 96, WHITE, BLACK, outline=7, shadow=0, align=2, margin_v=560),
            style("Hook", 74, WHITE, BLACK, outline=18, shadow=0, align=8, margin_v=210, box=True),
            style("Name", 52, WHITE, GREEN, outline=14, shadow=0, align=2, margin_v=420, box=True),
        ],
        events,
    )


def end_card_ass(settings: dict) -> str:
    line1 = settings.get("end_card_line", "FULL EPISODE ON YOUTUBE").upper()
    handle = settings.get("handle", "@Full9Yards")
    return ass_file(
        [
            style("Big", 80, WHITE, BLACK, outline=0, shadow=0, align=5, margin_v=0),
            style("Small", 58, GOLD, BLACK, outline=0, shadow=0, align=5, margin_v=0),
        ],
        [
            dialogue("Big", 0, END_CARD_SECONDS, f"{{\\pos({W // 2},{H // 2 + 230})}}{line1}"),
            dialogue("Small", 0, END_CARD_SECONDS, f"{{\\pos({W // 2},{H // 2 + 330})}}{handle}"),
        ],
    )


def caption_chunks(words: list[dict], max_words: int = 3, max_span: float = 1.4, max_gap: float = 0.6):
    chunk: list[dict] = []
    for w in words:
        if chunk and (
            len(chunk) >= max_words
            or w["end"] - chunk[0]["start"] > max_span
            or w["start"] - chunk[-1]["end"] > max_gap
            or chunk[-1]["word"].endswith((".", "?", "!"))
        ):
            yield chunk
            chunk = []
        chunk.append(w)
    if chunk:
        yield chunk


def clean(word: str) -> str:
    return word.strip().upper().replace("{", "(").replace("}", ")").rstrip(",")


def style(name, size, primary, back, outline, shadow, align, margin_v, box=False):
    # BorderStyle 3 draws an opaque box in the outline color behind the text.
    border_style, outline_color = (3, back) if box else (1, back)
    return (
        f"Style: {name},{FONT},{size},{primary},{primary},{outline_color},{back},"
        f"0,0,0,0,100,100,1,0,{border_style},{outline},{shadow},{align},60,60,{margin_v},1"
    )


def dialogue(style_name, t0, t1, text):
    return f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},{style_name},,0,0,0,,{text}"


def ass_file(styles, events):
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {W}",
            f"PlayResY: {H}",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )


def ass_time(t: float) -> str:
    t = max(t, 0)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "clip"
