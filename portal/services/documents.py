import contextlib
import fcntl
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string

from portal.models import Upload, WorkspaceReservation
from portal.services.capacity import (
    CapacityError,
    check_storage,
    lock_storage,
    total,
    workspace_uses_private_storage,
)
from portal.services.storage import (
    new_workspace_key,
    normalized_upload_key,
    path_for,
    publish_file,
    workspace_root,
)


class ProcessingError(Exception):
    pass


def resource_limits():
    resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    resource.setrlimit(
        resource.RLIMIT_CPU, (settings.JOB_TIMEOUT_SECONDS, settings.JOB_TIMEOUT_SECONDS)
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (600_000_000, 600_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def run_document_process(action, source, destination, *, kind="receipt"):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    workspace = destination if action == "validate" else destination.parent
    readonly = [str(source)]
    if action == "build":
        manifest = json.loads(source.read_text())
        readonly += manifest["allowed_assets"] + [item["path"] for item in manifest["receipts"]]
    arguments = [action, str(source), str(destination), "--kind", kind]
    if settings.DOCUMENT_SANDBOX_ENABLED:
        policy = workspace / "sandbox.json"
        policy.write_text(json.dumps({"readonly": readonly, "workspace": str(workspace)}))
        command = [sys.executable, "-m", "portal.document_sandbox", str(policy), *arguments]
    else:
        if not settings.DEBUG:
            raise ProcessingError("document_sandbox_unavailable")
        command = [sys.executable, "-m", "portal.file_worker", *arguments]
    with (
        workspace_lock(workspace) as execution_lock,
        (workspace / "process-result.json").open("w+b") as output,
    ):
        try:
            process = subprocess.Popen(
                command,
                cwd=settings.BASE_DIR,
                env={
                    "PATH": os.defpath,
                    "LANG": "C.UTF-8",
                    "TMPDIR": str(workspace),
                    "XDG_CACHE_HOME": str(workspace / "cache"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdout=output,
                stderr=subprocess.DEVNULL,
                preexec_fn=resource_limits,
                start_new_session=True,
                # Retain the execution lock if the supervisor dies before this child.
                pass_fds=(execution_lock.fileno(),),
            )
            try:
                process.wait(timeout=settings.JOB_TIMEOUT_SECONDS)
            finally:
                # Descendants inherit confinement and cannot change process groups.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            output.seek(0)
            stdout = output.read(65_537)
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError("document_timeout") from exc
    if len(stdout) > 65_536:
        raise ProcessingError("document_process_failed")
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ProcessingError("document_process_failed") from exc
    if process.returncode or "error" in payload:
        raise ProcessingError(payload.get("error", "document_process_failed"))
    return payload


@contextlib.contextmanager
def validate_upload(upload):
    source = path_for(upload.source_key)
    with temporary_workspace(upload=upload) as workspace:
        result = run_document_process("validate", source, workspace, kind=upload.kind)
        normalized = Path(result.pop("normalized")).resolve()
        if result.get("media_type") == "application/pdf" and normalized == source:
            result.update(normalized_key=upload.source_key, normalized_size=0)
            yield result
            return
        suffix = ".png" if upload.kind == "signature" else ".jpg"
        expected = Path(workspace) / f"normalized{suffix}"
        if normalized != expected or not normalized.is_file() or normalized.is_symlink():
            raise ProcessingError("invalid_processing_output")
        size = normalized.stat().st_size
        if not 0 < size <= settings.MAX_NORMALIZED_FILE_BYTES:
            raise ProcessingError("normalized_size_limit")
        key = normalized_upload_key(upload.pk, suffix)
        publish_file(normalized, key, settings.MAX_NORMALIZED_FILE_BYTES)
        result.update(normalized_key=key, normalized_size=size)
        yield result


def build_claim_pdf(claim, workspace):
    uploads = list(claim.uploads.all())
    signature = next(upload for upload in uploads if upload.kind == "signature")
    signature_path = path_for(signature.normalized_key)
    expenses = list(claim.expenses.all())
    receipts = []
    for expense in expenses:
        for upload in sorted(
            (
                upload
                for upload in uploads
                if upload.expense_id == expense.id and upload.kind == "receipt"
            ),
            key=lambda upload: upload.position,
        ):
            receipts.append(
                {
                    "path": str(path_for(upload.normalized_key)),
                    "media_type": upload.media_type,
                    "label": f"Position {expense.position + 1} · {expense.receipt_reference}",
                }
            )
    manifest = {
        "html": render_to_string(
            "portal/summary.html",
            {"claim": claim, "expenses": expenses, "signature_uri": signature_path.as_uri()},
        ),
        "allowed_assets": [str(signature_path)],
        "receipts": receipts,
        "reference": claim.reference,
        "max_bytes": settings.MAX_FINAL_PDF_BYTES,
    }
    manifest_path = Path(workspace) / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = Path(workspace) / "complete.pdf"
    result = run_document_process("build", manifest_path, output_path)
    return output_path, result


@contextlib.contextmanager
def workspace_lock(directory, *, exclusive=False):
    directory = Path(directory)
    locks = directory.parent / ".locks"
    locks.mkdir(exist_ok=True, mode=0o700)
    # Outside the parser's writable subtree, so it cannot unlink/replace the lock.
    lock_path = locks / directory.name
    with lock_path.open("a+b") as handle:
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle, operation)
        yield handle


def remove_abandoned_workspace(reservation):
    directory = path_for(reservation.directory)
    directory.parent.mkdir(exist_ok=True, mode=0o700)
    try:
        with workspace_lock(directory, exclusive=True):
            if directory.exists():
                shutil.rmtree(directory)
            with transaction.atomic():
                lock_storage()
                if reservation.upload_id:
                    current = Upload.objects.select_for_update().get(pk=reservation.upload_id)
                    if (
                        current.state == Upload.State.PENDING
                        and not WorkspaceReservation.objects.filter(upload=current)
                        .exclude(pk=reservation.pk)
                        .exists()
                    ):
                        # A crashed validator still owes its admitted output capacity.
                        current.reserved_bytes = max(
                            current.reserved_bytes, reservation.output_bytes
                        )
                        current.save(update_fields=["reserved_bytes"])
                reservation.delete()
            (directory.parent / ".locks" / directory.name).unlink(missing_ok=True)
        return True
    except BlockingIOError:
        # An expired job lease does not prove that its parser has stopped.
        return False


@contextlib.contextmanager
def temporary_workspace(*, upload=None, claim=None):
    root = workspace_root()
    root.mkdir(exist_ok=True, mode=0o700)
    key = new_workspace_key()
    directory = path_for(key)
    size = settings.MAX_NORMALIZED_FILE_BYTES if upload else settings.MAX_RENDER_WORKSPACE_BYTES
    output_size = settings.MAX_NORMALIZED_FILE_BYTES if upload else settings.MAX_FINAL_PDF_BYTES
    persistent_size = output_size + (size if workspace_uses_private_storage() else 0)
    reservation = None
    with workspace_lock(directory):
        try:
            with transaction.atomic():
                # Transfer pending upload capacity into an execution-specific reservation.
                check_storage(0)
                current = Upload.objects.select_for_update().get(pk=upload.pk) if upload else None
                check_storage(max(0, persistent_size - (current.reserved_bytes if current else 0)))
                if (
                    shutil.disk_usage(root).free
                    < total(WorkspaceReservation.objects.all(), "size") + size
                ):
                    raise CapacityError("workspace_capacity")
                reservation = WorkspaceReservation.objects.create(
                    directory=key, size=size, output_bytes=output_size, upload=upload, claim=claim
                )
                if current:
                    current.reserved_bytes = 0
                    current.save(update_fields=["reserved_bytes"])
                directory.mkdir(mode=0o700)
            yield str(directory)
        finally:
            if directory.exists():
                shutil.rmtree(directory)
            if reservation:
                reservation.delete()
            (root / ".locks" / directory.name).unlink(missing_ok=True)
