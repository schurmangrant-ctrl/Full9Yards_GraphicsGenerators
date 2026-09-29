# F9Y Clip Machine

Turn a full podcast episode into ready-to-post vertical clips for TikTok,
Instagram Reels and YouTube Shorts, on the same night you record.

Every clip comes out 1080×1920 with:

- **A hook opener.** The punchiest 2 to 6 second line plays first, then the
  whole take. The cut from the hook into the take hits with a quick punch-in,
  a cream flash and a whoosh into a bass hit (`"hook_transition": false` in
  `settings.json` turns it off).
- **Score and player pop-ups.** When a finished game comes up, its final
  score slides in; when a player is named, a card with their headshot,
  position and team. Claude spots the mentions; the scores and headshots
  come from ESPN's public site data, and a card is left out rather than
  guessed if the lookup fails.
- Word-by-word captions at chest level, clear of the like and share buttons;
  the title over the hook (or the first 3 seconds); a 2-second F9Y end card.

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
4. Edit `settings.json`: add every host's name and set your real handle and
   end card line.

## Recording setup in OBS (free, one time)

**Put each mic on its own track** so the camera can follow whoever is talking:

1. Settings → Output → set Output Mode to **Advanced**. On the Recording tab,
   tick audio tracks **1, 2, 3 and 4**, and record as **MKV**, which is safe if
   OBS crashes. Afterwards, File → Remux Recordings makes an MP4 with every
   track kept. Give the clip tool that MP4, because the review page plays MP4
   in any browser.
2. In the Audio Mixer, open the gear → **Advanced Audio Properties**. Tick
   track 1 for every source, since that's the full mix YouTube gets. Then tick
   track 2 only for Grant's mic, track 3 only for Noah's, and track 4 only for
   Caden's.
3. `settings.json` already says Grant = 2, Noah = 3, Caden = 4. Change it if
   you set the tracks up differently.

**Camera layout: webcams two to a row.** Make an OBS scene with no overlay
and put each 1080p webcam in it at full size, two to a row, in the same
order you'll pass to `--hosts`:

| Hosts | Canvas and output resolution | Layout |
| --- | --- | --- |
| 2 | 3840x1080 | side by side |
| 3 | 3840x2160 | two on top, one bottom left |
| 4 | 3840x2160 | two on top, two below |

Every camera keeps its full quality, the clips crop from it, and the YouTube
version is cut from the same file. A fourth host needs their own mic track
in `mic_tracks`.

**Who's on tonight.** Pass the hosts left to right, top row first:
`--hosts Grant,Noah`. Leave it off when everyone in `mic_tracks` is on, in
that order.

**Clip framing.** A vertical clip shows about a third of each webcam's
width. The review page has a still of each camera with a gold box on it:
drag the box over the person. It's saved in `framing.json` and reused next
episode, so you only touch it when someone sits differently.

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

**Tell it where each part of the show starts** (optional). Keep recording one
file; just jot down when each part begins, in a text file:
```
0:00 CFB recap
38:10 NFL recap
1:12:00 CFB preview
1:25:30 NFL preview
```
and add `--sections sections.txt`. Claude spreads its picks across every
part instead of taking them all from the longest one, and each card on the
review page shows which part it came from.

No transcript and no list? `python -m f9yclip "episode.mp4"` still works, but
it transcribes the whole episode first, which is slow on a laptop.

## The review page

Play each candidate, nudge the start or end, tick **Keep**, then press
**Render kept clips**. Per clip you can also:

- turn the hook opener off, or move it,
- set the framing:
  - **Follow the speaker** (default when mic tracks are set up) cuts to
    whoever is talking.
  - **Whole shot, blurred fill** puts the full wide shot in the
    middle with a blurred copy above and below. It's safe for any camera setup.
  - **Crop left / center / right** is a tight vertical crop of one third of the
    frame, for when one host is talking and sits on that side.

### Pop-ups

Each card lists its score and player pop-ups with the moment they appear.
Untick any that are wrong. Scores are looked up for games in the 10 days
before the recording date (the video file's date, or `--played 2026-09-28`).

### Game footage under the speaker

Put game clips you've downloaded in a `game_footage` folder next to the
episode file. Each card then has a **Game footage** choice. Pick a clip and
the video splits: whoever is talking on the top half, the game playing on the
bottom half (muted, looping if it's shorter than the clip), with the captions
on the seam. **From** sets how many seconds into the game clip to start.

League footage is copyrighted, and TikTok and YouTube can mute or remove
clips that use it, so keep the game clip short and your take the main thing.

### Voice

**Pitched up** raises the voice without changing speed. **Sped up** plays
the whole clip faster, which raises the voice too. `voice_boost` in
`settings.json` sets how strong both are (1.2 by default, a bit over three
semitones and 20% faster).

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
