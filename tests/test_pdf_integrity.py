import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image
from pypdf import PdfWriter

from portal.file_worker import build_pdf
from portal.services.documents import build_claim_pdf, run_document_process, temporary_workspace

pytestmark = pytest.mark.django_db


def test_multipage_receipt_content_and_order_survive_compression(tmp_path):
    source = tmp_path / "receipt.pdf"
    pages = [Image.new("RGB", (1000, 1400), color) for color in ["#e0ffe8", "#fff1d5", "#e0f0ff"]]
    pages[0].save(source, "PDF", save_all=True, append_images=pages[1:])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "html": "<h1>Summary</h1>",
                "allowed_assets": [],
                "receipts": [
                    {"path": str(source), "media_type": "application/pdf", "label": "Receipt"}
                ],
                "reference": "synthetic",
                "max_bytes": 1_000_000,
            }
        )
    )
    output = tmp_path / "complete.pdf"
    build_pdf(manifest, output)
    expected = [(224, 255, 232), (255, 241, 213), (224, 240, 255)]
    with pdfium.PdfDocument(output) as document:
        assert len(document) == 4
        for index, color in enumerate(expected, 1):
            page = document[index]
            bitmap = page.render(scale=0.5)
            image = bitmap.to_pil().convert("RGB")
            actual = image.getpixel((image.width // 2, image.height // 2))
            assert max(abs(a - b) for a, b in zip(actual, color, strict=True)) < 8, (index, actual)
            bitmap.close()
            page.close()


def test_summary_text_stays_inside_page_margins(accepted):
    with temporary_workspace() as workspace:
        output, _ = build_claim_pdf(accepted, workspace)
        with pdfium.PdfDocument(output) as document:
            page = document[0]
            text = page.get_textpage()
            boxes = [text.get_charbox(index) for index in range(text.count_chars())]
            assert max(box[2] for box in boxes) <= page.get_width() - 30
            text.close()
            page.close()


def test_maximum_receipt_page_count_survives_confined_assembly(tmp_path):
    receipts = []
    for index, count in enumerate([30, 30, 30, 10]):
        source = tmp_path / f"receipt-{index}.pdf"
        writer = PdfWriter()
        for _ in range(count):
            writer.add_blank_page(595, 842)
        writer.write(source)
        receipts.append(
            {"path": str(source), "media_type": "application/pdf", "label": f"Receipt {index}"}
        )
    with temporary_workspace() as workspace:
        manifest = Path(workspace) / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "html": "<h1>Synthetic maximum-page claim</h1>",
                    "allowed_assets": [],
                    "receipts": receipts,
                    "reference": "synthetic",
                    "max_bytes": 10_000_000,
                }
            )
        )
        output = manifest.parent / "complete.pdf"
        result = run_document_process("build", manifest, output)
        assert result["pages"] == 101
        assert 0 < output.stat().st_size <= 10_000_000
        with pdfium.PdfDocument(output) as document:
            assert len(document) == 101
