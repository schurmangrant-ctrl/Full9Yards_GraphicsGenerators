"""A small local web page for approving and trimming clip candidates.

Runs on http://127.0.0.1:8765 and blocks until you press "Render" on the
page, then returns the clips you kept, with your edits.
"""

import json
import mimetypes
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).parent / "review.html"


def review(video: Path, transcript: dict, clips: list[dict], settings: dict, port: int = 8765,
           cameras: dict | None = None, games: list[str] | None = None) -> list[dict]:
    """cameras, when the camera follows the speaker: {"boxes", "crop_w", "framing", "stills"}.
    Dragging a host's crop box on the page updates cameras["framing"] in place."""
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/data":
                payload = {
                    "clips": clips,
                    "segments": [
                        {"start": s["start"], "end": s["end"], "text": (f"{s['speaker']}: " if s.get("speaker") else "") + s["text"]}
                        for s in transcript["segments"]
                    ],
                    "hosts": settings.get("hosts", []),
                    "duration": transcript["duration"],
                    "episode": video.name,
                    "speaker_switching": bool(cameras and cameras.get("follow", True)),
                    "games": games or [],
                    "cameras": cameras and {
                        name: {"box": box, "crop_w": cameras["crop_w"], "at": cameras["framing"].get(name, 0.5)}
                        for name, box in cameras["boxes"].items()
                    },
                }
                self._send(200, json.dumps(payload).encode(), "application/json")
            elif self.path == "/video":
                self._send_video()
            elif self.path.startswith("/still/") and cameras:
                self._send_still(self.path[len("/still/"):])
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/save":
                return self._send(404, b"not found", "text/plain")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            saved = json.loads(body)
            result["clips"] = saved["clips"]
            if cameras:
                cameras["framing"].update(saved.get("framing") or {})
            self._send(200, b'{"ok": true}', "application/json")
            done.set()

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_still(self, name):
            from urllib.parse import unquote

            from .render import ffmpeg_exe

            name = unquote(name)
            if name not in cameras["boxes"]:
                return self._send(404, b"not found", "text/plain")
            x, y, w, h = cameras["boxes"][name]
            jpg = subprocess.run(
                [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{cameras['stills'].get(name, 0):.2f}",
                 "-i", str(video), "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},scale=640:-2",
                 "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                capture_output=True,
            ).stdout
            self._send(200 if jpg else 404, jpg or b"no frame", "image/jpeg" if jpg else "text/plain")

        def _send_video(self):
            # Browsers need HTTP range requests to seek inside a long video.
            size = video.stat().st_size
            ctype = mimetypes.guess_type(video.name)[0] or "video/mp4"
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = min(int(b), size - 1) if b else min(start + 8 * 1024 * 1024, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(video, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"\nReview your clips at {url}  (press Render on the page when you're done)")
    webbrowser.open(url)
    done.wait()
    server.shutdown()
    return result["clips"]
