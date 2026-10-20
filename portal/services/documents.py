import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from portal.services.storage import path_for


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
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "portal.file_worker",
                action,
                str(source),
                str(destination),
                "--kind",
                kind,
            ],
            cwd=settings.BASE_DIR,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "TMPDIR": str(Path(destination).parent),
            },
            capture_output=True,
            text=True,
            timeout=settings.JOB_TIMEOUT_SECONDS,
            preexec_fn=resource_limits,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError("document_timeout") from exc
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ProcessingError("document_process_failed") from exc
    if result.returncode or "error" in payload:
        raise ProcessingError(payload.get("error", "document_process_failed"))
    return payload


def validate_upload(upload):
    directory = path_for(upload.source_key).parent
    result = run_document_process(
        "validate", path_for(upload.source_key), directory, kind=upload.kind
    )
    normalized = Path(result.pop("normalized")).resolve()
    if (
        not normalized.is_relative_to(settings.PRIVATE_STORAGE_ROOT.resolve())
        or not normalized.is_file()
    ):
        raise ProcessingError("invalid_processing_output")
    result["normalized_key"] = str(normalized.relative_to(settings.PRIVATE_STORAGE_ROOT.resolve()))
    return result


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


def temporary_workspace():
    root = settings.PRIVATE_STORAGE_ROOT / "work"
    root.mkdir(exist_ok=True, mode=0o700)
    return tempfile.TemporaryDirectory(dir=root, prefix="job-")
