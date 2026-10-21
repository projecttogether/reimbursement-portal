"""Read-only consistency check for release and isolated restore verification."""

import hashlib
import json

from django.core.management.base import BaseCommand, CommandError

from portal.models import Claim, Upload
from portal.services.storage import path_for


class Command(BaseCommand):
    help = "Verify database/file consistency without printing participant data."

    def handle(self, *args, **options):
        checked, failures = 0, 0
        for upload in Upload.objects.all().iterator(chunk_size=100):
            for key in set(filter(None, [upload.source_key, upload.normalized_key])):
                checked += 1
                path = path_for(key)
                if not path.is_file():
                    failures += 1
                elif key == upload.source_key:
                    with path.open("rb") as original:
                        if (
                            path.stat().st_size != upload.size
                            or hashlib.file_digest(original, "sha256").hexdigest()
                            != upload.checksum
                        ):
                            failures += 1
                elif path.stat().st_size != upload.normalized_size:
                    failures += 1
        for claim in Claim.objects.exclude(pdf_key="").iterator(chunk_size=100):
            checked += 1
            path = path_for(claim.pdf_key)
            if not path.is_file() or path.stat().st_size != claim.pdf_size:
                failures += 1
            else:
                with path.open("rb") as pdf:
                    if hashlib.file_digest(pdf, "sha256").hexdigest() != claim.pdf_hash:
                        failures += 1
        self.stdout.write(json.dumps({"files_checked": checked, "failures": failures}))
        if failures:
            raise CommandError("Private storage verification failed.")
