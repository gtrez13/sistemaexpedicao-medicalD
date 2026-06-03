# services/windows_print_service.py
import os, sys

class WindowsPrintService:
    @staticmethod
    def print_pdf(pdf_path: str, printer_name: str, verify_queue: bool = True):
        if sys.platform != "win32":
            print(f"⚠️ Impressão ignorada no Linux (printer={printer_name})", flush=True)
            return
        raise Exception("WindowsPrintService não suportado fora do Windows")
