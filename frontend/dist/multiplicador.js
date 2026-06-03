(function() {
  const LIMITE = 20;
  const originalFetch = window.fetch;
  
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isBipe = url.includes('/verificar_e_bipar') || url.includes('/scanner/bipar') || url.includes('/bipar-produto');
    
    if (isBipe && init && init.method === 'POST' && init.body) {
      console.log('🎯 BIPE detectado:', url);
      try {
        const body = JSON.parse(init.body);
        console.log('Body:', body);
        const sku = (body.sku || body.codigo || '').trim();
        
        if (sku && !body._multiplied) {
          return originalFetch('/api/produtos-pendentes', {credentials: 'include'})
            .then(r => r.json())
            .then(data => {
              let maxQtd = 0;
              const list = data.produtos || data.items || (Array.isArray(data) ? data : []);
              for (const p of list) {
                const pSku = String(p.sku || '').toUpperCase();
                const pParent = String(p.parent_sku || '').toUpperCase();
                if (pSku === sku.toUpperCase() || pParent === sku.toUpperCase()) {
                  const falta = (p.quantidade_total || p.quantidade || 0) - (p.quantidade_bipada || 0);
                  if (falta > maxQtd) maxQtd = falta;
                }
                if (p.componentes) {
                  for (const c of p.componentes) {
                    if (String(c.sku).toUpperCase() === sku.toUpperCase()) {
                      const falta = (c.quantidade_total || c.quantidade || 0) - (c.quantidade_bipada || 0);
                      if (falta > maxQtd) maxQtd = falta;
                    }
                  }
                }
              }
              console.log('maxQtd:', maxQtd);
              
              if (maxQtd > LIMITE) {
                const qtd = prompt('SKU: ' + sku + '\nFaltam ' + maxQtd + ' unidades.\n\nQuantas bipar?', String(maxQtd));
                if (qtd === null) return Promise.reject(new Error('Cancelado'));
                const num = parseInt(qtd) || 1;
                
                // Para múltiplas unidades: faz N requisições sequenciais
                if (num > 1) {
                  body._multiplied = true;
                  const newInit = Object.assign({}, init, {body: JSON.stringify(body)});
                  let promise = originalFetch(input, newInit);
                  for (let i = 1; i < num; i++) {
                    promise = promise.then(() => originalFetch(input, newInit));
                  }
                  return promise;
                }
              }
              body._multiplied = true;
              const newInit = Object.assign({}, init, {body: JSON.stringify(body)});
              return originalFetch(input, newInit);
            });
        }
      } catch(e) {
        console.error('Erro:', e);
      }
    }
    return originalFetch.apply(this, arguments);
  };
  console.log('✅ Multiplicador ativo (intercepta /verificar_e_bipar)');
})();
