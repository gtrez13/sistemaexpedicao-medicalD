# services/html_to_pdf_service.py
"""
Converte HTML -> PDF usando Playwright headless (render puro).
Remove scripts externos antes de renderizar para nao esperar rede (~3s vs ~25s).
"""
import os
import re
import tempfile
from playwright.sync_api import sync_playwright


def _strip_external_scripts(html: str) -> str:
    """Remove tags <script src="http..."> para evitar requests de rede desnecessarios."""
    html = re.sub(
        r'<script[^>]+src=["\']https?://[^"\']*["\'][^>]*></script>',
        '', html, flags=re.IGNORECASE
    )
    html = re.sub(
        r'<script[^>]+src=["\']https?://[^"\']*["\'][^>]*/?>',
        '', html, flags=re.IGNORECASE
    )
    return html


def html_bytes_to_pdf_bytes(
    html_bytes: bytes,
    width_mm: int = 100,
    height_mm: int = 150,
) -> bytes:
    html_text = html_bytes.decode("utf-8", errors="ignore")
    html_text = _strip_external_scripts(html_text)

    with tempfile.TemporaryDirectory() as td:
        html_path = os.path.join(td, "doc.html")
        pdf_path  = os.path.join(td, "doc.pdf")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_text)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 800, "height": 1200})

            # domcontentloaded: nao espera scripts externos (muito mais rapido)
            page.goto(f"file:///{html_path}", wait_until="domcontentloaded")

            page.pdf(
                path=pdf_path,
                width=f"{width_mm}mm",
                height=f"{height_mm}mm",
                print_background=True,
                margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
                prefer_css_page_size=True,
            )
            browser.close()

        with open(pdf_path, "rb") as f:
            return f.read()