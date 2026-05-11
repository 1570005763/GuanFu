import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


CONTAINER_MOCKBUILD_UID = 1000


def historical_repo_url(koji_topurl, buildroot):
    topurl = koji_topurl.rstrip("/")
    return (
        f"{topurl}/repos/{buildroot['tag_name']}/"
        f"{buildroot['repo_id']}/{buildroot['arch']}"
    )


def probe_repodata(koji_topurl, buildroot):
    repo_url = historical_repo_url(koji_topurl, buildroot)
    repomd_url = f"{repo_url}/repodata/repomd.xml"
    try:
        request = urllib.request.Request(repomd_url, method="GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(64)
            return {"url": repomd_url, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"url": repomd_url, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"url": repomd_url, "status": None, "error": repr(exc)}


def generate_mock_config(koji_server, koji_topurl, buildroot_id, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "koji",
        f"--server={koji_server}",
        "mock-config",
        f"--buildroot={buildroot_id}",
        f"--topurl={koji_topurl}",
        "-o",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest


def enforce_nonroot_mock_build_user(
    mock_cfg,
    uid=CONTAINER_MOCKBUILD_UID,
):
    """Keep rpmbuild/%check from running as root inside containerized mock."""
    mock_cfg = Path(mock_cfg)
    text = mock_cfg.read_text()
    text = _upsert_config_opt(text, "chrootuid", uid)
    mock_cfg.write_text(text)
    return mock_cfg


def _upsert_config_opt(text, key, value):
    line = f"config_opts['{key}'] = {value}"
    pattern = r"(?m)^config_opts\[['\"]%s['\"]\]\s*=.*$" % key
    replaced, count = re.subn(pattern, line, text)
    if count:
        return replaced
    if not text.endswith("\n"):
        text += "\n"
    if "# GuanFu container mock user override." not in text:
        text += "\n# GuanFu container mock user override.\n"
    return text + line + "\n"
