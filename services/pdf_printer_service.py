# services/pdf_printer_service.py
from __future__ import annotations
import os
import sys

class PDFPrinterService:
    """
    Impressão PDF - desabilitada no Linux (servidor).
    No servidor, o PDF é servido via URL para download.
    """
    @staticmethod
    def print_pdf_bytes(pdf_bytes: bytes, printer_name: str, *, copies: int = 1) -> None:
        if sys.platform != "win32":
            print(f"⚠️ Impressão ignorada no Linux (printer={printer_name})", flush=True)
            return
        # Windows: usa SumatraPDF
        import subprocess
        import tempfile
        from pathlib import Path
        exe = (os.getenv("SUMATRA_EXE") or os.getenv("SUMATRA_PATH") or "").strip()
        if not exe or not Path(exe).exists():
            raise Exception("SumatraPDF não encontrado")
        if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
            raise Exception("Conteúdo não é PDF válido")
        with tempfile.TemporaryDirectory() as td:
            pdf_path = Path(td) / "doc.pdf"
            pdf_path.write_bytes(pdf_bytes)
            cmd = [exe, "-silent", "-exit-when-done", "-print-to", str(printer_name), str(pdf_path)]
            for _ in range(max(1, int(copies))):
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise Exception(f"Falha SumatraPDF code={r.returncode}")
