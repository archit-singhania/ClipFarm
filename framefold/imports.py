"""Stream imports into private staging and probe self-contained media."""
import contextlib
import errno
import hashlib
import http.client
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urljoin, unquote

from .media import probe, probe_music
from .public_network import direct_connection, https_url
from .storage import ROOT, db, encode, identifier

RESERVE = 512 * 1024**2
BLOCK = 1024**2
FORMATS = {"mov", "mp4", "matroska", "webm", "avi", "mpeg", "mpegts", "flv", "ogg"}


def public_url(url):
    for _ in range(5):
        parsed = https_url(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection = direct_connection(parsed.hostname)
        try:
            connection.request("GET", path, headers={"Accept": "video/*, application/octet-stream"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200 or response.getheader("Content-Type", "").startswith("text/"):
                raise ValueError("This link did not return a video. Try a direct link or upload.")
            length = response.getheader("Content-Length")
            yield response, int(length) if length else None, unquote(Path(parsed.path).name) or "Linked video"
            return
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError("Could not read the public video link. Retry or upload the recording.") from exc
        finally:
            connection.close()
    raise ValueError("The video link redirected too many times.")


def require_space(directory, needed=0):
    if shutil.disk_usage(directory).free < RESERVE + needed:
        raise ValueError("There is insufficient disk space for this recording. Free storage and retry.")


def save_url(url, owner):
    url = url.strip()
    if re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#]|$)", url, re.I):
        url = "https://" + url
    parsed = https_url(url)
    # Fragments are client-side and must never supply extractor options.
    url = parsed._replace(fragment="").geturl()
    if Path(parsed.path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".flv", ".ts", ".ogv", ".m4v"}:
        with contextlib.closing(public_url(url)) as responses:
            stream, length, name = next(responses)
            return save(stream, length, name, "link", owner)
    try:
        from .providers import recording
    except ModuleNotFoundError as exc:
        raise ValueError("The recording importer needs its dependencies. Install requirements.txt and restart ClipFarm.") from exc
    with tempfile.TemporaryDirectory(prefix=".recording-", dir=ROOT) as scratch:
        path, name = recording(url, Path(scratch))
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return store_source(path, name, digest, "link", owner)


def save(stream, length, name, origin, owner):
    if length is not None and length <= 0:
        raise ValueError("Choose a video containing media.")
    staging = ROOT / (identifier() + ".upload")
    try:
        digest = stream_file(stream, length, staging)
        return store_source(staging, name, digest, origin, owner)
    finally:
        staging.unlink(missing_ok=True)


def store_source(staging, name, digest, origin, owner):
    metadata = probe(staging)
    if not FORMATS.intersection(metadata["format"].split(",")):
        raise ValueError("Upload a self-contained MP4, MOV, WebM or MKV video.")
    if metadata["duration_ms"] < 2000:
        raise ValueError("Choose a video at least 2 seconds long.")
    source_id = identifier()
    with db() as connection:
        result = connection.execute(
            "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)",
            (source_id, owner, name[:160], digest, encode(metadata), origin),
        )
        if result.rowcount:
            staging.replace(ROOT / (source_id + ".source"))
        else:
            source_id = connection.execute(
                "SELECT id FROM sources WHERE owner=? AND digest=?", (owner, digest)
            ).fetchone()[0]
    return {"id": source_id, "name": name[:160], **metadata}


def stream_file(stream, length, staging, limit=None):
    require_space(staging.parent, length or 0)
    digest = hashlib.sha256()
    size = 0
    try:
        with staging.open("xb") as target:
            while length is None or size < length:
                chunk = stream.read(BLOCK if length is None else min(BLOCK, length - size))
                if not chunk:
                    break
                size += len(chunk)
                if limit is not None and size > limit:
                    raise ValueError("The upload exceeds the size limit.")
                require_space(staging.parent, len(chunk))
                target.write(chunk)
                digest.update(chunk)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise ValueError("The disk filled during transfer. Free storage and retry.") from exc
        raise
    if length is not None and size != length:
        raise ValueError("The transfer ended early. Try again.")
    return digest.hexdigest()


def save_music(stream, length, name, owner):
    limit = 128 * 1024**2
    if not 0 < length <= limit:
        raise ValueError("Choose a soundtrack up to 128 MB.")
    uid = identifier()
    staging = ROOT / (uid + ".upload")
    try:
        digest = stream_file(stream, length, staging, limit)
        duration = probe_music(staging)
        with db() as connection:
            created = connection.execute("INSERT OR IGNORE INTO music "
                                         "(id,owner,name,digest,duration_ms,created) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
                                         (uid, owner, name[:160], digest, duration))
            if created.rowcount:
                staging.replace(ROOT / (uid + ".music"))
            row = connection.execute("SELECT id,name,duration_ms FROM music WHERE owner=? AND digest=?",
                                     (owner, digest)).fetchone()
        return dict(row)
    finally:
        staging.unlink(missing_ok=True)
