"""Stand-in for ESPN's site data, so tests run offline with known scores and players."""

import io
import json
import subprocess
from unittest import mock

from f9yclip.render import ffmpeg_exe

SCOREBOARD = {"events": [{
    "date": "2026-09-27T17:00Z",
    "competitions": [{
        "status": {"type": {"completed": True, "shortDetail": "Final"}},
        "competitors": [
            {"homeAway": "home", "score": "27", "winner": True,
             "team": {"displayName": "New York Giants", "location": "New York", "abbreviation": "NYG"}},
            {"homeAway": "away", "score": "24", "winner": False,
             "team": {"displayName": "Minnesota Vikings", "location": "Minnesota", "abbreviation": "MIN"}},
        ],
    }],
}]}
TEAMS = {"sports": [{"leagues": [{"teams": [
    {"team": {"id": "16", "displayName": "Minnesota Vikings", "location": "Minnesota", "abbreviation": "MIN"}},
    {"team": {"id": "19", "displayName": "New York Giants", "location": "New York", "abbreviation": "NYG"}},
]}]}]}
ROSTER = {"athletes": [{"position": "offense", "items": [
    {"fullName": "J.J. McCarthy", "lastName": "McCarthy", "position": {"abbreviation": "QB"},
     "headshot": {"href": "https://a.espncdn.com/i/headshots/nfl/players/full/1.png"}},
]}]}


def headshot_png() -> bytes:
    """A gray head-and-shoulders shape on a transparent background, like ESPN's cutouts."""
    graph = ("color=c=black@0:s=350x254,format=rgba,"
             "geq=r=150:g=150:b=150:a='255*lt(hypot(X-175,Y-90),60)+255*lt(hypot(X-175,Y-300),140)'")
    return subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i", graph, "-frames:v", "1",
                           "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout


def patched():
    png = headshot_png()

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/scoreboard" in url:
            body = json.dumps(SCOREBOARD).encode()
        elif url.endswith("/roster"):
            body = json.dumps(ROSTER).encode()
        elif "/teams?" in url:
            body = json.dumps(TEAMS).encode()
        elif url.endswith(".png"):
            body = png
        else:
            raise OSError(f"unexpected {url}")
        return io.BytesIO(body)

    return mock.patch("urllib.request.urlopen", side_effect=urlopen)
