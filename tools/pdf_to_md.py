"""
Convert PDFs to Markdown with inline image extraction.

Usage:
    python tools/pdf_to_md.py --root reference

Outputs a .md file next to each PDF and an <stem>_images/ directory
containing extracted PNGs. Images in page headers/footers and tiny assets
are filtered out heuristically.

Dependencies:
    pip install pymupdf
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF


def extract_pdf(
    pdf_path: Path,
    *,
    margin_ratio: float = 0.1,
    min_area: int = 5000,
    min_dim: int = 20,
) -> None:
    """
    Extract text and body images from one PDF into Markdown + image folder.

    Args:
        pdf_path: Path to the PDF.
        margin_ratio: Fraction of page height to treat as header/footer (skip images there).
        min_area: Minimum image area (pixels^2) to keep.
        min_dim: Minimum width/height (pixels) to keep.
    """
    doc = fitz.open(pdf_path)
    images_dir = pdf_path.with_suffix("").parent / f"{pdf_path.stem}_images"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    parts: list[str] = [f"# {pdf_path.name}\n"]
    img_total = 0

    for page_index, page in enumerate(doc):
        text = page.get_text("text") or ""
        parts.append(f"\n\n## Page {page_index + 1}\n\n{text.strip()}\n")

        page_height = page.rect.height
        header_cut = margin_ratio * page_height
        footer_cut = page_height - header_cut

        for img in page.get_images(full=True):
            xref = img[0]
            rects = page.get_image_rects(xref)
            rect = rects[0] if rects else None
            if rect:
                if rect.y1 <= header_cut or rect.y0 >= footer_cut:
                    continue  # header/footer
                if rect.width * rect.height < min_area:
                    continue
                if rect.width < min_dim or rect.height < min_dim:
                    continue
            try:
                pix = fitz.Pixmap(doc, xref)
                img_total += 1
                img_name = f"img-{page_index + 1:03d}-{img_total:03d}.png"
                img_path = images_dir / img_name
                if pix.n - pix.alpha < 4:
                    pix.save(img_path)
                else:
                    pix_rgb = fitz.Pixmap(fitz.csRGB, pix)
                    pix_rgb.save(img_path)
                    pix_rgb = None
                parts.append(f"\n![Page {page_index + 1} image {img_total}]({images_dir.name}/{img_name})\n")
            except Exception:
                continue

    md_text = "\n".join(parts)
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    md_path = pdf_path.with_suffix(".md")
    md_path.write_text(md_text)
    print(f"[OK] {pdf_path.name} -> {md_path.name} with {img_total} images ({images_dir.name}/)")


def iter_pdfs(root: Path) -> Iterable[Path]:
    """Yield all PDFs under root."""
    return root.rglob("*.pdf")


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert PDFs to Markdown with body images.")
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("reference"),
        help="Root directory to scan for PDFs (default: reference)",
    )
    ap.add_argument(
        "--margin-ratio",
        type=float,
        default=0.1,
        help="Fraction of page height treated as header/footer for image skipping (default: 0.1)",
    )
    ap.add_argument(
        "--min-area",
        type=int,
        default=5000,
        help="Minimum image area in pixels^2 to keep (default: 5000)",
    )
    ap.add_argument(
        "--min-dim",
        type=int,
        default=20,
        help="Minimum image width/height in pixels to keep (default: 20)",
    )
    args = ap.parse_args()

    if not args.root.exists():
        raise SystemExit(f"Root path not found: {args.root}")

    pdfs = list(iter_pdfs(args.root))
    if not pdfs:
        print(f"No PDFs found under {args.root}")
        return

    for pdf in pdfs:
        extract_pdf(pdf, margin_ratio=args.margin_ratio, min_area=args.min_area, min_dim=args.min_dim)


if __name__ == "__main__":
    main()
