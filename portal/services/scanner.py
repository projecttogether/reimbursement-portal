import socket
import struct

from django.conf import settings

from portal.services.documents import ProcessingError


def scan_file(path):
    if settings.UPLOAD_SCANNER == "disabled" and settings.DEBUG:
        return
    if settings.UPLOAD_SCANNER != "clamav":
        raise ProcessingError("scanner_configuration")
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=30
        ) as connection:
            connection.sendall(b"zINSTREAM\0")
            with open(path, "rb") as source:
                while chunk := source.read(64 * 1024):
                    connection.sendall(struct.pack("!I", len(chunk)) + chunk)
            connection.sendall(struct.pack("!I", 0))
            response = b""
            while b"\0" not in response and len(response) < 4096:
                part = connection.recv(4096)
                if not part:
                    break
                response += part
    except OSError as exc:
        raise ProcessingError("scanner_unavailable") from exc
    if response.strip(b"\0\r\n") == b"stream: OK":
        return
    if b" FOUND" in response:
        raise ProcessingError("malware_detected")
    raise ProcessingError("scanner_unavailable")
