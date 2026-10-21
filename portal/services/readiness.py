"""Protected operational checks with no provider calls or participant content."""

import os
import re
import socket
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from portal.models import Job


def bounded_workspace():
    root = (settings.PRIVATE_STORAGE_ROOT / "work").resolve()
    if not root.is_dir():
        return False
    try:
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields, filesystem = line.split(" - ", 1)
            mount = Path(
                re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields.split()[4])
            )
            if root.is_relative_to(mount):
                mounts.append((len(mount.parts), filesystem.split()[0]))
        filesystem = os.statvfs(root)
        return (
            max(mounts)[1] == "tmpfs"
            and root.stat().st_dev != settings.PRIVATE_STORAGE_ROOT.stat().st_dev
            and root.stat().st_mode & 0o077 == 0
            and root.stat().st_uid == os.getuid()
            and filesystem.f_blocks * filesystem.f_frsize <= 750_000_000
        )
    except (OSError, ValueError):
        return False


def sandbox_operational(root):
    try:
        return successful_sandbox_probe(root)
    except (OSError, subprocess.TimeoutExpired):
        return False


@lru_cache(maxsize=16)
def successful_sandbox_probe(root):
    # lru_cache retains successes only. Exceptions leave transient failures retryable.
    Path(root).mkdir(exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=root, prefix="job-probe-") as workspace:
        result = subprocess.run(
            [sys.executable, "-m", "portal.document_sandbox", "--self-test", workspace],
            cwd=settings.BASE_DIR,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0 or result.stdout.strip() != b"sandbox_ok":
            raise OSError("Document sandbox probe failed")
    return True


def queue_age_seconds():
    now = timezone.now()
    oldest = (
        Job.objects.filter(Q(state="pending", run_after__lte=now) | Q(state="running"))
        .order_by("run_after")
        .values_list("run_after", flat=True)
        .first()
    )
    # A retry starts waiting when its backoff ends, regardless of original creation.
    return max(0, int((now - oldest).total_seconds())) if oldest else 0


def dependencies():
    result = {
        "sandbox": not settings.DOCUMENT_SANDBOX_ENABLED
        or sandbox_operational(str(settings.PRIVATE_STORAGE_ROOT / "work"))
    }
    if not settings.DEBUG:
        result["workspace"] = bounded_workspace()
    if settings.REQUIRE_WORKER_HEARTBEAT:
        try:
            age = (
                timezone.now().timestamp()
                - (settings.PRIVATE_STORAGE_ROOT / "worker-heartbeat").stat().st_mtime
            )
            result["worker"] = 0 <= age < settings.JOB_LEASE_SECONDS + 60
        except OSError:
            result["worker"] = False
    if settings.UPLOAD_SCANNER == "clamav":
        try:
            with socket.create_connection(
                (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=2
            ) as scanner:
                scanner.sendall(b"zPING\0")
                result["scanner"] = scanner.recv(32).strip(b"\0\r\n") == b"PONG"
        except OSError:
            result["scanner"] = False
    age = queue_age_seconds()
    result["queue"] = age <= settings.MAX_JOB_AGE_SECONDS
    return result, age
