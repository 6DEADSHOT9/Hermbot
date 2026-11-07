"""Actual binary/library preview fixtures, without model responses or external documents."""

import base64
import io
import json
from pathlib import Path
import zipfile
from .phase2_acceptance import request


def pdf():
    content = b"BT /F1 24 Tf 40 160 Td (Phase 3 document preview) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 220] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for i, value in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{i} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(result)
    result += (
        f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
        + b"".join(f"{o:010} 00000 n \n".encode() for o in offsets[1:])
        + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return result


def office(kind):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        if kind == "docx":
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Phase 3 office document preview</w:t></w:r></w:p></w:body></w:document>',
            )
        else:
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                "<worksheet><sheetData><row><c><v>42</v></c></row></sheetData></worksheet>",
            )
    return buffer.getvalue()


def main():
    files = [
        ("phase3-preview.pdf", "application/pdf", pdf()),
        (
            "phase3-preview.png",
            "image/png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1sAAAAASUVORK5CYII="
            ),
        ),
        ("phase3-preview.py", "text/x-python", b'print("Phase 3 code preview")\n'),
        (
            "phase3-preview.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            office("docx"),
        ),
        (
            "phase3-preview.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            office("xlsx"),
        ),
    ]
    receipts = []
    for name, mime, data in files:
        receipt = request(
            "POST",
            "/api/v1/library",
            {"name": name, "content_type": mime, "content_base64": base64.b64encode(data).decode()},
        )
        from .protocol_acceptance import client

        downloaded = client.get("/api/v1/artifacts/" + receipt["id"] + "?download=true")
        assert downloaded.content == data and downloaded.headers["x-content-type-options"] == "nosniff"
        receipts.append({"id": receipt["id"], "name": name, "content_type": mime, "download_verified": True})
    Path("/app/test-results/phase3-previews.json").write_text(json.dumps(receipts, indent=2))
    print("Binary, document, office and code library downloads verified.")


if __name__ == "__main__":
    main()
