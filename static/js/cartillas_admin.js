/* ═══════════════════════════════════════════════════
   BINGO PRO — cartillas_admin.js  v4.0
   Made by Renso Ramirez  |  Fixed & Enhanced by Claude
═══════════════════════════════════════════════════ */

// ── COLORES ───────────────────────────────────────
const GC = [
  {fg:'#5dade2',bg:'#0a1e2e'},  // B  1-15
  {fg:'#f4d03f',bg:'#1e1500'},  // I  16-30
  {fg:'#f1948a',bg:'#2a0805'},  // N  31-45
  {fg:'#58d68d',bg:'#051e0f'},  // G  46-60
  {fg:'#a569bd',bg:'#160525'},  // O  61-75
];
const COL_LABELS = ['B (1-15)','I (16-30)','N (31-45)','G (46-60)','O (61-75)'];
const COL_RANGES_MANUAL = [
  [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],
  [16,17,18,19,20,21,22,23,24,25,26,27,28,29,30],
  [31,32,33,34,35,36,37,38,39,40,41,42,43,44,45],
  [46,47,48,49,50,51,52,53,54,55,56,57,58,59,60],
  [61,62,63,64,65,66,67,68,69,70,71,72,73,74,75],
];

// ── ESTADO ────────────────────────────────────────
let allCartillas = [];
let checkResults = {};
let drawnNums    = [];
let selectedNums = new Set();

// ── CARGAR CARTILLAS ──────────────────────────────
async function loadCartillas() {
  try {
    const [cRes, sRes] = await Promise.all([
      fetch('/api/cartilla/list'),
      fetch('/api/state'),
    ]);
    const cData = await cRes.json();
    const sData = await sRes.json();
    allCartillas = cData.cartillas || [];
    drawnNums    = sData.drawn     || [];
    document.getElementById('st-drawn').textContent     = drawnNums.length;
    document.getElementById('st-cartillas').textContent = allCartillas.length;
    renderTable(allCartillas);
  } catch(e) {
    showToast('❌ Error al cargar cartillas');
  }
}

// ── RENDER TABLA ──────────────────────────────────
function renderTable(list) {
  const tbody = document.getElementById('cartilla-tbody');
  document.getElementById('count-label').textContent =
    `${list.length} cartilla${list.length !== 1 ? 's' : ''}`;

  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="6"
      style="text-align:center;color:var(--muted);padding:30px;">
      No hay cartillas generadas todavía.</td></tr>`;
    return;
  }

  tbody.innerHTML = list.map(c => {
    const r      = checkResults[c.id] || {};
    const bingo  = r.bingo;
    const linea  = r.linea && !r.bingo;
    const marked = r.marked ?? '—';
    const total  = r.total  ?? 15;
    const badge  = bingo
      ? `<span class="badge badge-bingo">🎉 BINGO</span>`
      : linea
        ? `<span class="badge badge-linea">⭐ LÍNEA</span>`
        : `<span class="badge badge-none">—</span>`;
    const dateStr = c.created ? c.created.slice(0,16).replace('T',' ') : '—';

    return `<tr data-id="${c.id}"
                data-nombre="${escHtml(c.nombre).toLowerCase()}"
                data-status="${bingo ? 'bingo' : linea ? 'linea' : 'none'}">
      <td><span class="id-badge">${c.id}</span></td>
      <td style="font-weight:600;">${escHtml(c.nombre)}</td>
      <td style="color:var(--muted);font-size:.78rem;">${dateStr}</td>
      <td>${badge}</td>
      <td><span class="num-marked">${marked}</span> / ${total}</td>
      <td>
        <div class="actions">
          <button class="btn btn-ghost btn-sm" onclick="viewCartilla('${c.id}')">👁 Ver</button>
          <a href="/api/cartilla/${c.id}/pdf" class="btn btn-pdf btn-sm" target="_blank">📄 PDF</a>
          <a href="/api/cartilla/${c.id}/png" class="btn btn-png btn-sm" target="_blank">🖼 PNG</a>
          <button class="btn btn-danger btn-sm" onclick="deleteCartilla('${c.id}')">🗑</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// ── FILTRO ────────────────────────────────────────
function filterTable() {
  const q      = document.getElementById('search-input').value.toLowerCase();
  const status = document.getElementById('filter-status').value;
  const rows   = document.querySelectorAll('#cartilla-tbody tr[data-id]');
  let visible  = 0;
  rows.forEach(row => {
    const matchQ = !q || row.dataset.nombre.includes(q) || row.dataset.id.toLowerCase().includes(q);
    const matchS = status === 'all' || row.dataset.status === status;
    row.style.display = matchQ && matchS ? '' : 'none';
    if (matchQ && matchS) visible++;
  });
  document.getElementById('count-label').textContent =
    `${visible} cartilla${visible !== 1 ? 's' : ''}`;
}

// ── GENERAR AUTOMÁTICO ────────────────────────────
async function generateCartillas() {
  const nombre = document.getElementById('inp-nombre').value.trim() || 'Jugador';
  const count  = parseInt(document.getElementById('inp-count').value) || 1;
  showToast('⏳ Generando cartillas…');
  try {
    const res  = await fetch('/api/cartilla/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nombre, count }),  // No code needed — admin bypass
    });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.error || 'Error al generar')); return; }
    await loadCartillas();
    showToast(`✅ ${data.cartillas.length} cartilla(s) generada(s)`);
    if (count === 1) viewCartilla(data.cartillas[0].id);
  } catch(e) {
    showToast('❌ Error al generar');
  }
}

// ── BATCH ─────────────────────────────────────────
function generateBatch() {
  document.getElementById('modal-batch').classList.add('show');
}
function closeBatch() {
  document.getElementById('modal-batch').classList.remove('show');
}

async function generateBatchSubmit() {
  const names = document.getElementById('batch-names').value
    .split('\n').map(n => n.trim()).filter(Boolean);
  if (!names.length) { showToast('Escribe al menos un nombre'); return; }
  showToast(`⏳ Generando ${names.length} cartillas…`);
  closeBatch();
  let success = 0;
  for (const nombre of names) {
    try {
      const res = await fetch('/api/cartilla/generate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ nombre, count: 1 }),
      });
      if (res.ok) success++;
    } catch(e) { /* skip */ }
  }
  await loadCartillas();
  showToast(`✅ ${success} cartilla(s) generada(s)`);
}

// ── ELIMINAR ──────────────────────────────────────
async function deleteCartilla(cid) {
  if (!confirm(`¿Eliminar cartilla ${cid}?`)) return;
  try {
    const res = await fetch(`/api/cartilla/${cid}/delete`, { method: 'DELETE' });
    if (!res.ok) { showToast('❌ No autorizado'); return; }
    delete checkResults[cid];
    await loadCartillas();
    showToast('🗑️ Cartilla eliminada');
  } catch(e) {
    showToast('❌ Error al eliminar');
  }
}

async function deleteAll() {
  if (!confirm('¿Eliminar TODAS las cartillas? Esta acción no se puede deshacer.')) return;
  try {
    const res = await fetch('/api/cartilla/delete_all', { method: 'DELETE' });
    if (!res.ok) { showToast('❌ No autorizado'); return; }
    checkResults = {};
    await loadCartillas();
    showToast('🗑️ Todas las cartillas eliminadas');
  } catch(e) {
    showToast('❌ Error al eliminar');
  }
}

// ── VER CARTILLA ──────────────────────────────────
async function viewCartilla(cid) {
  try {
    const [cRes, sRes, chkRes] = await Promise.all([
      fetch(`/api/cartilla/${cid}`),
      fetch('/api/state'),
      fetch(`/api/cartilla/${cid}/check`),
    ]);
    const c   = await cRes.json();
    const s   = await sRes.json();
    const chk = await chkRes.json();
    const drawnSet = new Set(s.drawn || []);

    const bingo = chk.bingo;
    const linea = chk.linea && !chk.bingo;

    let alertHtml = '';
    if (bingo) {
      alertHtml = `<div class="winner-alert show">
        <h2>🎉 ¡BINGO COMPLETO!</h2>
        <p style="color:var(--muted);">Todos los números de esta cartilla fueron sorteados.</p>
      </div>`;
    } else if (linea) {
      alertHtml = `<div class="linea-alert show">
        <strong>⭐ ¡LÍNEA!</strong> La fila ${(chk.linea_row ?? 0) + 1} está completa.
      </div>`;
    }

    const headerHtml = COL_LABELS.map((lbl, ci) =>
      `<div class="c-col-label" style="color:${GC[ci].fg};background:${GC[ci].bg};">${lbl}</div>`
    ).join('');

    let cellsHtml = '';
    for (let ri = 0; ri < 5; ri++) {
      for (let ci = 0; ci < 5; ci++) {
        const num = c.grid[ri][ci];
        const g   = GC[ci];
        if (num === null) {
          cellsHtml += `<div class="c-cell empty"></div>`;
        } else if (drawnSet.has(num)) {
          cellsHtml += `<div class="c-cell marked"
            style="color:${g.fg};background:${g.bg};border-color:${g.fg};">
            <span style="background:${g.fg};color:#060d12;width:80%;height:80%;
              display:flex;align-items:center;justify-content:center;
              border-radius:50%;font-size:.9rem;">${num}</span>
          </div>`;
        } else {
          cellsHtml += `<div class="c-cell filled"
            style="color:${g.fg}44;background:rgba(17,31,46,.8);">${num}</div>`;
        }
      }
    }

    document.getElementById('modal-body').innerHTML = `
      ${alertHtml}
      <div style="display:flex;justify-content:space-between;align-items:flex-start;
                  flex-wrap:wrap;gap:12px;margin-bottom:16px;">
        <div>
          <h2 style="font-family:'Bebas Neue',sans-serif;font-size:2rem;
                     color:var(--accent);letter-spacing:2px;">${escHtml(c.nombre)}</h2>
          <p style="color:var(--muted);font-size:.85rem;">
            Cartilla <strong style="color:var(--text);">#${c.id}</strong>
            &nbsp;·&nbsp; Generada: ${c.created?.slice(0,16).replace('T',' ')}
          </p>
          <p style="color:var(--muted);font-size:.85rem;margin-top:4px;">
            Marcadas: <strong style="color:var(--accent);">${chk.marked} / ${chk.total}</strong>
            &nbsp;·&nbsp; Sorteadas en juego: <strong>${s.drawn?.length || 0}</strong>
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <a href="/api/cartilla/${cid}/pdf" class="btn btn-pdf btn-sm" target="_blank">📄 PDF</a>
          <a href="/api/cartilla/${cid}/png" class="btn btn-png btn-sm" target="_blank">🖼 PNG</a>
        </div>
      </div>
      <div class="cartilla-preview">
        <div class="c-header-row">${headerHtml}</div>
        <div class="c-grid">${cellsHtml}</div>
      </div>
      <div style="margin-top:14px;font-size:.78rem;color:var(--muted);text-align:center;">
        ✅ Marcados en verde · ⬜ Pendientes en gris
      </div>
    `;
    document.getElementById('modal-overlay').classList.add('show');
  } catch(e) {
    showToast('❌ Error al cargar cartilla');
  }
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
}

// ── VERIFICAR TODAS ───────────────────────────────
async function checkAll() {
  showToast('🔍 Verificando…');
  try {
    const res  = await fetch('/api/cartilla/check_all');
    const data = await res.json();

    if (!res.ok) { showToast('❌ Error al verificar'); return; }

    checkResults = {};
    let bingos = 0, lineas = 0;
    const winners = [];

    (data.results || []).forEach(r => {
      checkResults[r.id] = r;
      if (r.bingo)      { bingos++; winners.push({...r, type:'bingo'}); }
      else if (r.linea) { lineas++; winners.push({...r, type:'linea'}); }
    });

    document.getElementById('st-bingos').textContent = bingos;
    document.getElementById('st-lineas').textContent = lineas;
    document.getElementById('st-drawn').textContent  = data.drawn_count || drawnNums.length;

    const wList = document.getElementById('winners-list');
    if (winners.length) {
      wList.innerHTML = winners.map(w => `
        <div style="display:flex;justify-content:space-between;align-items:center;
          padding:8px 12px;margin-bottom:6px;border-radius:8px;
          background:${w.type==='bingo'?'rgba(0,229,180,.08)':'rgba(246,195,67,.08)'};
          border:1px solid ${w.type==='bingo'?'var(--accent)':'var(--warning)'};">
          <span>
            <strong>${w.type==='bingo' ? '🎉 BINGO' : '⭐ LÍNEA'}</strong>
            &nbsp; ${escHtml(w.nombre)}
          </span>
          <span style="color:var(--muted);font-size:.78rem;">#${w.id} · ${w.marked}/${w.total}</span>
        </div>`
      ).join('');
    } else {
      wList.innerHTML = `<p style="color:var(--muted);font-size:.85rem;">
        Ninguna cartilla tiene premio todavía. Sorteadas: ${data.drawn_count || 0}/75</p>`;
    }

    renderTable(allCartillas);
    showToast(`✅ Verificado: ${bingos} bingo(s), ${lineas} línea(s)`);
  } catch(e) {
    showToast('❌ Error al verificar');
  }
}

// ── TABS ──────────────────────────────────────────
function switchTab(tab) {
  document.getElementById('tab-auto').classList.toggle('active',   tab === 'auto');
  document.getElementById('tab-manual').classList.toggle('active', tab === 'manual');
  document.getElementById('tab-auto-btn').classList.toggle('active',   tab === 'auto');
  document.getElementById('tab-manual-btn').classList.toggle('active', tab === 'manual');
}

// ── MANUAL PICKER ─────────────────────────────────
function buildPicker() {
  const wrap = document.getElementById('picker-wrap');
  if (!wrap) return;
  // N column label shows FREE reminder
  const labels = COL_LABELS.map((lbl, ci) =>
    ci === 2 ? lbl + ' · libre centro' : lbl
  );
  wrap.innerHTML = `<div class="picker-grid">${
    COL_RANGES_MANUAL.map((nums, ci) => `
      <div class="picker-col">
        <div class="picker-col-label" style="color:${GC[ci].fg};background:${GC[ci].bg};">
          ${labels[ci]}
        </div>
        ${nums.map(n => `
          <div class="p-num" id="pn-${n}" style="color:${GC[ci].fg};"
               onclick="toggleNum(${n},${ci})">${n}</div>`).join('')}
      </div>`).join('')
  }</div>`;
}

function toggleNum(n, ci) {
  const el = document.getElementById(`pn-${n}`);
  if (!el) return;
  if (selectedNums.has(n)) {
    selectedNums.delete(n);
    el.classList.remove('selected');
    el.style.background  = 'var(--card)';
    el.style.borderColor = '';
  } else {
    if (selectedNums.size >= 24) { showToast('⚠️ Máximo 24 números'); return; }
    selectedNums.add(n);
    el.classList.add('selected');
    el.style.background  = GC[ci].bg;
    el.style.borderColor = GC[ci].fg;
  }
  updatePickerUI();
}

function clearPicker() {
  selectedNums.forEach(n => {
    const el = document.getElementById(`pn-${n}`);
    if (el) { el.classList.remove('selected'); el.style.background = 'var(--card)'; el.style.borderColor = ''; }
  });
  selectedNums.clear();
  updatePickerUI();
}

function updatePickerUI() {
  const count = selectedNums.size;
  const disp  = document.getElementById('picker-count-display');
  if (disp) {
    disp.innerHTML  = `Seleccionados: <span>${count}</span> / 24`;
    disp.className  = `picker-count${count > 24 ? ' warn' : ''}`;
  }

  const colCounts = COL_RANGES_MANUAL.map(range =>
    range.filter(n => selectedNums.has(n)).length
  );
  // B,I,G,O need 5 each; N needs 4 (FREE center)
  const needed = [5, 5, 4, 5, 5];
  const errors = [], ok = [];

  if (count < 24) errors.push(`⏳ Faltan ${24 - count} número(s)`);
  else            ok.push('✅ Total: 24 números');

  colCounts.forEach((c, i) => {
    const n = needed[i];
    if (c > n) errors.push(`❌ ${COL_LABELS[i]}: máx ${n} (tienes ${c})`);
    else if (count === 24 && c < n) errors.push(`❌ ${COL_LABELS[i]}: necesitas ${n} (tienes ${c})`);
  });

  const rules = document.getElementById('picker-rules');
  if (rules) {
    rules.innerHTML = [...ok, ...errors].map(r =>
      `<div class="${r.startsWith('✅') ? 'rule-ok' : r.startsWith('⏳') ? 'rule-warn' : 'rule-err'}">${r}</div>`
    ).join('') || 'Selecciona tus números.';
  }

  const allOk = count === 24 && errors.length === 0;
  const btn   = document.getElementById('btn-save-manual');
  if (btn) btn.disabled = !allOk;
}


async function saveManualCartilla() {
  const nombre = document.getElementById('inp-nombre').value.trim() || 'Jugador';
  if (selectedNums.size !== 24) { showToast('⚠️ Selecciona exactamente 24 números'); return; }

  // Build 5×5 BINGO 75 grid: each column contains its range's numbers sorted by row
  // N column (ci=2) has FREE space at row 2 (center)
  const needed = [5, 5, 4, 5, 5];
  const grid = Array.from({ length: 5 }, () => Array(5).fill(null));
  for (let ci = 0; ci < 5; ci++) {
    const colNums = COL_RANGES_MANUAL[ci].filter(n => selectedNums.has(n)).sort((a, b) => a - b);
    if (colNums.length !== needed[ci]) {
      showToast(`⚠️ ${COL_LABELS[ci]}: necesitas exactamente ${needed[ci]} números`);
      return;
    }
    if (ci === 2) {
      // N column: rows 0,1,3,4 used; row 2 = FREE (null)
      [0, 1, 3, 4].forEach((row, i) => { grid[row][ci] = colNums[i]; });
    } else {
      colNums.forEach((n, row) => { grid[row][ci] = n; });
    }
  }

  showToast('⏳ Guardando cartilla…');
  try {
    const res  = await fetch('/api/cartilla/save_manual', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nombre, grid }),  // No code needed — admin bypass
    });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.error || 'Error al guardar')); return; }
    clearPicker();
    await loadCartillas();
    showToast('✅ Cartilla guardada correctamente');
    viewCartilla(data.cartilla.id);
  } catch(e) {
    showToast('❌ Error al guardar cartilla');
  }
}

// ── HELPERS ───────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let toastJob = null;
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastJob);
  toastJob = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── INIT ──────────────────────────────────────────
buildPicker();
loadCartillas();
setInterval(loadCartillas, 10000);
