"""Extract one finished recording and transfer its tracks over public-only HTTPS."""
import shutil
import urllib.request
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.dependencies import Cryptodome
from yt_dlp.downloader.dash import DashSegmentsFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.downloader.http import HttpFD
from yt_dlp.networking._urllib import RedirectHandler, UrllibRH, UrllibResponseAdapter
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import DownloadError, determine_protocol

from .imports import BLOCK, require_space
from .media import FFMPEG, INPUT, command
from .public_network import PublicHTTPSConnection, https_url


# These extractors use the request director. Generic also discovers HTML video,
# player embeds and manifests on other public sites. Synthetic search schemes,
# authenticated sessions and external downloader programs aren't import methods.
EXTRACTORS = ["youtube", "youtubeytbe", "twitch:vod", "twitch:clips", "twitch:stream",
              "kick:vod", "kick:clip", "kick:live", "rumble", "rumbleembed", "vimeo",
              "dailymotion", "streamable", "tedtalk", "tedembed", r"archive\.org",
              "tiktok", r"vm\.tiktok", "facebook", "facebook:reel", "facebookpluginsvideo",
              "facebookredirecturl", "twitter", "twitter:card", "twitter:shortener", "instagram",
              "reddit", "bilibili", "bilibiliplayer", "bluesky", "peertube", "generic"]
SUPPORTED = {"https", "m3u8", "m3u8_native", "http_dash_segments"}
RESTRICTED = {"private", "premium_only", "subscriber_only", "needs_auth"}


class PublicHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        https_url(req.full_url)
        for name in ("Host", "Proxy-authorization", "Proxy-connection"):
            req.remove_header(name)
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class PublicRedirect(RedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        origin = https_url(req.full_url)
        destination = https_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if origin.hostname != destination.hostname:
            redirected.remove_header("Authorization")
        return redirected


class PublicResponse(UrllibResponseAdapter):
    def read(self, amt=None):
        if amt is not None and amt >= 0:
            return super().read(amt)
        # Extractors read pages/manifests/keys in a single call. Media transfers
        # use explicitly sized reads, so their total duration and size are open.
        limit = 32 * 1024**2
        content = super().read(limit + 1)
        if len(content) > limit:
            self.close()
            raise ValueError("The provider's recording metadata is too large to read.")
        return content


class PublicUrllibRH(UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("https",)
    _SUPPORTED_PROXY_SCHEMES = ()

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        opener = urllib.request.OpenerDirector()
        for handler in (PublicHandler(context=self._make_sslcontext()), PublicRedirect(),
                        urllib.request.HTTPCookieProcessor(cookiejar),
                        urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(),
                        urllib.request.UnknownHandler()):
            opener.add_handler(handler)
        opener.addheaders = []
        return opener

    def _send(self, request):
        response = super()._send(request)
        return PublicResponse(response)

    def _prepare_headers(self, request, headers):
        # Keep binary media streaming and avoid automatic whole-body inflation.
        headers["Accept-Encoding"] = "identity"


class RecordingDL(YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        return super().build_request_director([PublicUrllibRH])

    def urlopen(self, req):
        url = req if isinstance(req, str) else (req.url if hasattr(req, "url") else req.full_url)
        https_url(url)
        return super().urlopen(req)

    def process_ie_result(self, result, download=False, extra_info=None):
        kind = result.get("_type", "video")
        if kind in ("playlist", "multi_video", "compat_list"):
            raise ValueError("Paste a link to one recording from the playlist or channel.")
        if kind == "video":
            if result.get("is_live") or result.get("live_status") in ("is_live", "is_upcoming"):
                raise ValueError("This stream is live or upcoming. Paste its recording when the broadcast ends.")
            if result.get("availability") in RESTRICTED:
                raise ValueError("This recording requires access. Paste a public recording or upload your original.")
            if result.get("formats"):
                result["formats"] = [f for f in result["formats"] if f.get("url")
                                     and not f.get("has_drm") and determine_protocol(f) in SUPPORTED]
                if not result["formats"]:
                    raise ValueError("This recording has no supported public, unprotected media track.")
        return super().process_ie_result(result, download=False, extra_info=extra_info)


class StreamingFragments:
    # Native yt-dlp normally loads each entire downloaded segment into memory.
    # A valid single-segment VOD can be as large as the complete recording.
    def _read_fragment(self, ctx):
        filename = ctx.get("fragment_filename_sanitized")
        return Path(filename) if filename else None

    def decrypter(self, info_dict):
        def prepare(fragment, path):
            if path is None:
                return None
            crypto = fragment.get("decrypt_info") or {}
            if crypto.get("METHOD") != "AES-128":
                return path, None
            key = crypto.get("KEY")
            if not key:
                key_url = (info_dict.get("hls_aes") or {}).get("uri") or crypto["URI"]
                with self.ydl.urlopen(self._prepare_url(info_dict, key_url)) as response:
                    key = response.read(17)
                if len(key) != 16:
                    raise ValueError("The HLS media key has an invalid size.")
                crypto["KEY"] = key
            iv = crypto.get("IV") or fragment["media_sequence"].to_bytes(16, "big")
            return path, Cryptodome.AES.new(key, Cryptodome.AES.MODE_CBC, iv)
        return prepare

    def _append_fragment(self, ctx, content):
        path, cipher = content
        require_space(path.parent, path.stat().st_size)
        with path.open("rb") as source:
            if cipher is None:
                shutil.copyfileobj(source, ctx["dest_stream"], BLOCK)
            else:
                # RFC 8216 uses AES-CBC and PKCS#7 at the segment boundary.
                # Keep one block of lookahead so only the last chunk is unpadded.
                from Cryptodome.Util.Padding import unpad
                block = source.read(BLOCK)
                while block:
                    following = source.read(BLOCK)
                    decoded = cipher.decrypt(block)
                    ctx["dest_stream"].write(decoded if following else unpad(decoded, 16))
                    block = following
        super()._append_fragment(ctx, b"")


class RecordingDashFD(StreamingFragments, DashSegmentsFD):
    pass


class RecordingHlsFD(StreamingFragments, HlsFD):
    @classmethod
    def can_download(cls, manifest, info_dict, allow_unplayable_formats=False):
        if "#EXT-X-ENDLIST" not in manifest.splitlines():
            raise ValueError("The HLS stream is still open. Paste a finished recording.")
        if (not super().can_download(manifest, info_dict)
                or (not Cryptodome.AES and "#EXT-X-KEY:METHOD=AES-128" in manifest)):
            # Refuse before HlsFD can fall back to an external network downloader.
            raise ValueError("This HLS recording uses unsupported protection or media features.")
        return True


class QuietLogger:
    def debug(self, *args):
        pass

    warning = error = debug


def download_track(client, track, path):
    protocol = determine_protocol(track)
    downloader = {"https": HttpFD, "http_dash_segments": RecordingDashFD,
                  "m3u8": RecordingHlsFD, "m3u8_native": RecordingHlsFD}.get(protocol)
    if not downloader or track.get("has_drm"):
        raise ValueError("This recording has no supported public, unprotected media track.")
    https_url(track["url"])
    require_space(path.parent, track.get("filesize") or 0)
    # Paths and downloader options always come from the application. In
    # particular DashSegmentsFD honors a format's 'filepath' when provided.
    fields = ("id", "url", "ext", "protocol", "http_headers", "fragments", "fragment_base_url",
              "hls_media_playlist_data", "extra_param_to_segment_url", "extra_param_to_key_url",
              "hls_aes", "format_index")
    info = {key: track[key] for key in fields if key in track}
    info["protocol"] = protocol
    fd = downloader(client, client.params)
    fd.add_progress_hook(lambda _: require_space(path.parent))
    if not fd.download(str(path), info)[0] or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("The provider transfer ended early. Retry or upload the recording.")


def recording(url, directory):
    require_space(directory)
    options = {
        "allowed_extractors": EXTRACTORS,
        "format": "b[height>=720][height<=2160]/bv[height<=?2160]+ba/b[height<=?2160]/bv[height<=?2160]",
        "quiet": True, "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
        "noplaylist": True, "cachedir": False, "proxy": "", "socket_timeout": 30,
        "external_downloader": "native", "hls_prefer_native": True, "skip_unavailable_fragments": False,
        "retries": 2, "fragment_retries": 2, "extractor_retries": 1, "buffersize": 65536,
        "noresizebuffer": True, "updatetime": False,
    }
    try:
        with RecordingDL(options) as client:
            info = client.extract_info(url, download=False)
            if not info or info.get("_type", "video") != "video":
                raise ValueError("The provider could not find a finished recording at this URL.")
            if info.get("section_start") or info.get("section_end"):
                raise ValueError("Paste the full recording link or upload the clipped file.")
            tracks = info.get("requested_formats") or [info]
            paths = []
            for index, track in enumerate(tracks):
                path = directory / f"track-{index}"
                download_track(client, {**track, "id": info["id"]}, path)
                paths.append(path)
    except (DownloadError, RequestError, OSError) as exc:
        raise ValueError(provider_error(exc)) from exc

    if len(paths) == 1:
        return paths[0], str(info.get("title") or "Linked recording")
    if len(paths) != 2:
        raise ValueError("The provider returned an unsupported recording layout.")
    # Both inputs are local, fixed-name downloads. Forbid manifests and
    # network protocols before opening either. Stream-copy preserves quality.
    require_space(directory, sum(p.stat().st_size for p in paths))
    merged = directory / "recording.mkv"
    inputs = []
    for path in paths:
        inputs.extend([*INPUT, "-format_whitelist", "mov,matroska,webm,mpegts,aac,ogg,mp3", "-i", str(path)])
    command([FFMPEG, "-v", "error", "-nostdin", *inputs, "-map", "0:v:0", "-map", "1:a:0",
             "-c", "copy", "-map_metadata", "-1", str(merged)], timeout=None)
    return merged, str(info.get("title") or "Linked recording")


def provider_error(exc):
    reason = str(exc).lower()
    if "no space" in reason:
        return "There is insufficient disk space for this recording. Free storage and retry."
    if any(token in reason for token in ("private", "login", "sign in", "403", "429", "blocked", "bot", "forbidden")):
        return "The provider restricted this request. Try another public recording or upload your original."
    if any(token in reason for token in ("not available", "404", "not found", "removed")):
        return "The recording is unavailable from this server. Check the link or upload your original."
    if "drm" in reason or "protected" in reason:
        return "This recording is protected. Upload an unprotected original."
    return "Could not import a public recording from this provider. Check the link, retry, or upload your original."
