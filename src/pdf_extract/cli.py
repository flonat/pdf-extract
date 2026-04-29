"""pdf-extract CLI.

Usage:
    pdf-extract <pdf-path> [--backend auto|pymupdf4llm|marker] [--section SECTION]
                           [--tables | --figures | --references | --metadata | --json]
                           [--no-cache]

Default action: print full Markdown to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import EXTRACT_VERSION, extract, section


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdf-extract", description=__doc__)
    p.add_argument("pdf_path", type=Path, help="Path to PDF file")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "pymupdf4llm", "marker"])
    p.add_argument("--section", metavar="NAME",
                   help="Print only the named section (fuzzy heading match)")
    p.add_argument("--tables", action="store_true", help="Print tables as Markdown")
    p.add_argument("--figures", action="store_true",
                   help="Print figure paths + captions")
    p.add_argument("--references", action="store_true",
                   help="Print extracted bibliography entries")
    p.add_argument("--metadata", action="store_true",
                   help="Print metadata dict as JSON")
    p.add_argument("--json", action="store_true",
                   help="Print full ExtractedDoc as JSON")
    p.add_argument("--skip-ocr", action="store_true",
                   help="Skip OCR (Marker only)")
    p.add_argument("--no-cache", action="store_true", help="Bypass on-disk cache")
    p.add_argument("--version", action="version", version=f"pdf-extract {EXTRACT_VERSION}")
    args = p.parse_args(argv)

    if not args.pdf_path.exists():
        print(f"ERROR: file not found: {args.pdf_path}", file=sys.stderr)
        return 1

    doc = extract(
        args.pdf_path,
        backend=args.backend,
        skip_ocr=args.skip_ocr,
        use_cache=not args.no_cache,
    )

    if args.json:
        json.dump(doc.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.section:
        body = section(doc, args.section)
        if body is None:
            print(f"ERROR: section {args.section!r} not found", file=sys.stderr)
            return 2
        sys.stdout.write(body + "\n")
        return 0

    if args.tables:
        for t in doc.tables:
            cap = f"  ({t.caption})" if t.caption else ""
            print(f"--- table {t.index}{cap} (page {t.page}) ---")
            print(t.markdown)
            print()
        return 0

    if args.figures:
        for f in doc.figures:
            cap = f"  {f.caption}" if f.caption else ""
            print(f"figure {f.index}: {f.image_path} (page {f.page}){cap}")
        return 0

    if args.references:
        for r in doc.references:
            print(r.raw)
        return 0

    if args.metadata:
        json.dump(doc.metadata, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(doc.markdown + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
