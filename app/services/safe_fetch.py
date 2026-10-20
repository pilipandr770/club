"""Безопасная загрузка чужих страниц для проверок сайтов.

Защита от SSRF: только http/https на портах 80/443, только публичные IP (проверяются для каждого перехода по
редиректу), соединение идёт на уже проверенный IP (защита от подмены DNS между проверкой и запросом),
лимиты по размеру, времени и числу редиректов. Ответ сервера — всегда недоверенные данные.
"""

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (compatible; ClubSiteCheck/1.0)"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 8
MAX_REDIRECTS = 4


class FetchError(Exception):
    """Код ошибки (не текст): подпись для пользователя подбирает вызывающий код."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class Fetched:
    status: int
    headers: dict
    body: bytes
    url: str
    elapsed: float
    cookies: list = field(default_factory=list)  # сырые значения Set-Cookie
    tls_version: str = ""

    @property
    def charset(self):
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype.lower():
            return ctype.lower().split("charset=")[1].split(";")[0].strip() or "utf-8"
        return "utf-8"

    @property
    def text(self):
        try:
            return self.body.decode(self.charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise FetchError("empty")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or " " in raw:
        raise FetchError("invalid")
    if parsed.username or parsed.password:
        raise FetchError("credentials")
    if parsed.port not in (None, 80, 443):
        raise FetchError("port")
    return raw[:500]


def public_ips(host: str) -> list:
    """Все адреса хоста; если хоть один не публичный — отказ."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("dns") from None
    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("private")
        addresses.append(str(ip))
    if not addresses:
        raise FetchError("dns")
    return addresses


def _connect(parsed, ip: str):
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = socket.create_connection((ip, port), timeout=TIMEOUT)
    if parsed.scheme != "https":
        return sock, ""
    context = ssl.create_default_context()
    try:
        tls = context.wrap_socket(sock, server_hostname=parsed.hostname)
    except ssl.SSLError:
        sock.close()
        raise FetchError("tls") from None
    return tls, tls.version() or ""


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, parsed, ip):
        super().__init__(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), timeout=TIMEOUT)
        self._parsed, self._ip, self.tls_version = parsed, ip, ""

    def connect(self):
        try:
            self.sock, self.tls_version = _connect(self._parsed, self._ip)
        except OSError:
            raise FetchError("unreachable") from None


def fetch(url: str, max_bytes: int = MAX_BYTES) -> Fetched:
    """GET с ручным следованием редиректам; каждый переход проверяется на публичные IP."""
    hops = 0
    while True:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise FetchError("invalid")
        if parsed.port not in (None, 80, 443):
            raise FetchError("port")
        ips = public_ips(parsed.hostname)
        started = time.monotonic()
        conn = _PinnedConnection(parsed, ips[0])
        try:
            path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            conn.request("GET", path, headers={"Host": parsed.netloc, "User-Agent": UA,
                                               "Accept": "text/html,*/*;q=0.5", "Connection": "close"})
            response = conn.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            cookies = response.msg.get_all("Set-Cookie") or []
            location = headers.get("location")
            if response.status in (301, 302, 303, 307, 308) and location:
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise FetchError("redirects")
                url = urljoin(url, location)
                continue
            body = response.read(max_bytes + 1)[:max_bytes]
            return Fetched(response.status, headers, body, url, time.monotonic() - started, cookies,
                           conn.tls_version)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException):
            raise FetchError("unreachable") from None
        finally:
            conn.close()

