"""Stream imports into private staging and probe self-contained media."""
import hashlib
import http.client
import ipaddress
import os
import socket
import ssl
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

from .media import probe
from .storage import ROOT, db, encode, identifier

LIMIT = 1024 ** 3
FORMATS = {"mov", "mp4", "matroska", "webm", "avi", "mpeg", "mpegts", "flv", "ogg"}


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def public_url(url):
    for _ in range(5):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username is not None or not parsed.hostname
                or parsed.port not in (None, 443)):
            raise ValueError("Use a direct public HTTPS video link or upload a file.")
        host = parsed.hostname.encode("idna").decode()
        addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("The link must point to a public video host.")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection = PinnedHTTPS(host, sorted(addresses)[0])
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
        finally:
            connection.close()
    raise ValueError("The video link redirected too many times.")


def save(stream, length, name, origin, owner):
    if length is not None and not 0 < length <= LIMIT:
        raise ValueError("Choose a video smaller than 1 GB.")
    source_id = identifier()
    staging = ROOT / (source_id + ".upload")
    original = ROOT / (source_id + ".source")
    digest = hashlib.sha256()
    size = 0
    try:
        with staging.open("xb") as target:
            while length is None or size < length:
                chunk = stream.read(min(1024 * 1024, length - size) if length is not None else 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > LIMIT:
                    raise ValueError("Choose a video smaller than 1 GB.")
                target.write(chunk)
                digest.update(chunk)
        if length is not None and size != length:
            raise ValueError("The transfer ended early. Try again.")
        metadata = probe(staging)
        if not FORMATS.intersection(metadata["format"].split(",")):
            raise ValueError("Upload a self-contained MP4, MOV, WebM or MKV video.")
        if not 2 <= metadata["duration_ms"] / 1000 <= 3600:
            raise ValueError("Choose a video between 2 seconds and 1 hour.")
        with db() as connection:
            result = connection.execute(
                "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)",
                (source_id, owner, name[:160], digest.hexdigest(), encode(metadata), origin),
            )
            if result.rowcount:
                os.replace(staging, original)
            else:
                source_id = connection.execute(
                    "SELECT id FROM sources WHERE owner=? AND digest=?", (owner, digest.hexdigest())
                ).fetchone()[0]
        return {"id": source_id, "name": name, **metadata}
    finally:
        staging.unlink(missing_ok=True)
