# F9Y Clip Machine

Turn a full podcast episode into ready-to-post vertical clips for TikTok,
Instagram Reels and YouTube Shorts, on the same night you record.

Every clip comes out 1080×1920 with:

- **A hook opener.** The punchiest 2 to 6 second line plays first, then the
  whole take.
- **Team logos.** The teams the clip is about appear under the title as it
  opens, and again when a team first comes up later. All 32 NFL teams and the
  FBS teams from f9ytools are included.
- Word-by-word captions, the title at the top, an optional host tag, and a
  2-second F9Y end card.

`captions.md` has each clip's post caption and hashtags.

## One-time setup

1. Install Python 3.10 or newer from python.org.
2. In a terminal, from this `clipper` folder:
   ```
   pip install -r requirements.txt
   ```
   This includes a copy of ffmpeg, so you don't need to install it separately.
3. Get an Anthropic API key from https://console.anthropic.com, then set it:
   - Mac/Linux: `export ANTHROPIC_API_KEY=sk-ant-...` (add it to `~/.zshrc` to keep it)
   - Windows PowerShell: `setx ANTHROPIC_API_KEY "sk-ant-..."`, then open a new terminal
4. Edit `settings.json`: add every host's name (these show up as host tag
   choices), and set your real handle and end card line.

## Recording setup in OBS (free, one time)

**Put each mic on its own track** so the camera can follow whoever is talking:

1. Settings → Output → set Output Mode to **Advanced**. On the Recording tab,
   tick audio tracks **1, 2, 3 and 4**, and record as **MKV**. MKV keeps every
   track, and File → Remux Recordings turns it into an MP4 afterwards if you
   need one.
2. In the Audio Mixer, open the gear → **Advanced Audio Properties**. Tick
   track 1 for every source, since that's the full mix YouTube gets. Then tick
   track 2 only for Grant's mic, track 3 only for Noah's, and track 4 only for
   Caden's.
3. `settings.json` already says Grant = 2, Noah = 3, Caden = 4. Change it if
   you set the tracks up differently.

**Camera boxes.** `"boxes": "thirds"` means the recording is three cameras side
by side, left to right in the order of `hosts`. If you record the YouTube
layout instead, give each host's box in pixels of the recorded frame:
`"boxes": {"Grant": [x, y, width, height], "Noah": [...], "Caden": [...]}`.
Each clip crops the box of whoever is talking. The bigger the boxes are in
the recording, the sharper the clips look.

Recordings without the separate tracks still work. Their clips use the whole
shot instead of following the speaker.

**A free transcript while you record.** The LocalVocal plugin
(https://github.com/royshil/obs-localvocal) transcribes on your own computer
during the recording and can save an `.srt` that lines up with the recording.
Hand that file to `--transcript`.

## Right after recording

Pick whichever way suits the night.

**Let Claude find the clips.** Export a transcript from your recording or
editing app as `.srt` or `.vtt`, then:
```
python -m f9yclip "episode.mp4" --transcript "episode.srt"
```
Claude reads it and the review page opens a couple of minutes later. Only the
clips you keep get transcribed for word-by-word captions, so there's no long wait.

**Name the clips yourself.** Write the moments you want in a text file, one
per line, with an optional title:
```
12:30-13:45 JJ McCarthy is HIM
1:02:10 - 1:03:00
```
then:
```
python -m f9yclip "episode.mp4" --ranges picks.txt
```
Claude writes the titles you left blank, the hooks, the teams and the post
captions. Add `--no-review` to go straight to rendering.

No transcript and no list? `python -m f9yclip "episode.mp4"` still works, but
it transcribes the whole episode first, which is slow on a laptop.

## The review page

Play each candidate, nudge the start or end, tick **Keep**, then press
**Render kept clips**. Per clip you can also:

- turn the hook opener off, or move it,
- edit the team list (comma separated, names as in `f9yclip/teams.json`),
- set the framing:
  - **Follow the speaker** (default when mic tracks are set up) cuts to
    whoever is talking and shows their name the first time the camera lands
    on them.
  - **Whole shot, blurred fill** puts the full wide shot in the
    middle with a blurred copy above and below. It's safe for any camera setup.
  - **Crop left / center / right** is a tight vertical crop of one third of the
    frame, for when one host is talking and sits on that side.

## Scheduling the week

Every file name starts with its suggested post date, one clip a day starting
tomorrow (`--first-post 2026-10-06` or `--per-day 2` to change it). Schedule
from TikTok Studio on desktop (up to 10 days ahead), Meta Business Suite for
Instagram, and YouTube Studio for Shorts.

## Options

- `--repick` asks Claude for fresh candidates instead of reusing the last picks.
- `--count 20` asks for more or fewer candidates.
- `--no-review` skips the review page and renders your ranges, or every
  candidate Claude scored 7 or higher.
- The Whisper model (a few hundred MB) downloads the first time captions are timed.

## Teaching it your taste

`clip_guide.md` is sent to Claude on every run. When a pick misses, add a
line about why ("never clip the fantasy segment", "Jake's rants always
hit"), and the next episode's picks will follow it.

## Test without a real episode

```
python -m tests.smoke_test
```

This runs both modes end to end on a generated test video, with stand-ins for
Claude, Whisper and the review page, so it needs no API key and no model download.
