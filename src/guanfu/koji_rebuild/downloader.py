import base64
import hashlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from pathlib import Path


class TLSConfig:
    def __init__(self, mode, ssl_context=None):
        self.mode = mode
        self.ssl_context = ssl_context


def build_tls_config(urls, ca_cert=None, insecure=False):
    urls = [url for url in (urls or []) if url]
    uses_https = any(urllib.parse.urlparse(str(url)).scheme == "https" for url in urls)
    if not uses_https:
        return TLSConfig("plain-http")
    if insecure:
        return TLSConfig("insecure", ssl._create_unverified_context())
    if ca_cert:
        return TLSConfig("custom-ca", ssl.create_default_context(cafile=str(ca_cert)))
    return TLSConfig("system-ca", ssl.create_default_context())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize_file(path, label=None, url=None):
    path = Path(path)
    summary = {
        "file": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if label:
        summary["label"] = label
    if url:
        summary["url"] = url
    return summary


def join_url(base_url, filename):
    if not base_url.endswith("/"):
        base_url += "/"
    return urllib.parse.urljoin(base_url, urllib.parse.quote(filename))


def download_url(url, dest, ssl_context=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "guanfu-koji-rebuild/0.1"})
    kwargs = {"timeout": 60}
    if ssl_context is not None:
        kwargs["context"] = ssl_context
    with urllib.request.urlopen(request, **kwargs) as response:
        with open(dest, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    return dest


def try_download_url(url, dest, ssl_context=None):
    try:
        return download_url(url, dest, ssl_context=ssl_context), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return None, repr(exc)


def _decode_xmlrpc_chunk(chunk):
    if isinstance(chunk, xmlrpc.client.Binary):
        return chunk.data
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, str):
        return base64.b64decode(chunk)
    raise TypeError(f"unexpected download chunk type: {type(chunk)!r}")


def download_task_output(client, task_id, filename, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    size = 1024 * 1024
    with open(dest, "wb") as f:
        while True:
            chunk = client.download_task_output(task_id, filename, offset, size)
            data = _decode_xmlrpc_chunk(chunk)
            if not data:
                break
            f.write(data)
            offset += len(data)
            if len(data) < size:
                break
    return dest
