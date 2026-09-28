# F9Y Clip Machine

Turn a full podcast episode (up to about two hours) into ready-to-post
vertical clips for TikTok, Instagram Reels and YouTube Shorts.

1. **Transcribe.** Whisper runs on your computer and writes down every word
   with its exact time. It's free.
2. **Pick.** Claude reads the whole transcript and shortlists 10 to 15 complete
   takes, using `clip_guide.md` as its idea of a good F9Y clip. Each one comes
   with an on-screen hook line and a post caption.
3. **Review.** A page opens in your browser. Play each candidate, nudge the
   start or end, set the framing and host tag, edit the hook, and tick
   **Keep**. Then press **Render kept clips**.
4. **Render.** Each kept clip comes out 1080×1920 with word-by-word captions,
   the hook at the top, an optional host name tag, and a 2-second F9Y end card.
   `captions.md` has the caption and hashtags for every clip.

Everything lands in a folder next to the episode called `<episode>_clips/`.

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

## Each episode

```
python -m f9yclip "/path/to/episode.mp4"
```

- The first run downloads the Whisper model (a few hundred MB).
- Transcribing is the slow step on a laptop. It only happens once per episode,
  because the transcript is saved and reused.
- Want different picks? `python -m f9yclip episode.mp4 --repick`
- Want more or fewer? `--count 20`
- In a hurry? `--no-review` skips the page and renders every candidate
  Claude scored 7 or higher. The review step is what makes the clips good,
  so use this sparingly.

## Framing options on the review page

- **Whole shot, blurred fill** (default): the full wide shot in the middle
  with a blurred copy filling the top and bottom. Safe for any camera setup.
- **Crop left / center / right**: a tight vertical crop of one third of the
  frame. Use it when a single host is talking and sits on that side.

## Teaching it your taste

`clip_guide.md` is sent to Claude on every run. When a pick misses, add a
line about why ("never clip the fantasy segment", "Jake's rants always
hit"), and the next episode's picks will follow it.

## Test without a real episode

```
python -m tests.smoke_test
```

This renders two clips from a generated test video with a stand-in for
Claude, so it needs no API key and no model download.
