"""Cut approved clips into vertical 9:16 videos.

Each clip can open on its hook line, then play the full take, then a
2-second F9Y end card. All text (captions, title, end card) is an ASS
subtitle file drawn by ffmpeg's libass filter; score and player cards slide
in when a finished game or a player comes up.
"""

import re
import shutil
import subprocess
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
FONT = "Barlow Condensed Black"
W, H, FPS = 1080, 1920, 30
END_CARD_SECONDS = 2.0
TITLE_SECONDS = 3.0
CARD_SECONDS = 3.2
CARD_SLIDE = 0.3
CARD_Y = 170         # the title's spot, free once the title is gone
SPLIT_H = 960        # with game footage: speaker on the top half, game on the bottom

# ASS colors are &HAABBGGRR. Brand: black #101311, green #3F7954, gold #D4AF37.
WHITE = "&H00FFFFFF"
BLACK = "&H00111310"
GREEN = "&H0054793F"
GOLD = "&H0037AFD4"
CREAM = "&H00E7EDEF"


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def render_all(
    video: Path, clips: list[dict], out_dir: Path, settings: dict, post_dates: list[str] | None = None,
    crop: tuple | None = None, split_crop: tuple | None = None,
) -> list[Path]:
    """Render each clip. Every clip must carry its own "words" (absolute word times).

    crop is (width, height, {host: (x, y)}) for the "speaker" layout, which also
    needs "shots" (and "hook_shots" when opening on the hook) on each clip.
    split_crop is the same at 9:8, for the top half when a clip has game footage
    ("game_path", starting "game_from" seconds in). "voice" is "normal",
    "pitch" (higher voice, same speed) or "fast" (sped up, voice higher too).
    """
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
        cards = card_appearances(clip, hook_len)
        card_files = []
        for k, (card, cs, ce) in enumerate(cards):
            png = f"card{n:02d}_{k}.png"
            make_card(card, build, png)
            card_files.append((png, cs, ce))

        ass_name = f"clip{n:02d}.ass"
        (build / ass_name).write_text(clip_ass(clip, hook, settings))
        target = (out_dir / f"{name}.mp4").resolve()
        render_one(video.resolve(), clip, hook, build, ass_name, target, crop, split_crop, settings, card_files)
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


def card_appearances(clip: dict, hook_len: float) -> list[tuple[dict, float, float]]:
    """(card, from, to) on the output timeline, one at a time, after the title is gone."""
    total = hook_len + clip["end"] - clip["start"]
    free_from = title_end(hook_len, total) + 0.2
    shown = []
    for card in clip.get("cards", []):
        if not card.get("keep", True):
            continue
        t = max(hook_len + card["at"] - clip["start"], free_from)
        if t + CARD_SECONDS > total - 0.3:
            continue
        shown.append((card, t, t + CARD_SECONDS))
        free_from = t + CARD_SECONDS + 0.3
    return shown


def make_card(card: dict, build: Path, png: str) -> None:
    """Draw one card as a PNG in the build folder, in the F9Y style: black body,
    green bar down the left, a small green "FULL 9 YARDS / ..." line, cream type."""
    images, events = [], []
    text = lambda x, y, an, size, color, s: events.append(
        dialogue("Card", 0, 5, f"{{\\an{an}\\pos({x},{y})\\fs{size}\\c{color}}}{ass_text(s)}"))
    if card["kind"] == "score":
        cw, ch = 920, 240
        text(48, 20, 7, 34, GREEN, f"FULL 9 YARDS / {card['status']}")
        dim = "&H00888E8C"
        for sd, lx, sx in zip(card["sides"], (60, cw - 190), (345, cw - 345)):
            color = CREAM if sd["winner"] or not any(o["winner"] for o in card["sides"]) else dim
            text(sx, 142, 5, 130, color, sd["score"])
            if sd.get("logo") and (ASSETS / "logos" / sd["logo"]).exists():
                images.append((ASSETS / "logos" / sd["logo"], lx, 72, 130, 130))
            else:
                text(lx + 65, 137, 5, 64, CREAM, sd["abbr"])
        text(cw // 2 + 8, 142, 5, 90, GREEN, "-")
    else:
        cw, ch = 900, 240
        events.append(dialogue("Card", 0, 5, f"{{\\an7\\pos(0,0)\\p1\\c{GREEN}}}m 16 0 l 256 0 l 256 {ch} l 16 {ch}"))
        if card.get("headshot") and Path(card["headshot"]).exists():
            images.append((Path(card["headshot"]), 16, 0, 240, ch))
        name = card["name"].upper()
        size = 84 if len(name) <= 14 else 68 if len(name) <= 19 else 54
        team = card["team"].split()[-1] if card["logo"].startswith("nfl-") else card["team"]
        sub = " · ".join(x for x in (card.get("position", ""), team.upper()) if x)
        text(284, 22, 7, 34, GREEN, "FULL 9 YARDS / PLAYER")
        text(282, 122, 4, size, CREAM, name)
        text(284, 192, 4, 40, CREAM, sub)
        if (ASSETS / "logos" / card["logo"]).exists():
            images.append((ASSETS / "logos" / card["logo"], cw - 140, 70, 110, 110))
    events.append(dialogue("Card", 0, 5, f"{{\\an7\\pos(0,0)\\p1\\c{GREEN}}}m 0 0 l 16 0 l 16 {ch} l 0 {ch}"))

    ass_name = png.replace(".png", ".ass")
    (build / ass_name).write_text(ass_file([style("Card", 60, WHITE, BLACK, outline=0, shadow=0, align=7, margin_v=0)],
                                           events, cw, ch))
    inputs, graph, last = [], [f"[0:v]ass={ass_name}:fontsdir=.[b0]"], "b0"
    for k, (path, x, y, w, h) in enumerate(images):
        inputs += ["-i", str(path.resolve())]
        # Logos fit inside their box; a headshot fills its panel from the bottom up.
        fit = "decrease" if "logos" in path.parts else "increase"
        graph.append(f"[{k + 1}:v]scale={w}:{h}:force_original_aspect_ratio={fit},crop='min(iw,{w})':'min(ih,{h})'[im{k}]")
        graph.append(f"[{last}][im{k}]overlay=x={x}+({w}-w)/2:y={y}+({h}-h)[b{k + 1}]" if fit == "increase" else
                     f"[{last}][im{k}]overlay=x={x}+({w}-w)/2:y={y}+({h}-h)/2[b{k + 1}]")
        last = f"b{k + 1}"
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x101311:s={cw}x{ch}:d=1",
         *inputs, "-filter_complex", ";".join(graph), "-map", f"[{last}]", "-frames:v", "1", png],
        cwd=build, check=True,
    )


def ass_text(s: str) -> str:
    return str(s).replace("{", "(").replace("}", ")").replace("\\", "/")


def render_one(
    video: Path, clip: dict, hook, build: Path, ass_name: str, target: Path,
    crop=None, split_crop=None, settings: dict | None = None, cards: list | None = None,
) -> None:
    start, end = clip["start"], clip["end"]
    layout = clip.get("layout", "blur")
    if layout == "speaker" and not (crop and clip.get("shots")):
        layout = "blur"
    body_len = (hook[1] - hook[0] if hook else 0) + end - start
    game = clip.get("game_path")
    size = (W, SPLIT_H) if game else (W, H)
    if game:
        crop = split_crop
        if layout == "speaker" and not crop:
            layout = "center"
        if layout == "blur":
            layout = "center"

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
    card_inputs = []
    for i, (file, _, _) in enumerate(cards or []):
        inputs += ["-loop", "1", "-t", f"{body_len:.2f}", "-i", file]
        card_inputs.append(silence + 1 + i)
    game_input = silence + 1 + len(cards or [])
    if game:
        # Loops if the footage is shorter than the clip; its own audio is dropped.
        inputs += ["-stream_loop", "-1", "-ss", f"{float(clip.get('game_from') or 0):.2f}", "-t", f"{body_len:.2f}", "-i", str(game)]

    norm_v = f"fps={FPS},setsar=1,format=yuv420p"
    norm_a = "aformat=sample_rates=48000:channel_layouts=stereo"
    parts = [f"{frame(0, layout, clip.get('shots'), crop, size)},{norm_v}[mv]", f"[0:a:0]{norm_a}[ma]"]
    top = "tv" if game else "bv0"
    if hook:
        hook_layout = layout if layout != "speaker" or clip.get("hook_shots") else ("center" if game else "blur")
        parts += [f"{frame(1, hook_layout, clip.get('hook_shots'), crop, size)},{norm_v}[hv]", f"[1:a:0]{norm_a}[ha]",
                  f"[hv][ha][mv][ma]concat=n=2:v=1:a=1[{top}][ba]"]
    else:
        parts += [f"[mv]null[{top}]", "[ma]anull[ba]"]
    if game:
        parts += [f"[{game_input}:v]scale={W}:{H - SPLIT_H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H - SPLIT_H},{norm_v}[gv]", "[tv][gv]vstack=inputs=2[bv0]"]

    last = "bv0"
    # Cards slide in from the left, hold, and slide out to the right.
    card_y = SPLIT_H + 100 if game else CARD_Y
    for i, (_, a, b) in enumerate(cards or []):
        x0 = f"(W-w)/2"
        x = (f"if(lt(t,{a + CARD_SLIDE:.2f}),-w+({x0}+w)*(t-{a:.2f})/{CARD_SLIDE},"
             f"if(gt(t,{b - CARD_SLIDE:.2f}),{x0}+(W-{x0})*(t-{b - CARD_SLIDE:.2f})/{CARD_SLIDE},{x0}))")
        parts.append(f"[{last}][{card_inputs[i]}:v]overlay=x='{x}':y={card_y}:enable='between(t,{a:.2f},{b:.2f})'[cd{i}]")
        last = f"cd{i}"
    parts.append(f"[{last}]ass={ass_name}:fontsdir=.[cv0]")
    boost = float((settings or {}).get("voice_boost", 1.2))
    voice = clip.get("voice", "normal")
    if voice == "fast":
        parts += [f"[cv0]setpts=PTS/{boost}[cv]", f"[ba]asetrate={48000 * boost:.0f},aresample=48000[ba2]"]
    elif voice == "pitch":
        parts += ["[cv0]null[cv]", f"[ba]asetrate={48000 * boost:.0f},aresample=48000,atempo={1 / boost:.5f}[ba2]"]
    else:
        parts += ["[cv0]null[cv]", "[ba]anull[ba2]"]

    parts += [
        f"[{mark}:v]scale=420:-1[mark]",
        f"[{card}:v][mark]overlay=(W-w)/2:(H-h)/2-100,ass=endcard.ass:fontsdir=.,{norm_v}[ev]",
        f"[{silence}:a]{norm_a}[ea]",
        "[cv][ba2][ev][ea]concat=n=2:v=1:a=1[v][a]",
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


def frame(i: int, layout: str, shots=None, crop=None, size=(W, H)) -> str:
    ow, oh = size
    if layout == "speaker":
        cw, ch, spots = crop
        x = y = None
        for t0, t1, host in reversed(shots):
            sx, sy = spots[host]
            x = f"{sx}" if x is None else f"if(lt(t\\,{t1:.2f})\\,{sx}\\,{x})"
            y = f"{sy}" if y is None else f"if(lt(t\\,{t1:.2f})\\,{sy}\\,{y})"
        return f"[{i}:v]crop=w={cw}:h={ch}:x={x}:y={y},scale={ow}:{oh}"
    if layout == "blur":
        return (
            f"[{i}:v]split[a{i}][b{i}];"
            f"[a{i}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.12[bg{i}];"
            f"[b{i}]scale={W}:-2[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2"
        )
    x = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[layout]
    return f"[{i}:v]crop=ih*{ow}/{oh}:ih:{x}:0,scale={ow}:{oh}"


def clip_ass(clip: dict, hook, settings: dict) -> str:
    start, end = clip["start"], clip["end"]
    hook_len = hook[1] - hook[0] if hook else 0.0
    total = hook_len + end - start
    words = clip.get("words", [])
    split = bool(clip.get("game_path"))
    events = []

    title = clip.get("title", "").strip().upper()
    if title:
        events.append(dialogue("Title", 0, title_end(hook_len, total), title))

    stretches = [(start, end, hook_len)]
    if hook:
        stretches.insert(0, (hook[0], hook[1], 0.0))
    for s0, s1, placed_at in stretches:
        seg_words = [w for w in words if w["start"] >= s0 - 0.05 and w["end"] <= s1 + 0.05]
        limit = placed_at + (s1 - s0)
        chunks = list(caption_chunks(seg_words))
        for n, chunk in enumerate(chunks):
            # Linger a beat after the last word, but never overlap the next line,
            # or both show at once stacked in the wrong order.
            last = chunk[-1]["end"] + 0.25
            if n + 1 < len(chunks):
                last = min(last, chunks[n + 1][0]["start"])
            for i, w in enumerate(chunk):
                t0 = placed_at + w["start"] - s0
                t1 = placed_at + (chunk[i + 1]["start"] if i + 1 < len(chunk) else last) - s0
                text = " ".join(
                    (f"{{\\c{GOLD}}}{clean(x['word'])}{{\\c{WHITE}}}" if j == i else clean(x["word"]))
                    for j, x in enumerate(chunk)
                )
                if i == 0:
                    # Each new line pops in: starts a touch small and snaps to full size.
                    text = "{\\fscx80\\fscy80\\t(0,100,\\fscx100\\fscy100)}" + text
                events.append(dialogue("Caption", max(t0, placed_at), min(t1, limit), text))

    return ass_file(
        [
            # Captions sit centered just under the speaker's chin: at the bottom of
            # the speaker's half when there's game footage, otherwise a little
            # below the middle, clear of the app's caption, username and buttons.
            style("Caption", 100, WHITE, BLACK, outline=8, shadow=0, align=2, margin_v=990 if split else 760,
                  margin_l=140, margin_r=140),
            style("Title", 74, WHITE, BLACK, outline=18, shadow=0, align=8, margin_v=170, box=True),
        ],
        events,
    )


def end_card_ass(settings: dict) -> str:
    line1 = settings.get("end_card_line", "FULL EPISODE ON YOUTUBE").upper()
    handle = settings.get("handle", "@Full9Yards")
    return ass_file(
        [
            style("Big", 80, CREAM, BLACK, outline=0, shadow=0, align=5, margin_v=0),
            style("Small", 62, GREEN, BLACK, outline=0, shadow=0, align=5, margin_v=0),
        ],
        [
            # Sits right under the logo mark (420x310, centered 100px above middle).
            dialogue("Big", 0, END_CARD_SECONDS, f"{{\\pos({W // 2},{H // 2 + 130})}}{line1}"),
            dialogue("Small", 0, END_CARD_SECONDS, f"{{\\pos({W // 2},{H // 2 + 210})}}{handle}"),
        ],
    )


def title_end(hook_len: float, total: float) -> float:
    """The title shows over the hook, or for the first few seconds when there's no hook."""
    return min(hook_len if hook_len else TITLE_SECONDS, total)


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


def style(name, size, primary, back, outline, shadow, align, margin_v, box=False, margin_l=60, margin_r=60):
    # BorderStyle 3 draws an opaque box in the outline color behind the text.
    border_style = 3 if box else 1
    return (
        f"Style: {name},{FONT},{size},{primary},{primary},{back},{back},"
        f"0,0,0,0,100,100,1,0,{border_style},{outline},{shadow},{align},{margin_l},{margin_r},{margin_v},1"
    )


def dialogue(style_name, t0, t1, text):
    return f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},{style_name},,0,0,0,,{text}"


def ass_file(styles, events, width=W, height=H):
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
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
