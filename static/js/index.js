// =========================
// BEEP SONORO
// =========================
let _audioCtx = null;
function _getAudioCtx() {
    if (!_audioCtx) {
        try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch { }
    }
    return _audioCtx;
}

function beepSucesso() {
    try {
        const ctx = _getAudioCtx();
        if (!ctx) return;
        if (ctx.state === "suspended") ctx.resume();
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.type = "sine";
        o.frequency.value = 880;
        g.gain.value = 0.3;
        o.start();
        o.stop(ctx.currentTime + 0.15);
    } catch {}
}

function unlockAudio() {
    const ctx = _getAudioCtx();
    if (!ctx) return;
    if (ctx.state === "suspended") ctx.resume();
}
document.addEventListener("click", unlockAudio);
document.addEventListener("keydown", unlockAudio);

function beepErro() {
    try {
        const ctx = _getAudioCtx();
        if (!ctx) return;
        [220, 180].forEach((freq, i) => {
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.connect(g); g.connect(ctx.destination);
            o.type = 'square';
            const t = ctx.currentTime + i * 0.22;
            o.frequency.setValueAtTime(freq, t);
            g.gain.setValueAtTime(0.3, t);
            g.gain.exponentialRampToValueAtTime(0.001, t + 0.2);
            o.start(t); o.stop(t + 0.2);
        });
    } catch {}
}


// =========================
// CONFIG GLOBAL
// =========================
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

let CURRENT_ML_ID = null;


// =========================
// AGRUPAR PRODUTOS
// =========================
function normalizeBaseName(name) {
    if (!name) return "";
    let s = String(name).toLowerCase();
    s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    s = s.replace(/\b(tamanho|tam|nº|n\.|no\.|numero|número)\b/g, ' ');
    s = s.replace(/\b\d+([.,]\d+)?\b/g, ' ');
    s = s.replace(/[|:/\-\(\)\[\],.]+/g, ' ');
    s = s.replace(/\s+/g, ' ').trim();
    return s.split(' ').filter(Boolean).slice(0, 4).join(' ');
}

function buildGroupBlock(title, totalQty) {
    const block = document.createElement('div');
    block.className = 'groupBlock';
    block.dataset.open = "1";
    block.innerHTML = `
        <div class="groupHead">
            <p class="groupTitle">🧩 ${title}</p>
            <div style="display:flex; align-items:center; gap:10px;">
                <span class="groupBadge">${totalQty} un</span>
                <div class="caret">▾</div>
            </div>
        </div>
        <div class="groupBody"></div>
    `;
    block.querySelector('.groupHead').addEventListener('click', () => {
        block.dataset.open = (block.dataset.open === "1") ? "0" : "1";
    });
    return block;
}


// =========================
// UTILIDADES PEDIDO
// =========================
function getVisibleOrders() {
    return Array.from(document.querySelectorAll('.order[data-ml-id]'))
        .filter(o => o.offsetParent !== null);
}

function findOrderByMlId(mlId) {
    return document.querySelector(`.order[data-ml-id="${CSS.escape(String(mlId))}"]`);
}

function scrollToSelectedPedido() {
    if (!CURRENT_ML_ID) return;
    const sel = findOrderByMlId(CURRENT_ML_ID);
    if (sel) sel.scrollIntoView({ behavior: "auto", block: "center" });
}

function setSelectedPedido(mlId) {
    CURRENT_ML_ID = (mlId || '').toString().trim();
    localStorage.setItem("selected_pedido", CURRENT_ML_ID);
    document.querySelectorAll('.order').forEach(o => {
        const isSel = (o.dataset.mlId || '') === CURRENT_ML_ID;
        o.classList.toggle('selected', isSel);
        o.style.outline = isSel ? '2px solid rgba(13,110,253,.35)' : '';
        o.style.outlineOffset = isSel ? '2px' : '';
    });
}

function saveNextPedidoTarget() {
    const orders = getVisibleOrders();
    const index = orders.findIndex(o => (o.dataset.mlId || '') === CURRENT_ML_ID);
    if (index === -1) return;
    const next = orders[index + 1];
    if (next) localStorage.setItem("next_order_ml_id", next.dataset.mlId);
    else localStorage.removeItem("next_order_ml_id");
}


// =========================
// SELEÇÃO DE PEDIDO
// =========================
function bindPedidoSelection() {
    document.querySelectorAll('.order[data-ml-id]').forEach(order => {
        order.addEventListener('click', (e) => {
            if (e.target.closest('button, input, select, textarea, a, label')) return;
            setSelectedPedido(order.dataset.mlId);
        });
    });
}


// =========================
// FILTRO POR TIPO ENVIO
// =========================
function aplicarFiltroEnvio() {
    const sel = document.getElementById('tipoFilter');
    if (!sel) return;
    const val = sel.value || 'ALL';
    document.querySelectorAll('.order[data-envio]').forEach(order => {
        const envio = (order.dataset.envio || '').toUpperCase();
        let show = true;
        if (val === 'FLEX') show = envio === 'FLEX';
        else if (val === 'COLETA') show = envio.includes('COLETA');
        order.style.display = show ? '' : 'none';
    });
}
document.getElementById('tipoFilter')?.addEventListener('change', aplicarFiltroEnvio);


// =========================
// CHECKBOXES — REMOVER ITENS
// =========================

/**
 * Atualiza visibilidade do botão "Remover selecionados"
 * sempre que um checkbox muda de estado.
 */
function _atualizarBotaoRemover(mlId) {
    const order = findOrderByMlId(mlId);
    if (!order) return;

    const temMarcado = order.querySelectorAll(
        '.item-checkbox:checked, .kit-checkbox:checked'
    ).length > 0;

    const btn = order.querySelector('.btn-remover-itens');
    if (btn) btn.style.display = temMarcado ? '' : 'none';
}

function bindCheckboxes() {
    // Checkboxes de itens normais
    document.querySelectorAll('.item-checkbox').forEach(cb => {
        cb.addEventListener('change', () => {
            _atualizarBotaoRemover(cb.dataset.mlId);
        });
    });

    // Checkboxes de kits (seleciona/deseleciona o kit inteiro visualmente)
    document.querySelectorAll('.kit-checkbox').forEach(cb => {
        cb.addEventListener('change', () => {
            _atualizarBotaoRemover(cb.dataset.mlId);
        });
    });
}

function bindRemoverButtons() {
    document.querySelectorAll('.btn-remover-itens').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const mlId = btn.dataset.mlId;
            const order = findOrderByMlId(mlId);
            if (!order) return;

            // coleta SKUs normais marcados
            const skus = Array.from(
                order.querySelectorAll('.item-checkbox:checked')
            ).map(cb => cb.dataset.sku).filter(Boolean);

            // coleta kit_skus marcados
            const kitSkus = Array.from(
                order.querySelectorAll('.kit-checkbox:checked')
            ).map(cb => cb.dataset.kitSku).filter(Boolean);

            if (!skus.length && !kitSkus.length) return;

            const total = skus.length + kitSkus.length;
            const descricao = [
                skus.length ? `${skus.length} item(ns) normal(is)` : '',
                kitSkus.length ? `${kitSkus.length} kit(s)` : '',
            ].filter(Boolean).join(' e ');

            _confirmarRemocao(mlId, skus, kitSkus, `Remover ${descricao} do pedido ${mlId}?`);
        });
    });
}

function _confirmarRemocao(mlId, skus, kitSkus, mensagem) {
    document.getElementById('_modalRemoverCustom')?.remove();

    const div = document.createElement('div');
    div.id = '_modalRemoverCustom';

    // Lista o que vai ser removido
    const listaItens = [
        ...skus.map(s => `<li style="font-size:0.82rem; opacity:0.8;">SKU: <strong>${s}</strong></li>`),
        ...kitSkus.map(s => `<li style="font-size:0.82rem; opacity:0.8;">📦 Kit: <strong>${s}</strong> (todos os componentes)</li>`),
    ].join('');

    div.innerHTML = `
        <div style="
            position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:9999;
            display:flex; align-items:center; justify-content:center;
        ">
            <div style="
                background: var(--card-bg, #1a1a2e);
                border: 1px solid var(--border, #333);
                border-radius: 14px; padding: 28px 24px;
                min-width: 320px; max-width: 460px; width: 90%;
                box-shadow: 0 8px 40px rgba(0,0,0,0.45);
                color: #ffffff;
            ">
                <p style="font-weight:900; font-size:1.05rem; margin-bottom:6px;">Remover itens do pedido</p>
                <p style="font-size:0.82rem; opacity:0.6; margin-bottom:12px;">Pedido: <strong>${mlId}</strong></p>

                <p style="font-size:0.83rem; margin-bottom:8px; font-weight:700;">Itens que serão removidos:</p>
                <ul style="margin:0 0 16px 0; padding-left:18px; list-style:disc;">${listaItens}</ul>

                <p style="font-size:0.8rem; color:#f87171; margin-bottom:18px;">
                    ⚠️ O item será removido deste pedido no sistema local.<br>
                    O pedido no Mercado Livre não será alterado.
                </p>

                <div style="display:flex; gap:10px; justify-content:flex-end;">
                    <button id="_cancelarRemover" class="btn btn-outline-secondary btnx" style="font-size:0.85rem;">Cancelar</button>
                    <button id="_confirmarRemoverBtn" class="btn btn-danger btnx" style="font-size:0.85rem; font-weight:700;">🗑 Confirmar remoção</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(div);

    document.getElementById('_cancelarRemover').addEventListener('click', () => div.remove());
    div.addEventListener('click', (e) => { if (e.target === div.firstElementChild) div.remove(); });

    document.getElementById('_confirmarRemoverBtn').addEventListener('click', async () => {
        const btnConf = document.getElementById('_confirmarRemoverBtn');
        btnConf.disabled = true;
        btnConf.textContent = 'Removendo...';

        try {
            const res = await fetch(`/pedido/${encodeURIComponent(mlId)}/remover_itens`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ skus, kit_skus: kitSkus })
            });
            const data = await res.json();

            if (data.success) {
                div.remove();

                if (data.itens_restantes === 0) {
                    // Pedido ficou vazio — remove o card
                    const card = findOrderByMlId(mlId);
                    if (card) {
                        card.style.transition = 'opacity 0.4s';
                        card.style.opacity = '0';
                        setTimeout(() => card.remove(), 420);
                    }
                } else {
                    // Recarrega a página para refletir os itens restantes
                    location.reload();
                }
            } else {
                alert('❌ Erro: ' + (data.error || 'Falha ao remover'));
                btnConf.disabled = false;
                btnConf.textContent = '🗑 Confirmar remoção';
            }
        } catch {
            alert('❌ Falha de conexão');
            btnConf.disabled = false;
            btnConf.textContent = '🗑 Confirmar remoção';
        }
    });
}


// =========================
// ARQUIVAR PEDIDO
// =========================
function abrirModalArquivar(mlId) {
    document.getElementById('_modalArquivarCustom')?.remove();

    const div = document.createElement('div');
    div.id = '_modalArquivarCustom';
    div.innerHTML = `
        <div style="
            position:fixed; inset:0; background:rgba(0, 0, 0, 0.68); z-index:9999;
            display:flex; align-items:center; justify-content:center;
        ">
            <div style="
                background: var(--card-bg, #0000008f);
                border: 1px solid var(--border, #333);
                border-radius: 14px; padding: 28px 24px;
                min-width: 320px; max-width: 440px; width: 90%;
                box-shadow: 0 8px 40px rgba(0,0,0,0.45);
                color: #ffffff;
            ">
                <p style="font-weight:900; font-size:1.05rem; margin-bottom:6px;">📁 Arquivar Pedido</p>
                <p style="font-size:0.85rem; opacity:0.65; margin-bottom:16px;">Pedido: <strong>${mlId}</strong></p>

                <label style="font-size:0.83rem; font-weight:700; display:block; margin-bottom:6px;">Observação (opcional)</label>
                <textarea id="_arquivarObsInput" rows="3" placeholder="Ex: cliente pediu cancelamento, aguardando retorno..." style="
                    width:100%; background: rgba(255,255,255,0.05);
                    border:1px solid var(--border, #1c1c1c); color: var(--fg, #fff);
                    border-radius:8px; padding:8px 10px; font-size:0.85rem;
                    resize:vertical; font-family:inherit; box-sizing:border-box;
                "></textarea>

                <div style="display:flex; gap:10px; margin-top:18px; justify-content:flex-end;">
                    <button id="_cancelarArquivar" class="btn btn-outline-secondary btnx" style="font-size:0.85rem;">Cancelar</button>
                    <button id="_confirmarArquivar" class="btn btn-warning btnx" style="font-size:0.85rem; font-weight:700;">Arquivar</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(div);
    setTimeout(() => document.getElementById('_arquivarObsInput')?.focus(), 80);

    document.getElementById('_cancelarArquivar').addEventListener('click', () => div.remove());
    div.addEventListener('click', (e) => { if (e.target === div.firstElementChild) div.remove(); });

    document.getElementById('_confirmarArquivar').addEventListener('click', async () => {
        const obs = (document.getElementById('_arquivarObsInput')?.value || '').trim();
        const btnConf = document.getElementById('_confirmarArquivar');
        btnConf.disabled = true;
        btnConf.textContent = 'Arquivando...';

        try {
            const res = await fetch(`/pedido/${encodeURIComponent(mlId)}/arquivar`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ observacao: obs })
            });
            const data = await res.json();
            if (data.success) {
                div.remove();
                const card = findOrderByMlId(mlId);
                if (card) {
                    card.style.transition = 'opacity 0.4s';
                    card.style.opacity = '0';
                    setTimeout(() => card.remove(), 420);
                }
                if (CURRENT_ML_ID === mlId) {
                    CURRENT_ML_ID = null;
                    localStorage.removeItem("selected_pedido");
                }
            } else {
                alert('❌ Erro ao arquivar: ' + (data.error || 'Falha'));
                btnConf.disabled = false;
                btnConf.textContent = '📁 Arquivar';
            }
        } catch {
            alert('❌ Falha de conexão');
            btnConf.disabled = false;
            btnConf.textContent = '📁 Arquivar';
        }
    });
}

function bindArquivarButtons() {
    document.querySelectorAll('.btn-arquivar-pedido').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            abrirModalArquivar(btn.dataset.mlId);
        });
    });
}


// =========================
// SCANNER
// =========================
const scannerInput = document.getElementById('scannerInput');
const scanMsg = document.getElementById('scanMsg');
const focusBtn = document.getElementById('focusBtn');

function focusScanner() {
    try { scannerInput?.focus({ preventScroll: true }); } catch {}
}

focusBtn?.addEventListener('click', focusScanner);
document.addEventListener('click', (e) => {
    if (e.target.closest('button, input, select, textarea, a, label')) return;
    focusScanner();
});


// =========================
// SCAN SKU
// =========================
scannerInput?.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();

    const sku = (scannerInput.value || '').trim();
    scannerInput.value = '';
    if (!sku) return;

    if (!CURRENT_ML_ID) {
        beepErro();
        scanMsg.textContent = "Selecione um pedido";
        return;
    }

    scanMsg.textContent = "Processando...";

    try {
        // Primeiro verifica quantidade antes de bipar
        const resCheck = await fetch('/verificar_e_bipar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sku, ml_id: CURRENT_ML_ID, quantidade: 1, apenas_verificar: true })
        });
        const dataCheck = await resCheck.json();
        let multiplicador = 1;
        if (dataCheck.quantidade_item >= 20) {
            const qtd = prompt(`Quantas unidades? (máx: ${dataCheck.quantidade_item})`, "1");
            const parsed = parseInt(qtd);
            if (!isNaN(parsed) && parsed >= 1) multiplicador = parsed;
        }
        const res = await fetch('/verificar_e_bipar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sku, ml_id: CURRENT_ML_ID, quantidade: multiplicador })
        });
        const data = await res.json();
        if (!data.success) {
            beepErro();
            scanMsg.textContent = "❌ " + (data.error || "Erro");
            return;
        }

        beepSucesso();
        scanMsg.textContent = "✅ Bipado!";

        if (data.pedido_fechado) saveNextPedidoTarget();
        else localStorage.setItem("next_order_ml_id", CURRENT_ML_ID);

        setTimeout(() => location.reload(), 300);

    } catch {
        beepErro();
        scanMsg.textContent = "❌ Falha conexão";
    }

    focusScanner();
});


// =========================
// CARREGAMENTO
// =========================
window.addEventListener('load', () => {
    bindPedidoSelection();
    bindArquivarButtons();
    bindCheckboxes();
    bindRemoverButtons();
    aplicarFiltroEnvio();

    const visibleOrders = getVisibleOrders();
    const savedNext = localStorage.getItem("next_order_ml_id");
    let pedidoParaSelecionar = null;

    if (savedNext) {
        const nextOrder = findOrderByMlId(savedNext);
        if (nextOrder) pedidoParaSelecionar = savedNext;
        localStorage.removeItem("next_order_ml_id");
    }

    if (!pedidoParaSelecionar && visibleOrders.length > 0)
        pedidoParaSelecionar = visibleOrders[0].dataset.mlId;

    if (pedidoParaSelecionar) setSelectedPedido(pedidoParaSelecionar);

    requestAnimationFrame(() => {
        scrollToSelectedPedido();
        focusScanner();
    });
});

// Sync
const syncBtn = document.getElementById("syncBtn");
const syncMsg = document.getElementById("syncMsg");
const syncPill = document.getElementById("syncPill");

syncBtn?.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncPill.style.display = "inline-block";
    syncMsg.textContent = "Sincronizando pedidos...";

    try {
        const res = await fetch("/sincronizar", {
            method: "POST",
            headers: { "X-Requested-With": "fetch" }
        });
        const data = await res.json();

        if (data.success) {
            syncMsg.textContent = "✔ Sync concluído";
            location.reload();
        } else {
            syncMsg.textContent = "❌ " + (data.message || "Erro");
        }
    } catch {
        syncMsg.textContent = "❌ Falha conexão";
    }

    syncBtn.disabled = false;
    syncPill.style.display = "none";
});