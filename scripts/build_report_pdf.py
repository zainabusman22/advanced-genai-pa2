"""Render report/report.md to report/report.pdf (A4) via headless Edge/Chrome.

  python scripts/build_report_pdf.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "report"

CSS = """
@page { size: A4; margin: 14mm 15mm 14mm 15mm; }
body { font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 9.6pt; line-height: 1.35; color: #111; }
h1 { font-size: 15pt; margin: 0 0 2px 0; }
h2 { font-size: 11.5pt; margin: 10px 0 4px 0; border-bottom: 1px solid #bbb; padding-bottom: 1px; }
h3 { font-size: 10pt; margin: 8px 0 2px 0; }
p { margin: 3px 0 5px 0; }
ul, ol { margin: 2px 0 5px 0; padding-left: 18px; }
li { margin: 1px 0; }
table { border-collapse: collapse; width: auto; max-width: 100%; margin: 4px 0 7px 0; font-size: 8.3pt; page-break-inside: avoid; }
th, td { border: 1px solid #c8c8c8; padding: 2px 4px; vertical-align: top; text-align: left; }
th { background: #eef2f7; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 4px 6px; white-space: pre-wrap; word-break: break-all; margin: 3px 0 6px 0; }
pre code { background: none; padding: 0; }
code { font-family: Consolas, monospace; font-size: 8.3pt; background: #f4f4f4; padding: 0 2px; }
img { width: 100%; margin: 2px 0; page-break-inside: avoid; }
img[alt="SFT loss"] { width: 58%; display: block; margin: 2px auto; }
a { color: #1a4fa0; text-decoration: none; }
"""


def find_browser():
    for c in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              shutil.which("msedge"), shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chrome")]:
        if c and Path(c).exists():
            return c
    raise SystemExit("No Edge/Chrome found for PDF rendering")


def main():
    md_text = (REPORT / "report.md").read_text(encoding="utf-8")
    # Python-Markdown needs a blank line before a list that follows a paragraph line.
    item = re.compile(r"^\s*([-*]|\d+\.) ")
    out, prev = [], ""
    for line in md_text.splitlines():
        if item.match(line) and prev.strip() and not item.match(prev):
            out.append("")
        out.append(line)
        prev = line
    md_text = "\n".join(out)
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists", "fenced_code"])
    html = f'<!doctype html><html><head><meta charset="utf-8"><title>PA2 Report</title><style>{CSS}</style></head><body>{body}</body></html>'
    html_path = REPORT / "report.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = REPORT / "report.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
    # Separate profile (works even if the browser is already open); print to a temp path without
    # spaces because Chromium mis-parses --print-to-pdf values containing spaces on Windows.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_pdf = Path(tmp) / "report.pdf"
        subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={Path(tmp) / 'profile'}", f"--print-to-pdf={tmp_pdf}", str(html_path)],
                       check=True, capture_output=True, timeout=180)
        if tmp_pdf.exists():
            shutil.move(str(tmp_pdf), pdf_path)
    if not pdf_path.exists():
        raise SystemExit("browser did not produce a PDF")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf_path.read_bytes()))
    print(f"wrote {pdf_path} ({pages} pages)")


if __name__ == "__main__":
    main()
