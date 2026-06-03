# services/bling_nfe_rpa.py
"""
RPA puro com browser persistente. Tudo via Playwright: gerar NF-e,
enviar SEFAZ, baixar ZPL, converter PDF, imprimir.
Ganho de ~15-20s por pedido com browser persistente.
"""
from __future__ import annotations
import os, re, time
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from services.zpl_converter import ZPLConverterService
from services.windows_print_service import WindowsPrintService
from services.bling_service import BlingService

BLING_VENDAS_URL = "https://www.bling.com.br/vendas.php#list"
BLING_NOTAS_URL  = "https://www.bling.com.br/notas.fiscais.php#list"
BASE_DIR         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS_DIR    = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ── Browser persistente ──────────────────────────────────────
_pw = _browser = None

def _get_browser(headless=True):
    global _pw, _browser
    if _pw is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
    if _browser is None or not _browser.is_connected():
        _browser = _pw.chromium.launch(headless=headless, args=["--start-maximized","--no-sandbox"])
        print("🌐 Browser iniciado (persistente)", flush=True)
    return _browser

def _new_ctx(headless=True):
    kw = {"viewport": {"width":1366,"height":900}, "accept_downloads":True}
    sf = os.path.join(BASE_DIR,"bling_state.json")
    if os.path.exists(sf): kw["storage_state"] = sf
    return _get_browser(headless).new_context(**kw)

# ── Helpers ──────────────────────────────────────────────────
def _overlay(page, ms=60000):
    try:
        page.wait_for_function(
            "()=>{const o=document.querySelector('.blockUI.blockOverlay');if(!o)return true;"
            "const s=window.getComputedStyle(o);return s.display==='none'||s.visibility==='hidden'||o.offsetHeight===0;}",
            timeout=ms)
    except PlaywrightTimeoutError: pass

def _login(page):
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    if "login" not in (page.url or ""): return
    print("🔐 Login...", flush=True)
    u,p = os.getenv("BLING_USER"), os.getenv("BLING_PASS")
    if not u or not p: raise Exception("BLING_USER/BLING_PASS não definidos")
    page.wait_for_selector("#username", state="visible", timeout=30000)
    page.wait_for_timeout(400)
    page.fill("#username", u); page.fill("input[type='password']", p)
    page.wait_for_function(
        "()=>{const b=[...document.querySelectorAll('button,div')].find(e=>(e.innerText||'').trim()==='Entrar');return b&&!b.disabled;}", timeout=30000)
    page.evaluate("()=>{const b=[...document.querySelectorAll('button,div')].find(e=>(e.innerText||'').trim()==='Entrar');if(b)b.click();}")
    page.wait_for_function("()=>!location.href.includes('login')", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=20000)
    try: page.context.storage_state(path=os.path.join(BASE_DIR,"bling_state.json"))
    except: pass
    print("✅ Login ok", flush=True)

def _click(page, loc):
    loc.scroll_into_view_if_needed()
    try: loc.click(timeout=2000)
    except: page.evaluate("(el)=>el.click()", loc.element_handle())

def _selecionar(page, numero: str):
    numero = re.sub(r"\D+","",str(numero).strip())
    pat    = re.compile(rf"(?<!\d){re.escape(numero)}(?!\d)")
    _overlay(page)
    page.wait_for_selector("table.tabela-listagem tbody tr", state="attached", timeout=30000)
    rows = page.locator("table.tabela-listagem tbody tr")
    chosen = None
    for i in range(min(rows.count(), 80)):
        try: txt = rows.nth(i).inner_text(timeout=800) or ""
        except: continue
        if pat.search(txt): chosen = rows.nth(i); break
    if not chosen: raise Exception(f"Linha '{numero}' não encontrada.")
    chosen.scroll_into_view_if_needed(); _overlay(page, 15000)
    cb = chosen.locator("label[for^='marcado']").first
    if cb.count():
        fid = cb.get_attribute("for")
        inp = page.locator(f"input#{fid}").first
        inp.wait_for(state="attached", timeout=10000)
        try: inp.check(force=True)
        except: page.evaluate("(el)=>{el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));el.dispatchEvent(new MouseEvent('click',{bubbles:true}));}", inp.element_handle())
        return
    cb2 = chosen.locator("input.tcheck").first
    if cb2.count():
        try: cb2.check(force=True)
        except: page.evaluate("(el)=>el.click()", cb2.element_handle())
        return
    raise Exception("Checkbox não encontrado.")

def _ir_notas(page):
    if "notas.fiscais.php" not in (page.url or ""):
        page.goto(BLING_NOTAS_URL, wait_until="domcontentloaded", timeout=60000)
    _login(page); _overlay(page)
    page.wait_for_selector("#pesquisa-mini", state="visible", timeout=30000)
    page.wait_for_selector("table.tabela-listagem", state="attached", timeout=30000)

def _buscar(page, valor: str):
    b = page.locator("#pesquisa-mini")
    b.wait_for(state="visible", timeout=15000)
    b.fill(str(valor)); b.press("Enter")
    page.wait_for_timeout(800)

def _resolver_num_nf(pv_id: int, numero_loja: str, timeout_s=90) -> str:
    bling = BlingService()
    t0 = time.time()
    while time.time()-t0 < timeout_s:
        try:
            nid = bling.resolver_nfe_por_pv_id(pv_id, numero_loja=numero_loja)
            if nid:
                num = bling.obter_numero_nota(int(nid))
                if num: return re.sub(r"\D+","",str(num))
        except: pass
        time.sleep(1.5)
    raise Exception(f"Número NF não encontrado via API em {timeout_s}s (PV={pv_id})")


class BlingRPA:

    def __init__(self, headless=True):
        self.headless = headless

    def emitir(self, numero_pedido: str, pv_id: int|None=None, numero_loja: str|None=None) -> dict:
        t0 = time.time()
        ctx = _new_ctx(self.headless)
        page = ctx.new_page()
        try:
            # ── VENDAS ──────────────────────────────────────
            page.goto(BLING_VENDAS_URL, wait_until="domcontentloaded", timeout=60000)
            _login(page); _overlay(page)
            page.wait_for_selector("#pesquisa-mini", timeout=20000)
            _buscar(page, numero_pedido)
            _selecionar(page, numero_pedido)
            print("✅ Pedido selecionado", flush=True)

            btn = page.locator("button.act-gerar-notas").first
            btn.wait_for(state="visible", timeout=15000)
            _click(page, btn)

            try:
                page.wait_for_selector("div.ui-dialog", timeout=6000)
                sim = page.locator("div.ui-dialog button.Button--primary").first
                sim.wait_for(state="visible", timeout=5000)
                _click(page, sim)
                print("✅ NF-e gerada", flush=True)
            except: print("ℹ️ Sem diálogo (NF já existia?)", flush=True)

            try:
                page.wait_for_selector("text=Emitir nota fiscal", timeout=4000)
                page.get_by_role("button", name="Emitir").click()
                print("✅ Emitir clicado", flush=True)
            except: pass

            # ── NOTAS ──────────────────────────────────────
            print(f"⏱️ VENDAS: {time.time()-t0:.1f}s — indo pra NOTAS...", flush=True)

            # Resolve número da NF via API (mais confiável)
            nf_num = _resolver_num_nf(int(pv_id), str(numero_loja or ""), timeout_s=60)
            print(f"✅ NF num = {nf_num}", flush=True)

            _ir_notas(page)
            _buscar(page, nf_num)
            _selecionar(page, nf_num)

            # Enviar pra SEFAZ
            self._enviar(page)

            # Fecha popup de processamento se aparecer
            try:
                page.wait_for_timeout(1000)
                x = page.locator("button.ui-dialog-titlebar-close:visible").last
                if x.count(): _click(page, x)
            except: pass
            _overlay(page)

            # Re-seleciona pra baixar ZPL
            _buscar(page, nf_num)
            _selecionar(page, nf_num)

            # Mais ações → DANFE Simplificado + Etiqueta
            self._abrir_mais_acoes(page)
            self._clicar_danfe_zpl(page)

            # Download ZPL
            zpl_path = self._baixar_zpl(page, nf_num)
            pdf_path = self._converter_zpl(zpl_path, nf_num)

            # Imprimir
            printer = os.getenv("PRINTER_NAME","")
            # if printer: WindowsPrintService.print_pdf(pdf_path, printer, verify_queue=True)

            print(f"✅ TOTAL: {time.time()-t0:.1f}s", flush=True)
            return {"pdf": pdf_path}

        finally:
            try: page.close()
            except: pass
            try: ctx.close()
            except: pass

    def _enviar(self, page):
        # Tenta abrir menu de envio
        for sel in [
            "span.action-text:has-text('Enviar NF-es selecionadas')",
            "text=Enviar NF-es selecionadas",
        ]:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=4000)
                _click(page, loc); break
            except: continue
        else:
            page.evaluate("""()=>{
                const els=[...document.querySelectorAll('span,button,a,div')];
                const h=els.find(e=>e.offsetParent&&(e.innerText||'').toLowerCase().includes('enviar nf'));
                if(h)h.click();
            }""")

        try:
            page.wait_for_selector("div.ui-dialog:visible", timeout=6000)
            btn = page.locator("div.ui-dialog:visible").last.locator("#notaAcao, button:has-text('Enviar selecionadas')").first
            btn.wait_for(state="visible", timeout=6000); _click(page, btn)
        except: pass

        try:
            page.wait_for_selector("div.ui-dialog:visible", timeout=5000)
            sim = page.locator("div.ui-dialog:visible").last.locator("button.Button--primary, button:has-text('Sim')").first
            sim.wait_for(state="visible", timeout=5000); _click(page, sim)
        except: pass

        page.wait_for_timeout(1500)

        try:
            x = page.locator("button.ui-dialog-titlebar-close:visible").last
            if x.count(): _click(page, x)
            _overlay(page)
        except: pass
        print("✅ NF-e enviada pra SEFAZ", flush=True)

    def _abrir_mais_acoes(self, page):
        _overlay(page); page.keyboard.press("Escape"); page.wait_for_timeout(100)
        ok = page.evaluate("""()=>{
            const h=document.querySelector('.more-actions .hidden-actions');
            if(!h)return false;
            h.hidden=false; h.style.display='block'; h.style.visibility='visible';
            h.style.opacity='1'; h.style.pointerEvents='auto'; h.style.zIndex='99999';
            h.classList.add('force-open'); return true;
        }""")
        if not ok: raise Exception("Não abriu Mais ações")
        page.locator("li[data-function='imprimirDANFESimplificadoComEtiqueta']").first.wait_for(state="attached", timeout=8000)

    def _clicar_danfe_zpl(self, page):
        def _zpl_ok():
            try:
                return (page.locator("text=Impressões geradas em zpl").first.count() > 0
                     or page.locator("a:has-text('Clique para baixar os arquivos')").first.count() > 0)
            except: return False

        item = page.locator("li[data-function='imprimirDANFESimplificadoComEtiqueta']").first
        for _ in range(3):
            _overlay(page)
            try: item.scroll_into_view_if_needed(); item.click(timeout=3000, force=True)
            except: pass
            if not _zpl_ok():
                page.evaluate("""()=>{
                    const el=document.querySelector("li[data-function='imprimirDANFESimplificadoComEtiqueta']");
                    if(!el)return;
                    el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
                    el.dispatchEvent(new MouseEvent('click',{bubbles:true}));
                }""")
            t = time.time()
            while time.time()-t < 10:
                if _zpl_ok(): print("✅ ZPL card apareceu", flush=True); return
                page.wait_for_timeout(300)
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
        raise Exception("ZPL card não apareceu")

    def _baixar_zpl(self, page, nf_num: str) -> str:
        page.wait_for_function(
            "()=>document.querySelectorAll('a[href*=\"security.file.php\"]').length>0",
            timeout=60000)
        href = page.eval_on_selector('a[href*="security.file.php"]','a=>a.href')
        resp = page.context.request.get(href, timeout=60000)
        if not resp.ok: raise Exception(f"Download ZPL falhou: {resp.status}")
        body = resp.body()
        if body[:20].lstrip().startswith(b"<"):
            html = body.decode("utf-8","ignore")
            m = re.search(r"https://www\.bling\.com\.br/security\.file\.php\?token=[^\"']+", html)
            if m:
                r2 = page.context.request.get(m.group(0), timeout=60000)
                if r2.ok: body = r2.body()
        path = os.path.join(DOWNLOADS_DIR, f"combo_{nf_num}_{int(time.time())}.zpl")
        with open(path,"wb") as f: f.write(body)
        print(f"✅ ZPL salvo: {path}", flush=True)
        return path

    def _converter_zpl(self, zpl_path: str, nf_num: str) -> str:
        with open(zpl_path,"rb") as f: zpl = f.read().decode("utf-8","ignore")
        pdf = ZPLConverterService.zpl_to_pdf(zpl)
        path = os.path.join(DOWNLOADS_DIR, f"combo_{nf_num}_{int(time.time())}.pdf")
        with open(path,"wb") as f: f.write(pdf)
        print(f"✅ PDF: {path}", flush=True)
        return path