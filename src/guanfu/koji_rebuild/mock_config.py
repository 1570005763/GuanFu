import subprocess
import shutil
import urllib.error
import urllib.request
from pathlib import Path


def historical_repo_url(koji_topurl, buildroot, repo_url_template=None):
    topurl = koji_topurl.rstrip("/")
    if repo_url_template:
        values = dict(buildroot or {})
        values["topurl"] = topurl
        return repo_url_template.format(**values)
    return (
        f"{topurl}/repos/{buildroot['tag_name']}/"
        f"{buildroot['repo_id']}/{buildroot['arch']}"
    )


def probe_repodata(koji_topurl, buildroot, ssl_context=None, repo_url_template=None):
    repo_url = historical_repo_url(koji_topurl, buildroot, repo_url_template=repo_url_template)
    repomd_url = f"{repo_url}/repodata/repomd.xml"
    try:
        request = urllib.request.Request(repomd_url, method="GET")
        kwargs = {"timeout": 20}
        if ssl_context is not None:
            kwargs["context"] = ssl_context
        with urllib.request.urlopen(request, **kwargs) as response:
            response.read(64)
            return {"url": repomd_url, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"url": repomd_url, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"url": repomd_url, "status": None, "error": repr(exc)}


def generate_mock_config(koji_server, koji_topurl, buildroot_id, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    koji = shutil.which("koji")
    if not koji:
        raise RuntimeError(
            "koji CLI is required to generate mock.cfg. Please install koji "
            "(for example: dnf install koji, or apt install koji)."
        )
    cmd = [
        koji,
        f"--server={koji_server}",
        "mock-config",
        f"--buildroot={buildroot_id}",
        f"--topurl={koji_topurl}",
        "-o",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest
