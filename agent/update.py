"""Self-update from the hub: signed manifest, per-file SHA-256, staged then swapped in.

The manifest is signed with the hub's Ed25519 key, which the agent received during pairing, so a
compromised network (or a hub impersonator without that key) can't push code.
"""
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric import ed25519

from .state import APP_DIR, version

log = logging.getLogger(__name__)


def verify(public_b64, manifest, signature_b64):
    key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    try:
        key.verify(base64.b64decode(signature_b64), manifest.encode())
        return True
    except Exception:
        return False


def _newer(remote, local):
    def parts(v):
        return [int(p) if p.isdigit() else 0 for p in v.split(".")]
    return parts(remote) > parts(local)


async def check(request, state, platform_name):
    """Download and stage a newer version if the hub has one. Returns the staging dir or None.

    request(msg) sends a request to the hub and returns its reply.
    """
    reply = await request({"type": "get_update", "platform": platform_name})
    if reply.get("type") != "update":
        return None
    if not verify(state.hub["sign_public_key"], reply["manifest"], reply["signature"]):
        log.error("update manifest signature invalid; ignoring")
        return None
    manifest = json.loads(reply["manifest"])
    if manifest.get("platform") != platform_name or not _newer(manifest["version"], version()):
        return None

    staging = state.dir / "update"
    shutil.rmtree(staging, ignore_errors=True)
    for entry in manifest["files"]:
        rel = entry["path"]
        if rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
            log.error("update has a bad path %r; ignoring", rel)
            return None
        local = APP_DIR / rel
        if local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == entry["sha256"]:
            continue  # unchanged
        data, offset = b"", 0
        while offset < entry["size"]:
            chunk = await request({"type": "get_file", "platform": platform_name, "path": rel, "offset": offset})
            piece = base64.b64decode(chunk["data"])
            if not piece:
                break
            data += piece
            offset += len(piece)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            log.error("update file %s failed its checksum; ignoring update", rel)
            return None
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    log.info("update %s -> %s staged", version(), manifest["version"])
    return staging


def apply_and_restart(staging):
    """Copy staged files over the install and start the new version (this process then exits).

    On Windows the running interpreter's files are locked, so a detached PowerShell waits for us to
    exit, copies, and relaunches.
    """
    if sys.platform == "win32":
        pythonw = APP_DIR / "python" / "pythonw.exe"
        log_path = staging.parent / "update.log"
        script = staging.parent / "apply-update.ps1"
        # robocopy merges folders reliably (Copy-Item -Recurse nests into existing ones); exit codes
        # below 8 mean success. Everything is logged, so a failed update leaves evidence.
        script.write_text(f"""$ErrorActionPreference = 'Continue'
Start-Transcript -Path '{log_path}' -Append | Out-Null
Wait-Process -Id {os.getpid()} -Timeout 30 -ErrorAction SilentlyContinue
robocopy '{staging}' '{APP_DIR}' /E /R:5 /W:1 /NFL /NDL /NJH /NJS
if ($LASTEXITCODE -lt 8) {{ Remove-Item -Recurse -Force '{staging}' }} else {{ Write-Output "robocopy failed: $LASTEXITCODE" }}
Start-Process '{pythonw}' -ArgumentList '-m','agent','run' -WorkingDirectory '{APP_DIR}'
Stop-Transcript | Out-Null
""", encoding="utf-8-sig")
        # CREATE_NO_WINDOW runs the console program invisibly; DETACHED_PROCESS (no console at all) can
        # stop PowerShell from starting. NEW_PROCESS_GROUP keeps it alive when we exit.
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         creationflags=0x08000000 | 0x00000200, close_fds=True)
    else:
        shutil.copytree(staging, APP_DIR, dirs_exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        os.execv(sys.executable, [sys.executable, "-m", "agent", "run"])
    os._exit(0)
