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
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = 'sine';
    o.frequency.setValueAtTime(880, ctx.currentTime);
    g.gain.setValueAtTime(0.35, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
    o.start(ctx.currentTime);
    o.stop(ctx.currentTime + 0.18);
  } catch { }
}

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
      o.start(t);
      o.stop(t + 0.2);
    });
  } catch { }
}


// =========================
// AGRUPAR PRODUTOS PARECIDOS
// =========================
function normalizeBaseName(name) {
  if (!name) return "";
  let s = String(name).toLowerCase();
  s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  s = s.replace(/\b(tamanho|tam|nº|n\.|no\.|numero|número)\b/g, ' ');
  s = s.replace(/\b\d+([.,]\d+)?\b/g, ' ');
  s = s.replace(/[|:/\-\(\)\[\],.]+/g, ' ');
  s = s.replace(/\s+/g, ' ').trim();
  const tokens = s.split(' ').filter(Boolean);
  return tokens.slice(0, 4).join(' ');
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
    block.dataset.open = block.dataset.open === "1" ? "0" : "1";
  });
  return block;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // não zera CURRENT_ML_ID ao mudar de aba — só pausa
  } else {
    focusScanner();
  }
});

function groupSimilarProducts() {
  document.querySelectorAll('.order').forEach(order => {
    const zone = order.querySelector('.normalZone');
    if (!zone) return;
    const rows = Array.from(zone.querySelectorAll('.itemRow'));
    if (rows.length <= 1) return;

    const map = new Map();
    rows.forEach(row => {
      const name = row.dataset.itemName || row.querySelector('.itemName')?.textContent || '';
      if (!name) return;
      const key = normalizeBaseName(name);
      const qty = Number(row.querySelector('.qtot')?.textContent || 0);
      if (!map.has(key)) map.set(key, { rows: [], qtySum: 0, firstName: name });
      const g = map.get(key);
      g.rows.push(row);
      g.qtySum += qty;
    });

    if (map.size <= 1) return;

    const fragment = document.createDocumentFragment();
    Array.from(map.entries())
      .sort((a, b) => b[1].qtySum - a[1].qtySum)
      .forEach(([_, g]) => {
        if (g.rows.length === 1) { fragment.appendChild(g.rows[0]); return; }
        const block = buildGroupBlock(g.firstName, g.qtySum);
        const body = block.querySelector('.groupBody');
        g.rows.forEach(r => body.appendChild(r));
        fragment.appendChild(block);
      });

    zone.innerHTML = '';
    zone.appendChild(fragment);
  });
}

window.addEventListener('DOMContentLoaded', () => {
  groupSimilarProducts();
  setTimeout(groupSimilarProducts, 300);
  loadPedidoAtual();
});


// =========================
// SCANNER CONFERÊNCIA
// =========================
const scannerInput = document.getElementById('scannerInput');
const focusBtn = document.getElementById('focusBtn');
const clearBtn = document.getElementById('clearBtn');
const scanStatus = document.getElementById('scanStatus');
const pedidoAtualTitle = document.getElementById('pedidoAtualTitle');
const faltasText = document.getElementById('faltasText');

let CURRENT_ML_ID = null;

// ============================================================
// LOCK: uma vez escolhido o pedido, trava até terminar
// ============================================================
let LOCKED_ML_ID = null;  // pedido travado

function _setLock(mlId) {
  LOCKED_ML_ID = mlId;
  CURRENT_ML_ID = mlId;
  _atualizarBannerLock();
}

function _clearLock() {
  LOCKED_ML_ID = null;
  _atualizarBannerLock();
}

function _atualizarBannerLock() {
  const banner = document.getElementById('lockBanner');
  if (!banner) return;
  if (LOCKED_ML_ID) {
    banner.style.display = 'flex';
    const lbl = document.getElementById('lockLabel');
    if (lbl) lbl.textContent = `🔒 Bipando: ${LOCKED_ML_ID}`;
  } else {
    banner.style.display = 'none';
  }
}

// Botão para liberar o lock manualmente
document.getElementById('btnLiberarLock')?.addEventListener('click', () => {
  _clearLock();
  CURRENT_ML_ID = null;
  setStatus("Lock liberado", "Próximo bipe descobrirá o pedido.", "warn");
});


// Seleção manual de card
document.querySelectorAll(".order-card").forEach(card => {
  card.addEventListener("click", () => {
    document.querySelectorAll(".order-card").forEach(c => c.classList.remove("selected"));
    card.classList.add("selected");
    CURRENT_ML_ID = card.dataset.mlId;
    _setLock(CURRENT_ML_ID);
  });
});

let BIP_LOCK = false;

function focusScanner() { try { scannerInput?.focus(); } catch { } }

window.addEventListener('load', () => {
  focusScanner();
  setInterval(() => { focusScanner(); }, 3000);
  setTimeout(focusScanner, 300);
  setTimeout(focusScanner, 1000);
  verificarTodosPedidos();
  _atualizarBannerLock();
});

focusBtn?.addEventListener('click', e => { e.preventDefault(); focusScanner(); });
clearBtn?.addEventListener('click', e => { e.preventDefault(); scannerInput.value = ''; focusScanner(); });

window.addEventListener('click', (e) => {
  if (!e.target.closest('#scannerInput')) return;
  focusScanner();
});


// ========================
// FILTRO POR TIPO ENVIO (Tela 2)
// ========================
function aplicarFiltroScanner() {
  const sel = document.getElementById('tipoFilterScanner');
  if (!sel) return;
  const val = sel.value || 'ALL';

  document.querySelectorAll('.order-card[data-envio]').forEach(card => {
    const envio = (card.dataset.envio || '').toUpperCase();
    let show = true;
    if (val === 'FLEX') show = envio === 'FLEX';
    else if (val === 'COLETA') show = envio.includes('COLETA');
    card.style.display = show ? '' : 'none';
  });
}

document.getElementById('tipoFilterScanner')?.addEventListener('change', aplicarFiltroScanner);


// ---------------- STATUS UI ----------------
function setStatus(title, sub, type = "idle") {
  if (!scanStatus) return;
  const icon = type === "ok" ? "✅" : type === "warn" ? "⚠️" : type === "err" ? "⛔" : "⏳";
  scanStatus.innerHTML = `<div class="title">${icon} ${title || ""}</div><div class="sub">${sub || ""}</div>`;
}


// ---------------- CARREGAR PEDIDO ----------------
async function loadPedidoAtual() {
  document.body.classList.remove("pedido-finalizado");

  try {
    setStatus("Carregando pedido…", "Buscando próximo pedido…", "idle");
    const mlParam = LOCKED_ML_ID || CURRENT_ML_ID;
    if (!mlParam) {
      document.getElementById('kitItensBox').style.display = 'none';
      pedidoAtualTitle.textContent = '—';
      faltasText.textContent = 'Faltas: —';
      return;
    }
    const url = `/scanner/pedido_atual?ml_id=${mlParam}`;
    const res = await fetch(url, { headers: { 'X-Requested-With': 'fetch' } });
    const data = await res.json();

    if (!data.success) {
      CURRENT_ML_ID = null;
      _clearLock();
      pedidoAtualTitle.textContent = "Nenhum pedido SEPARADO";
      faltasText.textContent = "Faltas: —";
      setStatus("Aguardando bip…", "Sem pedidos agora.", "warn");
      return;
    }

    CURRENT_ML_ID = data.pedido.ml_id;
    _setLock(CURRENT_ML_ID);
    pedidoAtualTitle.textContent = `${CURRENT_ML_ID} • ${data.pedido.cliente_nome}`;
    faltasText.textContent = "Faltas: —";
    // Mostra itens do pedido no painel
    const kitBox = document.getElementById("kitItensBox");
    const kitList = document.getElementById("kitItensList");
    const itens = data.pedido.itens || [];
    if (itens.length > 0) {
      kitList.innerHTML = itens.map(it => {
        const conf = it.quantidade_conferida || 0;
        const tot = it.quantidade || 1;
        const done = conf >= tot;
        const cor = done ? "#28a745" : "#f0ad4e";
        const ico = done ? "✅" : "⬜";
        return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;font-size:0.82rem;">
          <span>${ico}</span>
          <span style="flex:1;color:var(--text)">${it.nome}</span>
          <span style="color:${cor};font-weight:700;">${conf}/${tot}</span>
        </div>`;
      }).join("");
      kitBox.style.display = "block";
    } else {
      kitBox.style.display = "none";
    }
    setStatus("Aguardando bip…", "Bipe um item.", "ok");

  } catch {
    setStatus("Erro", "Falha conexão", "err");
  }
}


// ---------------- MARCAR ITEM ----------------
function findItemRowBySku(sku) {
  if (!CURRENT_ML_ID) return null;
  return document.querySelector(
    `.item-row[data-ml-id="${CSS.escape(CURRENT_ML_ID)}"][data-sku="${CSS.escape(String(sku))}"]`
  );
}

function updateItemBip(sku) {
  const row = findItemRowBySku(sku);
  if (!row) return;
  const qb = row.querySelector('.qbip');
  const qt = row.querySelector('.qtot');
  if (!qb || !qt) return;
  let b = Number(qb.textContent || 0) + 1;
  qb.textContent = b;
  if (b >= Number(qt.textContent || 0)) {
    row.classList.add('is-checked');
  }
  // Atualiza painel do scanner
  const kitList = document.getElementById("kitItensList");
  if (kitList) {
    const divs = kitList.querySelectorAll("div");
    divs.forEach(div => {
      const nomeEl = div.querySelector("span:nth-child(2)");
      const contEl = div.querySelector("span:nth-child(3)");
      if (!contEl) return;
      const parts = contEl.textContent.split("/");
      if (parts.length < 2) return;
      // Encontra o item pela row do SKU
      const rowSku = row.dataset.sku;
      const rowNome = row.querySelector(".item-name");
      if (rowNome && nomeEl && rowNome.textContent.trim() === nomeEl.textContent.trim()) {
        let conf = Number(parts[0]) + 1;
        const tot = Number(parts[1]);
        contEl.textContent = `${conf}/${tot}`;
        contEl.style.color = conf >= tot ? "#28a745" : "#f0ad4e";
        div.querySelector("span:nth-child(1)").textContent = conf >= tot ? "✅" : "⬜";
      }
    });
  }
  verificarPedidoFinalizado(CURRENT_ML_ID);
}


// ---------------- BIPAR COM SELEÇÃO DE PEDIDO COM LOCK ----------------

async function processarBip(sku) {
  // Se já tem pedido travado, bipa direto
  if (LOCKED_ML_ID) {
    CURRENT_ML_ID = LOCKED_ML_ID;
    await biparSKU_Tela2(sku);
    return;
  }

  const res = await fetch("/scanner/descobrir_pedido", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sku })
  });

  const data = await res.json();

  if (!data.success || !data.pedidos.length) {
    beepErro();
    setStatus("Produto não pertence a pedido separado", sku, "err");
    return;
  }

  let pedidoEscolhido = null;

  if (data.pedidos.length === 1) {
    pedidoEscolhido = data.pedidos[0].ml_id;
  } else {
    pedidoEscolhido = await escolherPedidoModal(data.pedidos);
    if (!pedidoEscolhido) return;
  }

  // Trava no pedido escolhido
  _setLock(pedidoEscolhido);
  CURRENT_ML_ID = pedidoEscolhido;

  await biparSKU_Tela2(sku);
}


// ---------------- SELETOR MODAL (substitui prompt) ----------------

function escolherPedidoModal(pedidos) {
  return new Promise((resolve) => {
    // Remove modal anterior se existir
    document.getElementById('_pedidoSelectorModal')?.remove();

    const opts = pedidos.map((p, i) =>
      `<button class="btn btn-outline-primary btnx mb-2 w-100 text-start _pedido-opt" data-idx="${i}">
        <strong>${p.ml_id}</strong> — ${p.cliente_nome}
       </button>`
    ).join('');

    const div = document.createElement('div');
    div.id = '_pedidoSelectorModal';
    div.innerHTML = `
      <div style="
        position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:9998;
        display:flex; align-items:center; justify-content:center;
      ">
        <div style="
          background: var(--card-bg, #1a1a2e); border:1px solid var(--border, #333);
          border-radius:14px; padding:24px; min-width:320px; max-width:420px;
          box-shadow: 0 8px 40px rgba(0,0,0,0.4);
        ">
          <p style="font-weight:900; margin-bottom:14px; font-size:1rem;">
            📦 Mais de um pedido tem esse produto.<br>
            <span style="font-size:0.82rem; font-weight:400; opacity:0.7;">Selecione o pedido e ele ficará travado até finalizar.</span>
          </p>
          ${opts}
          <button class="btn btn-outline-secondary btnx mt-2 w-100" id="_cancelarSeletor">Cancelar</button>
        </div>
      </div>
    `;
    document.body.appendChild(div);

    div.querySelectorAll('._pedido-opt').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.dataset.idx);
        div.remove();
        resolve(pedidos[idx]?.ml_id || null);
      });
    });

    document.getElementById('_cancelarSeletor')?.addEventListener('click', () => {
      div.remove();
      resolve(null);
    });
  });
}

// Função legada mantida por compatibilidade
function escolherPedido(pedidos) {
  let msg = "Mais de um pedido possui esse produto:\n\n";
  pedidos.forEach((p, i) => { msg += `${i+1} - ${p.ml_id} (${p.cliente_nome})\n`; });
  msg += "\nDigite o número:";
  const escolha = prompt(msg);
  const index = Number(escolha) - 1;
  if (pedidos[index]) return pedidos[index].ml_id;
  return null;
}


function verificarTodosPedidos() {
  document.querySelectorAll(".order-card").forEach(card => {
    const mlId = card.dataset.mlId;
    const rows = card.querySelectorAll(".item-row");
    let faltando = 0;
    rows.forEach(r => {
      const qb = Number(r.querySelector('.qbip')?.textContent || 0);
      const qt = Number(r.querySelector('.qtot')?.textContent || 0);
      faltando += (qt - qb);
    });
    if (faltando <= 0) {
      card.classList.add("done-order");
      const container = card.parentElement;
      container.appendChild(card);
    }
  });
}

function verificarPedidoFinalizado(mlId) {
  const rows = document.querySelectorAll(`.item-row[data-ml-id="${mlId}"]`);
  let faltando = 0;
  rows.forEach(r => {
    const qb = Number(r.querySelector('.qbip')?.textContent || 0);
    const qt = Number(r.querySelector('.qtot')?.textContent || 0);
    faltando += (qt - qb);
  });
  if (faltando <= 0) {
    const card = document.querySelector(`.order-card[data-ml-id="${mlId}"]`);
    if (card) {
      card.classList.add("done-order");
      const container = card.parentElement;
      container.appendChild(card);
    }
  }
}

async function biparSKU_Tela2(sku) {
  if (BIP_LOCK) return;
  BIP_LOCK = true;

  sku = String(sku || '').trim().toUpperCase();
  if (!sku || !CURRENT_ML_ID) { BIP_LOCK = false; return; }

  setStatus("Processando…", `SKU: ${sku}`, "idle");

  try {
    const r = await fetch('/scanner/bipar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku, ml_id: String(CURRENT_ML_ID) })
    });

    const data = await r.json();

    if (!data.success) {
      beepErro();
      setStatus("Rejeitado", data.error || "Falha", "err");
      return;
    }

    beepSucesso();
    updateItemBip(sku);
    loadPedidoAtual();

    if (data.pedido_fechado) {
      const card = document.querySelector(`.order-card[data-ml-id="${CURRENT_ML_ID}"]`);
      if (card) {
        card.classList.add("done-order");
        const container = card.parentElement;
        container.appendChild(card);
      }

      setStatus("Pedido finalizado 📦", "Emitindo NF...", "ok");
      document.body.classList.add("pedido-finalizado");

      // Libera o lock — pedido concluído
      _clearLock();
      CURRENT_ML_ID = null;

      if (data.pdf) {
        const filename = data.pdf.split('/').pop();
        const url = `/print/${filename}`;

        if (window.__printingNow) return;
        window.__printingNow = true;
        setTimeout(() => window.__printingNow = false, 4000);

        const win = window.open(url, "_blank");
        if (win) {
          let tries = 0;
          const timer = setInterval(() => {
            tries++;
            try { win.focus(); win.print(); clearInterval(timer); } catch { }
            if (tries > 20) clearInterval(timer);
          }, 300);
        }
      }

      setTimeout(loadPedidoAtual, 2000);
      return;
    }

    let faltando = 0;
    document.querySelectorAll(`.item-row[data-ml-id="${CURRENT_ML_ID}"]`).forEach(r => {
      const qb = Number(r.querySelector('.qbip')?.textContent || 0);
      const qt = Number(r.querySelector('.qtot')?.textContent || 0);
      faltando += (qt - qb);
    });

    setStatus("OK", `Faltam ${faltando} unidades`, "ok");

  } catch {
    beepErro();
    setStatus("Erro", "Falha conexão", "err");
  } finally {
    BIP_LOCK = false;
  }
}


// input leitor
scannerInput?.addEventListener('keydown', async e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();

  const sku = scannerInput.value.trim();
  scannerInput.value = '';

  if (!sku) return;

  await processarBip(sku);
  focusScanner();
});
