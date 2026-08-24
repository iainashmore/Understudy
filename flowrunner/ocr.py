"""Reading text that only exists as pixels.

The genuine last resort, for a response rendered into a canvas or a
custom-drawn panel where there is no text to extract. Two things follow from
that, and both are deliberate:

  * The region image is always kept, whether or not OCR runs. A transcription
    is a lossy derivative; the pixels are the evidence, and a run whose only
    record was an OCR guess would be unauditable.
  * A missing OCR engine is reported, never silently treated as an empty
    response. An empty string that means "could not read" is indistinguishable
    from one that means "the assistant said nothing", and the two demand
    completely different reactions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrResult:
    text: str
    engine: str
    available: bool = True
    error: str | None = None


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def read_text(image: bytes) -> OcrResult:
    """Transcribe an image. Never raises."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return OcrResult(
            text="", engine="none", available=False,
            error="OCR needs pytesseract and the tesseract binary "
                  "(pip install pytesseract; apt install tesseract-ocr)",
        )

    import io

    try:
        with Image.open(io.BytesIO(image)) as picture:
            text = pytesseract.image_to_string(picture)
        return OcrResult(text=" ".join(text.split()), engine="tesseract")
    except Exception as exc:
        return OcrResult(
            text="", engine="tesseract", available=False,
            error=f"OCR failed: {exc}",
        )
