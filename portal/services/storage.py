import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings


def path_for(key):
    root = settings.PRIVATE_STORAGE_ROOT.resolve()
    target = (root / key).resolve()
    if not key or not target.is_relative_to(root) or target == root:
        raise ValueError("Invalid private storage key")
    return target


def storage_available():
    return shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free >= settings.MIN_FREE_STORAGE_BYTES


def save_upload(file, key, limit):
    target = path_for(key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size, checksum = 0, hashlib.sha256()
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix="incoming-")
    try:
        with os.fdopen(handle, "wb") as output:
            for chunk in file.chunks():
                size += len(chunk)
                if size > limit:
                    raise ValueError("file_too_large")
                output.write(chunk)
                checksum.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return size, checksum.hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_keys(*keys):
    for key in set(filter(None, keys)):
        path_for(key).unlink(missing_ok=True)
