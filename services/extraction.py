"""Resume file text extraction for PDF, DOCX and plain text uploads."""

import io
import os
from typing import Tuple

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_TEXT_CHARS = 60_000


def _pdf_text(file_bytes: bytes) -> str:
    import PyPDF2

    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:  # pragma: no cover - depends on malformed files
        raise ValueError(f"Unable to read PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        raise ValueError("Password-protected PDFs are not supported. Upload an unlocked copy.")

    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append(text.strip())
    extracted = "\n\n".join(pages).strip()
    if not extracted:
        raise ValueError(
            "No selectable text was found in this PDF. Scanned PDFs need OCR, which is not supported."
        )
    return extracted


def _docx_text(file_bytes: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise ValueError(
            "DOCX support is not installed. Run: python -m pip install python-docx"
        ) from exc

    try:
        document = docx.Document(io.BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError(f"Unable to read DOCX: {exc}") from exc

    parts = [paragraph.text.strip() for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    extracted = "\n".join(part for part in parts if part).strip()
    if not extracted:
        raise ValueError("This DOCX file does not contain any readable text.")
    return extracted


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extract plain text from an uploaded resume based on its extension."""
    extension = os.path.splitext(filename or "")[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file type. Upload a PDF, DOCX, or UTF-8 text file.")
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")

    if extension == ".pdf":
        text = _pdf_text(file_bytes)
    elif extension == ".docx":
        text = _docx_text(file_bytes)
    else:
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text files must use UTF-8 encoding.") from exc

    text = text.replace("\x00", "").strip()
    if not text:
        raise ValueError("The file does not contain readable text.")
    return text[:MAX_TEXT_CHARS]


def extract_upload(storage) -> Tuple[str, str]:
    """Extract text from a Werkzeug ``FileStorage``; returns (filename, text)."""
    from werkzeug.utils import secure_filename

    filename = secure_filename(storage.filename or "")
    if not filename:
        raise ValueError("The uploaded file has an invalid filename.")
    return filename, extract_text(storage.read(), filename)
