"""Loopback HTTP control API and browser entry point."""
import argparse
import contextlib
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import feedback, imports, media, worker
from .storage import ROOT, db, encode, execute, identifier, init, query
from .upstream import format_seconds

WEB = Path(__file__).resolve().parents[1] / "web"
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
DEFAULTS = {"ratio": "9:16", "quality": "HD", "count": 3, "framing": "fit", "captions": True}


def settings(values):
    if not isinstance(values, dict):
        raise ValueError("Select valid export settings.")
    options = DEFAULTS | {key: values[key] for key in DEFAULTS.keys() & values.keys()}
    if (options["ratio"] not in tuple(media.PRESETS) or options["quality"] not in ("HD", "UHD")
            or options["framing"] not in ("fit", "crop", "track") or type(options["count"]) is not int
            or not 1 <= options["count"] <= 8 or type(options["captions"]) is not bool):
        raise ValueError("Select valid export settings.")
    selector = values.get("selector", "auto")
    if selector not in ("auto", "editorial"):
        raise ValueError("Select an available highlight model.")
    if selector != "auto":
        options["selector"] = selector
    music_id = values.get("music_id")
    if music_id is not None:
        if not isinstance(music_id, str) or not TOKEN.fullmatch(music_id):
            raise ValueError("Choose a soundtrack you uploaded.")
        options["music_id"] = music_id
    return options


class Handler(BaseHTTPRequestHandler):
    def dispatch(self):
        self.cookie = False
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return self.reply({"error": "Open the local ClipFarm address."}, 403)
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                return self.reply({"error": "This action must come from ClipFarm."}, 403)
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        owner = cookies.get("framefold")
        self.owner = owner.value if owner and TOKEN.fullmatch(owner.value) else identifier()
        self.cookie = not owner or owner.value != self.owner
        self.connection.settimeout(60)
        parts = urlsplit(self.path).path.strip("/").split("/")
        try:
            if parts[0] == "api":
                self.api(parts[1:])
            elif (self.command == "GET" and len(parts) == 2 and parts[0] == "fonts"
                  and parts[1] in {"barlow-condensed-semibold.woff2", "dm-sans.woff2",
                                   "OFL-BarlowCondensed.txt", "OFL-DMSans.txt"}):
                self.file(WEB / "fonts" / parts[1])
            elif self.command == "GET" and parts[0] in ("", "app.js", "style.css", "theme.js", "example.jpg", "surf-study.jpg"):
                if len(parts) != 1:
                    raise LookupError
                self.file(WEB / (parts[0] or "index.html"))
            else:
                raise LookupError
        except LookupError:
            self.reply({"error": "This item could not be found."}, 404)
        except (ValueError, TypeError, OverflowError) as exc:
            self.reply({"error": worker.public_error(exc, "Choose a playable video and try again.")}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as exc:
            print(exc, file=sys.stderr)
            self.reply({"error": "Could not read the video or reach its host. Check the link or try Upload."}, 503)
        finally:
            self.close_connection = True

    do_GET = do_POST = dispatch

    def headers_for(self, status, kind, size, extra=None):
        self.send_response(status)
        if self.cookie:
            self.send_header("Set-Cookie", f"framefold={self.owner}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def reply(self, value, status=200):
        data = encode(value).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 32768:
            raise ValueError("The request must contain valid JSON.")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError("The request must contain a JSON object.")
        return data

    def file(self, path, attachment=False):
        if not path.is_file():
            raise LookupError
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        extra = {"Accept-Ranges": "bytes"}
        if attachment:
            extra["Content-Disposition"] = f'attachment; filename="{path.name}"'
        size = path.stat().st_size
        start, end, status = 0, size-1, 200
        if self.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"])
            if match and (match[1] or match[2]):
                start = int(match[1]) if match[1] else max(0, size-int(match[2]))
                end = min(size-1, int(match[2])) if match[1] and match[2] else size-1
            if not match or not (0 <= start <= end < size):
                return self.headers_for(416, kind, 0, {"Content-Range": f"bytes */{size}"})
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            status = 206
        with path.open("rb") as handle:
            self.headers_for(status, kind, end-start+1, extra)
            handle.seek(start)
            remaining = end-start+1
            while remaining:
                chunk = handle.read(min(remaining, 1024*1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def owned(self, sql, uid):
        if not isinstance(uid, str) or not TOKEN.fullmatch(uid):
            raise LookupError
        rows = query(sql, (uid, self.owner))
        if not rows:
            raise LookupError
        return rows[0]

    def job(self, uid):
        return self.owned("""SELECT jobs.*,sources.name,sources.metadata FROM jobs
                            JOIN sources ON jobs.source=sources.id WHERE jobs.id=? AND jobs.owner=?""", uid)

    def clip(self, uid):
        return self.owned("""SELECT clips.*,jobs.cancelled,sources.metadata FROM clips JOIN jobs ON clips.job=jobs.id
                            JOIN sources ON jobs.source=sources.id WHERE clips.id=? AND jobs.owner=?""", uid)

    def source(self):
        if shutil.disk_usage(ROOT).free < 1536 * 1024**2:
            raise ValueError("Free at least 1.5 GB of disk space before importing.")
        if self.headers.get_content_type() == "application/json":
            data = self.body()
            url = data.get("url", "")
            if not isinstance(url, str) or not url or len(url) > 4096:
                raise ValueError("Enter a direct HTTPS video link.")
            with contextlib.closing(imports.public_url(url.strip())) as responses:
                stream, length, name = next(responses)
                result = imports.save(stream, length, name, "link", self.owner)
        else:
            size = int(self.headers.get("Content-Length", "0"))
            name = Path(unquote(self.headers.get("X-File-Name", "Uploaded video"))).name
            result = imports.save(self.rfile, size, name, "upload", self.owner)
        self.reply(result, 201)

    def export_options(self, values):
        options = settings(values)
        if "music_id" in options:
            self.owned("SELECT id FROM music WHERE id=? AND owner=?", options["music_id"])
        return encode(options)

    def music(self):
        if shutil.disk_usage(ROOT).free < 512 * 1024**2:
            raise ValueError("Free at least 512 MB of disk space before importing music.")
        size = int(self.headers.get("Content-Length", "0"))
        name = Path(unquote(self.headers.get("X-File-Name", "Soundtrack"))).name
        return self.reply(imports.save_music(self.rfile, size, name, self.owner), 201)

    def create(self):
        body = self.body()
        source = self.owned("SELECT * FROM sources WHERE id=? AND owner=?", body.get("source"))
        key = body.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError("Generate requires an idempotency key.")
        options = self.export_options(body.get("settings", {}))
        run = identifier()
        with db() as connection:
            connection.execute("INSERT OR IGNORE INTO jobs (id,owner,source,request_key,settings) VALUES (?,?,?,?,?)",
                               (run, self.owner, source["id"], key, options))
            row = connection.execute("SELECT id,source,settings FROM jobs WHERE owner=? AND request_key=?",
                                     (self.owner, key)).fetchone()
            if row["source"] != source["id"] or encode(settings(json.loads(row["settings"]))) != options:
                raise ValueError("This request key already belongs to another run.")
        self.reply({"id": row["id"]}, 202)

    def clips(self, uid):
        result = query("SELECT * FROM clips WHERE job=? ORDER BY rank,parent,id", (uid,))
        for clip in result:
            clip["options"] = json.loads(clip["options"])
            clip["artifact"] = json.loads(clip["artifact"]) if clip["artifact"] else None
            clip["range_label"] = f"{format_seconds(clip['start_ms']/1000)}–{format_seconds(clip['end_ms']/1000)}"
        return result

    def job_api(self, uid, action):
        job = self.job(uid)
        if self.command == "GET":
            clips = self.clips(uid)
            if action == "clips":
                return self.reply(clips)
            if action == "ratings":
                rows = query("""SELECT ratings.record FROM ratings JOIN clips ON clips.id=ratings.clip
                                WHERE clips.job=? ORDER BY clips.rank""", (uid,))
                data = "".join(row["record"] + "\n" for row in rows).encode()
                self.headers_for(200, "application/x-ndjson", len(data),
                                 {"Content-Disposition": 'attachment; filename="clipfarm-ratings.jsonl"'})
                self.wfile.write(data)
                return
            if action == "preview":
                return self.file(ROOT / uid / "preview.jpg")
            if action == "download":
                ready = [clip for clip in clips if clip["state"] == "ready"]
                if not ready:
                    raise LookupError
                with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                    archive = Path(temporary) / "clipfarm-clips.zip"
                    with zipfile.ZipFile(archive, "w") as zipped:
                        for clip in ready:
                            for extension in ("mp4", "srt", "vtt"):
                                path = ROOT / uid / f"{clip['id']}.{extension}"
                                if path.is_file():
                                    zipped.write(path, f"pick-{clip['rank']:02}-{clip['id'][:8]}.{extension}")
                        zipped.writestr("manifest.json", encode({"clips": ready, "version": media.VERSION, "selector": job["selector"]}))
                    return self.file(archive, attachment=True)
            if not action:
                for key in ("settings", "metadata"):
                    job[key] = json.loads(job[key])
                job.pop("owner")
                job.pop("request_key")
                return self.reply({**job, "clips": clips, "online": worker.online(), "notice": worker.ready_notice(job)})
        if self.command == "POST" and action == "cancel":
            execute("""UPDATE jobs SET cancelled=1,state='cancelled' WHERE id=?
                       AND state IN ('queued','running')""", (uid,))
            return self.reply({"ok": True})
        raise LookupError

    def clip_api(self, uid, action):
        clip = self.clip(uid)
        if action == "rating":
            if self.command == "GET":
                return self.reply({"record": feedback.read(uid)})
            return self.reply({"record": feedback.save(clip, self.body())})
        if self.command == "GET" and clip["state"] == "ready":
            extension = {"play": "mp4", "download": "mp4", "srt": "srt", "vtt": "vtt", "poster": "jpg"}.get(action)
            if extension:
                return self.file(ROOT / clip["job"] / f"{uid}.{extension}", attachment=action in ("download", "srt", "vtt"))
        if self.command == "POST" and action in ("retry", "revisions"):
            body = self.body() if action == "revisions" else {}
            with db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if action == "retry":
                    changed = connection.execute(
                        "UPDATE clips SET state='queued',error=NULL WHERE id=? AND state='failed'", (uid,))
                    if not changed.rowcount:
                        return self.reply({"id": uid}, 202)
                else:
                    if clip["state"] != "ready":
                        raise ValueError("Wait until this clip is ready before adjusting it.")
                    start, end = body.get("start_ms"), body.get("end_ms")
                    duration = json.loads(clip["metadata"])["duration_ms"]
                    if (type(start) is not int or type(end) is not int
                            or not 0 <= start < end <= duration or not 1000 <= end-start <= 90000):
                        raise ValueError("Choose a range of 1 to 90 seconds within the source.")
                    options = self.export_options(json.loads(clip["options"]) | body.get("settings", {}))
                    existing = connection.execute(
                        """SELECT id FROM clips WHERE parent=? AND start_ms=? AND end_ms=? AND options=?""",
                        (uid, start, end, options)).fetchone()
                    if existing:
                        return self.reply({"id": existing["id"]}, 202)
                    parent, uid = uid, identifier()
                    connection.execute("""INSERT INTO clips (id,job,rank,start_ms,end_ms,reason,options,parent)
                                          VALUES (?,?,?,?,?,?,?,?)""",
                                       (uid, clip["job"], clip["rank"], start, end, clip["reason"], options, parent))
                connection.execute("""UPDATE jobs SET cancelled=0,error=NULL,
                                      state=CASE WHEN state='running' THEN state ELSE 'queued' END WHERE id=?""",
                                   (clip["job"],))
            return self.reply({"id": uid}, 202)
        raise LookupError

    def api(self, parts):
        if self.command == "GET":
            if parts == ["health"]:
                return self.reply({"online": worker.online(), "version": media.VERSION})
            if parts == ["jobs"]:
                return self.reply(query("""SELECT j.id,j.state,j.created,s.name,
                    (SELECT count(*) FROM clips WHERE job=j.id AND state='ready') ready
                    FROM jobs j JOIN sources s ON j.source=s.id WHERE j.owner=? ORDER BY j.created DESC LIMIT 50""",
                                        (self.owner,)))
        if self.command == "POST":
            if parts == ["music"]:
                return self.music()
            if parts == ["sources"]:
                return self.source()
            if parts == ["jobs"]:
                return self.create()
        if 2 <= len(parts) <= 3:
            category, uid, *actions = parts
            action = actions[0] if actions else ""
            if category == "jobs":
                return self.job_api(uid, action)
            if category == "clips":
                return self.clip_api(uid, action)
        raise LookupError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-worker", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    init()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        process = None if args.no_worker else subprocess.Popen([sys.executable, "-m", "framefold.worker"],
                                                              start_new_session=True)
        print(f"ClipFarm · http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()


if __name__ == "__main__":
    main()
