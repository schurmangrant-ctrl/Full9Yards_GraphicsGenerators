"""Cut approved clips into vertical 9:16 videos.

Each clip can open on its hook line, then play the full take, then a
2-second F9Y end card. All text (captions, title, host tag, end card) is an
ASS subtitle file drawn by ffmpeg's libass filter; team logos are overlaid
when the clip opens and when a team first comes up.
"""

import re
import shutil
import subprocess
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
FONT = "Barlow Condensed Black"
W, H, FPS = 1080, 1920, 30
END_CARD_SECONDS = 2.0
LOGO_SIZE = 260
LOGO_Y = 350

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


def render_all(
    video: Path, clips: list[dict], out_dir: Path, settings: dict, post_dates: list[str] | None = None, crop: tuple | None = None
) -> list[Path]:
    """Render each clip. Every clip must carry its own "words" (absolute word times).

    crop is (width, height, {host: (x, y)}) for the "speaker" layout, which also
    needs "shots" (and "hook_shots" when opening on the hook) on each clip.
    """
    from .pick import TEAMS

    logo_files = {t["name"]: t["logo"] for t in TEAMS}
    out_dir.mkdir(parents=True, exist_ok=True)
    build = out_dir / ".build"
    build.mkdir(exist_ok=True)
    shutil.copy(ASSETS / "BarlowCondensed-Black.ttf", build / "BarlowCondensed-Black.ttf")
    shutil.copy(ASSETS / "logo-mark.png", build / "logo.png")
    (build / "endcard.ass").write_text(end_card_ass(settings))

    outputs = []
    posts = ["# Clips ready to post\n"]
    for n, clip in enumerate(clips, 1):
        post_on = post_dates[n - 1] if post_dates else None
        # Prefixing the post date keeps the folder sorted in posting order.
        name = f"{n:02d}-{slug(clip['title'])}"
        if post_on:
            name = f"{post_on}_{name}"
        print(f"Rendering {name} ({clip['end'] - clip['start']:.0f}s)...")

        hook = hook_range(clip)
        hook_len = hook[1] - hook[0] if hook else 0.0
        appearances = logo_appearances(clip, hook_len)
        for team, _, _ in appearances:
            shutil.copy(ASSETS / "logos" / logo_files[team], build / logo_files[team])

        ass_name = f"clip{n:02d}.ass"
        (build / ass_name).write_text(clip_ass(clip, hook, settings))
        target = (out_dir / f"{name}.mp4").resolve()
        render_one(video.resolve(), clip, hook, [(logo_files[t], a, b) for t, a, b in appearances], build, ass_name, target, crop)
        outputs.append(target)

        tags = " ".join("#" + t.lstrip("#") for t in clip.get("hashtags", []))
        when = f"Post on: {post_on}\n\n" if post_on else ""
        posts.append(f"## {name}\n\n{when}{clip.get('caption', '')}\n\n{tags}\n")

    (out_dir / "captions.md").write_text("\n".join(posts))
    shutil.rmtree(build, ignore_errors=True)
    return outputs


def hook_range(clip: dict) -> tuple[float, float] | None:
    if not clip.get("use_hook"):
        return None
    hs, he = clip.get("hook_start", 0), clip.get("hook_end", 0)
    if clip["start"] <= hs < he <= clip["end"]:
        return hs, he
    return None


def logo_appearances(clip: dict, hook_len: float) -> list[tuple[str, float, float]]:
    """(team, from, to) on the output timeline: the clip's teams as it opens, then each first mention."""
    teams = clip.get("teams", [])
    total = hook_len + clip["end"] - clip["start"]
    shown = []
    opening = []
    for t in teams:
        if t["name"] not in opening and len(opening) < 2:
            opening.append(t["name"])
    for name in opening:
        shown.append((name, 0.0, min(3.5, total)))
    for t in teams:
        at = hook_len + t["at"] - clip["start"]
        if at > 5 and at + 0.5 < total:
            shown.append((t["name"], at, min(at + 2.5, total)))
    return shown


def render_one(
    video: Path, clip: dict, hook, logos: list[tuple[str, float, float]], build: Path, ass_name: str, target: Path, crop=None
) -> None:
    start, end = clip["start"], clip["end"]
    layout = clip.get("layout", "blur")
    if layout == "speaker" and not (crop and clip.get("shots")):
        layout = "blur"
    body_len = (hook[1] - hook[0] if hook else 0) + end - start

    inputs = ["-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video)]
    if hook:
        inputs += ["-ss", f"{hook[0]:.2f}", "-t", f"{hook[1] - hook[0]:.2f}", "-i", str(video)]
    idx = 2 if hook else 1
    card, mark, silence = idx, idx + 1, idx + 2
    inputs += [
        "-f", "lavfi", "-i", f"color=c=0x101311:s={W}x{H}:r={FPS}:d={END_CARD_SECONDS}",
        "-loop", "1", "-t", f"{END_CARD_SECONDS}", "-i", "logo.png",
        "-f", "lavfi", "-t", f"{END_CARD_SECONDS}", "-i", "anullsrc=r=48000:cl=stereo",
    ]
    logo_inputs = []
    for i, (file, _, _) in enumerate(logos):
        inputs += ["-loop", "1", "-t", f"{body_len:.2f}", "-i", file]
        logo_inputs.append(silence + 1 + i)

    norm_v = f"fps={FPS},setsar=1,format=yuv420p"
    norm_a = "aformat=sample_rates=48000:channel_layouts=stereo"
    parts = [f"{frame(0, layout, clip.get('shots'), crop)},{norm_v}[mv]", f"[0:a:0]{norm_a}[ma]"]
    if hook:
        hook_layout = layout if layout != "speaker" or clip.get("hook_shots") else "blur"
        parts += [f"{frame(1, hook_layout, clip.get('hook_shots'), crop)},{norm_v}[hv]", f"[1:a:0]{norm_a}[ha]", "[hv][ha][mv][ma]concat=n=2:v=1:a=1[bv0][ba]"]
    else:
        parts += ["[mv]null[bv0]", "[ma]anull[ba]"]

    last = "bv0"
    openers = [i for i, (_, a, _) in enumerate(logos) if a == 0.0]
    for i, (_, a, b) in enumerate(logos):
        if i in openers and len(openers) == 2:
            x = f"{W // 2 - LOGO_SIZE - 20}" if i == openers[0] else f"{W // 2 + 20}"
        else:
            x = f"{(W - LOGO_SIZE) // 2}"
        parts.append(f"[{logo_inputs[i]}:v]scale={LOGO_SIZE}:{LOGO_SIZE}:force_original_aspect_ratio=decrease[lg{i}]")
        parts.append(f"[{last}][lg{i}]overlay=x={x}+({LOGO_SIZE}-w)/2:y={LOGO_Y}:enable='between(t,{a:.2f},{b:.2f})'[lo{i}]")
        last = f"lo{i}"
    parts.append(f"[{last}]ass={ass_name}:fontsdir=.[cv]")

    parts += [
        f"[{mark}:v]scale=420:-1[mark]",
        f"[{card}:v][mark]overlay=(W-w)/2:(H-h)/2-160,ass=endcard.ass:fontsdir=.,{norm_v}[ev]",
        f"[{silence}:a]{norm_a}[ea]",
        "[cv][ba][ev][ea]concat=n=2:v=1:a=1[v][a]",
    ]
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(target),
    ]
    # Run inside the build folder so subtitle, font and logo paths stay relative;
    # absolute Windows paths (C:\...) break ffmpeg's filter syntax.
    subprocess.run(cmd, cwd=build, check=True)


def frame(i: int, layout: str, shots=None, crop=None) -> str:
    if layout == "speaker":
        cw, ch, spots = crop
        x = y = None
        for t0, t1, host in reversed(shots):
            sx, sy = spots[host]
            x = f"{sx}" if x is None else f"if(lt(t\\,{t1:.2f})\\,{sx}\\,{x})"
            y = f"{sy}" if y is None else f"if(lt(t\\,{t1:.2f})\\,{sy}\\,{y})"
        return f"[{i}:v]crop=w={cw}:h={ch}:x={x}:y={y},scale={W}:{H}"
    if layout == "blur":
        return (
            f"[{i}:v]split[a{i}][b{i}];"
            f"[a{i}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.12[bg{i}];"
            f"[b{i}]scale={W}:-2[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2"
        )
    x = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[layout]
    return f"[{i}:v]crop=ih*9/16:ih:{x}:0,scale={W}:{H}"


def clip_ass(clip: dict, hook, settings: dict) -> str:
    start, end = clip["start"], clip["end"]
    hook_len = hook[1] - hook[0] if hook else 0.0
    total = hook_len + end - start
    words = clip.get("words", [])
    events = []

    title = clip.get("title", "").strip().upper()
    if title:
        events.append(dialogue("Title", 0, total, title))
    speaker = clip.get("speaker", "").strip()
    if speaker:
        events.append(dialogue("Name", hook_len, min(total, hook_len + 6), speaker.upper()))
    elif clip.get("layout") == "speaker":
        # Name each host the first time the camera lands on them.
        seen = set()
        for offset, shot_list in [(0.0, clip.get("hook_shots") if hook else None), (hook_len, clip.get("shots"))]:
            for t0, t1, host in shot_list or []:
                if host not in seen and t1 - t0 >= 1.5:
                    seen.add(host)
                    events.append(dialogue("Name", offset + t0, min(offset + t0 + 2.2, total), host.upper()))

    stretches = [(start, end, hook_len)]
    if hook:
        stretches.insert(0, (hook[0], hook[1], 0.0))
    for s0, s1, placed_at in stretches:
        seg_words = [w for w in words if w["start"] >= s0 - 0.05 and w["end"] <= s1 + 0.05]
        limit = placed_at + (s1 - s0)
        for chunk in caption_chunks(seg_words):
            for i, w in enumerate(chunk):
                t0 = placed_at + w["start"] - s0
                t1 = placed_at + (chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk[-1]["end"] + 0.25) - s0
                text = " ".join(
                    (f"{{\\c{GOLD}}}{clean(x['word'])}{{\\c{WHITE}}}" if j == i else clean(x["word"]))
                    for j, x in enumerate(chunk)
                )
                events.append(dialogue("Caption", max(t0, placed_at), min(t1, limit), text))

    return ass_file(
        [
            style("Caption", 96, WHITE, BLACK, outline=7, shadow=0, align=2, margin_v=560),
            style("Title", 74, WHITE, BLACK, outline=18, shadow=0, align=8, margin_v=170, box=True),
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
    border_style = 3 if box else 1
    return (
        f"Style: {name},{FONT},{size},{primary},{primary},{back},{back},"
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
