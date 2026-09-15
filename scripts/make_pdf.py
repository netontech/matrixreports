#!/usr/bin/env python3
"""Render a markdown doc to a printable PDF.

The runbook goes to an engineer who will follow it on a second screen or on
paper, so it needs to survive printing: code blocks must not split across
pages, headings must not strand themselves at the foot of one, and the tables
need to stay readable in grey.

    python scripts/make_pdf.py docs/runbook-ubuntu-vm.md out/runbook.pdf

Needs `markdown` (pip install markdown) and Google Chrome, which does the
actual PDF rendering headlessly. No LaTeX, no pandoc.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
:root{ --ink:#16202c; --muted:#5b6878; --line:#c9d2dd; --brand:#2f5d8a;
       --warn:#8a5a12; --warn-bg:#fdf6e8; --code-bg:#f4f6f9; }
*{box-sizing:border-box}
body{font:10.5pt/1.5 -apple-system,"Helvetica Neue",Arial,sans-serif;color:var(--ink);margin:0}
h1{font-size:22pt;line-height:1.15;margin:0 0 4pt;letter-spacing:-.01em}
h1+p{color:var(--muted);font-size:10pt}
h2{font-size:14pt;margin:20pt 0 6pt;padding-bottom:4pt;border-bottom:1.5pt solid var(--brand);
   color:var(--brand);page-break-after:avoid;break-after:avoid}
h3{font-size:11.5pt;margin:13pt 0 4pt;page-break-after:avoid;break-after:avoid}
p,ul,ol,table{margin:0 0 7pt}
li{margin-bottom:2.5pt}
code{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:9pt;background:var(--code-bg);
     padding:1pt 3pt;border-radius:2pt}
pre{background:var(--code-bg);border:.5pt solid var(--line);border-left:2.5pt solid var(--brand);
    border-radius:3pt;padding:7pt 9pt;white-space:pre-wrap;word-break:break-word;
    page-break-inside:avoid;break-inside:avoid;margin:0 0 8pt}
pre code{background:none;padding:0;font-size:8.6pt;line-height:1.42}
table{border-collapse:collapse;width:100%;font-size:9pt;page-break-inside:avoid}
th,td{border:.5pt solid var(--line);padding:4pt 6pt;text-align:left;vertical-align:top}
th{background:var(--brand);color:#fff;font-weight:600}
tr:nth-child(even) td{background:#fafbfd}
blockquote{margin:0 0 9pt;padding:7pt 10pt;background:var(--warn-bg);
           border-left:2.5pt solid var(--warn);color:var(--warn);page-break-inside:avoid}
blockquote p{margin:0 0 4pt} blockquote p:last-child{margin:0}
blockquote code{background:rgba(0,0,0,.05)}
hr{border:0;border-top:.5pt solid var(--line);margin:14pt 0}
strong{font-weight:650}
a{color:var(--brand);text-decoration:none}
h2,h3{orphans:3;widows:3}
"""


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    sys.exit("Chrome not found - install it, or add its path to CHROME_CANDIDATES.")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <source.md> <output.pdf>")
    source, target = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    if not source.exists():
        sys.exit(f"no such file: {source}")

    try:
        import markdown
    except ImportError:
        sys.exit("pip install markdown")

    body = markdown.markdown(
        source.read_text(),
        extensions=["tables", "fenced_code", "sane_lists"],
    ).replace("[ ]", "&#9744;")

    target.parent.mkdir(parents=True, exist_ok=True)
    html = target.with_suffix(".html")
    html.write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><title>{source.stem}</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )

    subprocess.run(
        [find_chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={target.resolve()}", html.resolve().as_uri()],
        check=True, capture_output=True,
    )
    size = target.stat().st_size
    print(f"wrote {target} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
