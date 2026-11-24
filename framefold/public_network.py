"""TLS connections pinned to a validated public address for the entire request."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit


def https_url(url):
    if not isinstance(url, str) or not url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("Paste a public HTTPS recording URL.")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                 and parsed.password is None and parsed.port in (None, 443)
                 and "\\" not in parsed.netloc and "%" not in parsed.netloc)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Paste a public HTTPS recording URL without embedded credentials or a custom port.")
    return parsed


def public_addresses(host):
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = []
    for _, _, _, _, address in answers:
        ip = ipaddress.ip_address(address[0])
        if isinstance(ip, ipaddress.IPv6Address):
            if (ip.ipv4_mapped or ip.sixtofour or ip.teredo
                    or ip in ipaddress.ip_network("64:ff9b::/96")
                    or ip in ipaddress.ip_network("64:ff9b:1::/48")):
                raise ValueError("The link must point to a public video host.")
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError("The link must point to a public video host.")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise ValueError("The video host could not be resolved.")
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host or self.port != 443:
            raise ValueError("The link must point directly to a public HTTPS host.")
        # Send SNI and verify the certificate for the original hostname, but dial
        # only an already validated numeric IP (including on every redirect).
        addresses = public_addresses(self.host)
        for idx, address in enumerate(addresses):
            try:
                raw = socket.create_connection((address, 443), self.timeout)
            except OSError:
                if idx + 1 == len(addresses):
                    raise
                continue
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            except BaseException:
                raw.close()
                raise
            return


def direct_connection(host):
    return PublicHTTPSConnection(host.encode("idna").decode(), timeout=30, context=ssl.create_default_context())
