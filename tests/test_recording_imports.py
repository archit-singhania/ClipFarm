import io
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_dlp.networking import Response

from framefold import imports, providers, public_network, storage


class RecordingImport(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for module in (storage, imports):
            self.enterContext(patch.object(module, "ROOT", self.root))
        storage.init()

    def test_recording_hosts_and_bare_link(self):
        urls = {
            "https://www.youtube.com/watch?v=jNQXAC9IVRw": "Youtube",
            "https://www.twitch.tv/videos/6528877": "TwitchVod",
            "https://kick.com/test/videos/01a0251f-ef48-702d-9289-d66922da0e84": "KickVOD",
            "https://rumble.com/vdmum1-example.html": "Rumble",
            "https://vimeo.com/123456": "Vimeo",
            "https://www.tiktok.com/@example/video/1234567890123456789": "TikTok",
            "https://www.instagram.com/reel/ABC123/": "Instagram",
            "https://x.com/example/status/123456789": "Twitter",
        }
        with providers.RecordingDL({"allowed_extractors": providers.EXTRACTORS, "proxy": ""}) as client:
            for url, expected in urls.items():
                with self.subTest(url=url):
                    self.assertEqual(next(k for k, ie in client._ies.items() if ie.suitable(url)), expected)
        with patch.object(providers, "recording", side_effect=ValueError("test")) as fetch:
            with self.assertRaisesRegex(ValueError, "test"):
                imports.save_url("youtu.be/jNQXAC9IVRw#fragment", "owner")
            self.assertEqual(fetch.call_args.args[0], "https://youtu.be/jNQXAC9IVRw")
        self.assertFalse(list(self.root.glob(".recording-*")))

    def test_public_only_network_and_pinned_tls(self):
        for url in ("file:///etc/hosts", "https://example.org:99", "https://user@host/media",
                    "https://example.org\\@127.0.0.1", "http://example.org/video", "https://[fe80::1%25en0]/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_network.https_url(url)
        answers = lambda ip: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))]
        for ip in ("10.1.1.1", "127.0.0.1", "169.254.169.254", "::1", "ff02::1", "64:ff9b::7f00:1"):
            with self.subTest(ip=ip), patch.object(socket, "getaddrinfo", return_value=answers("8.8.8.8")+answers(ip)):
                with self.assertRaisesRegex(ValueError, "public"):
                    public_network.public_addresses("public.example")
        tls = MagicMock()
        with patch.object(socket, "getaddrinfo", return_value=answers("8.8.8.8")), patch.object(socket, "create_connection") as dial:
            conn = public_network.PublicHTTPSConnection("video.example", timeout=30, context=tls)
            conn.connect()
            dial.assert_called_once_with(("8.8.8.8", 443), 30)
            tls.wrap_socket.assert_called_once_with(dial.return_value, server_hostname="video.example")
        with providers.RecordingDL({"proxy": ""}) as client:
            for url in ("file:///test", "https://localhost/x", "https://[::1]/index"):
                with self.subTest(url=url), patch.object(socket, "create_connection") as dial:
                    with self.assertRaises(Exception):
                        client.urlopen(url)
                    dial.assert_not_called()

    def test_hls_public_recording_through_source_storage(self):
        # Exercise the real Generic extractor, native HLS downloader and media
        # probes. Only replace the external HTTPS responses.
        path = self.root / "fragment.ts"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=4:duration=3",
                        "-f", "mpegts", str(path)], check=True)
        segment = path.read_bytes()
        url = "https://media.example/video.m3u8"
        manifest = b"#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nsegment.ts\n#EXT-X-ENDLIST\n"
        def respond(client, req):
            address = req if isinstance(req, str) else req.url
            payload, mime = {url: (manifest, "application/vnd.apple.mpegurl"),
                             "https://media.example/segment.ts": (segment, "video/mp2t")}[address]
            return Response(io.BytesIO(payload), address, {"Content-Type": mime, "Content-Length": str(len(payload))})
        with patch.object(providers.RecordingDL, "urlopen", respond):
            result = imports.save_url(url, "owner")
            other = imports.save_url(url, "other")
        self.assertEqual(result["width"], 128)
        self.assertNotEqual(other["id"], result["id"])
        self.assertTrue((self.root / (result["id"] + ".source")).is_file())
        self.assertFalse(list(self.root.glob(".recording-*")))

    def test_live_protected_and_multi_video_sources_are_rejected(self):
        with providers.RecordingDL({"proxy": ""}) as client:
            for info, expected in [({"_type":"playlist"}, "one recording"), ({"is_live":True}, "live"),
                                   ({"availability":"subscriber_only"}, "requires access"),
                                   ({"formats":[{"url":"https://example.org/f","has_drm":True}]}, "unprotected")]:
                with self.subTest(info=info), self.assertRaisesRegex(ValueError, expected):
                    client.process_ie_result(info)
        with self.assertRaisesRegex(ValueError, "still open"):
            providers.RecordingHlsFD.can_download("#EXTM3U\n#EXTINF:3\ns.ts", {})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            providers.RecordingHlsFD.can_download('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES\n#EXT-X-ENDLIST', {})

    def test_large_upload_and_storage_preflight(self):
        metadata = {"format": "mov,mp4", "duration_ms": 30 * 3600 * 1000}
        with patch.object(imports, "probe", return_value=metadata):
            first = imports.save(io.BytesIO(b"video"), None, "video", "upload", "a")
            repeat = imports.save(io.BytesIO(b"video"), 5, "video", "upload", "a")
        self.assertEqual(first["id"], repeat["id"])
        self.assertEqual(first["duration_ms"], 30 * 3600000)
        # A >1 GiB declared transfer passes preflight on a disk with capacity.
        with patch.object(imports.shutil, "disk_usage", return_value=MagicMock(free=10*1024**3)):
            with self.assertRaisesRegex(ValueError, "ended early"):
                imports.save(io.BytesIO(), 2*1024**3, "long.mov", "upload", "a")
        with patch.object(imports.shutil, "disk_usage", return_value=MagicMock(free=imports.RESERVE+1)):
            with self.assertRaisesRegex(ValueError, "disk space"):
                imports.save(io.BytesIO(b"video"), None, "file", "upload", "a")
        self.assertFalse(list(self.root.glob("*.upload")))
