/* ═══════════════════════════════════════════════════
   BINGO PRO — cartillas_player.js  v4.0
   Made by Renso Ramirez  |  Fixed & Enhanced by Claude
═══════════════════════════════════════════════════ */

const COL_LABELS = ['1-9','10-19','20-29','30-39','40-49','50-59','60-69','70-79','80-90'];
const COL_RANGES = [
  [1,2,3,4,5,6,7,8,9],
  [10,11,12,13,14,15,16,17,18,19],
  [20,21,22,23,24,25,26,27,28,29],
  [30,31,32,33,34,35,36,37,38,39],
  [40,41,42,43,44,45,46,47,48,49],
  [50,51,52,53,54,55,56,57,58,59],
  [60,61,62,63,64,65,66,67,68,69],
  [70,71,72,73,74,75,76,77,78,79],
  [80,81,82,83,84,85,86,87,88,89,90],
];

let selected = new Set();

// ── TOAST ─────────────────────────────────────────
let toastJob = null;
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastJob);
  toastJob = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── NAVEGACIÓN ────────────────────────────────────
function goToGame() { location.href = '/'; }

// ── CHECK JUEGO INICIADO ──────────────────────────
async function checkStarted() {
  try {
    const s = await (await fetch('/api/state')).json();
    if ((s.drawn || []).length > 0) {
      document.getElementById('started-overlay').classList.add('show');
      setTimeout(() => { location.href = '/'; }, 4000);
    }
  } catch(e) { /* silent */ }
}

// ── TOGGLE MANUAL ─────────────────────────────────
function toggleManual() {
  const area = document.getElementById('manual-area');
  if (!area) return;
  const isHidden = area.style.display === 'none' || !area.style.display;
  area.style.display = isHidden ? 'block' : 'none';
}

// ── HELPERS ───────────────────────────────────────
function getCode()  { return (document.getElementById('inp-code')?.value  || '').trim().toUpperCase(); }
function getName()  { return (document.getElementById('inp-name')?.value  || '').trim() || 'Jugador'; }

// ── MY CARTILLAS (localStorage) ───────────────────
function saveMyCartilla(cid) {
  try {
    const arr = JSON.parse(localStorage.getItem('my_cartillas') || '[]');
    if (!arr.includes(cid)) arr.unshift(cid);
    localStorage.setItem('my_cartillas', JSON.stringify(arr.slice(0, 30)));
  } catch(e) { /* storage may be unavailable */ }
}

function loadMyCartillas() {
  const listEl = document.getElementById('mylist');
  if (!listEl) return;

  let arr = [];
  try { arr = JSON.parse(localStorage.getItem('my_cartillas') || '[]'); } catch(e) {}

  if (!arr.length) {
    listEl.innerHTML = '<div style="color:var(--muted);margin-top:10px;">Aún no has generado cartillas.</div>';
    return;
  }

  listEl.innerHTML = arr.map(cid => {
    const pdf = `/api/cartilla/${cid}/pdf`;
    const png = `/api/cartilla/${cid}/png`;
    const msg = encodeURIComponent(`¡Hola! Esta es mi cartilla de Bingo.\nID: ${cid}\nDescarga tu cartilla en PNG o PDF desde el servidor.`);
    const wa  = `https://wa.me/?text=${msg}`;
    return `<div class="myitem">
      <div class="meta">
        <div style="font-weight:900;font-size:1rem;">Cartilla ${cid}</div>
        <div class="hint">Guarda el ID — lo necesitarás para verificar</div>
      </div>
      <div class="actions">
        <a class="btn btn-ghost btn-sm" href="${png}" target="_blank" rel="noopener">🖼 PNG</a>
        <a class="btn btn-ghost btn-sm" href="${pdf}" target="_blank" rel="noopener">📄 PDF</a>
        <a class="btn btn-sm" href="${wa}" target="_blank" rel="noopener">📲 WhatsApp</a>
        <button class="btn btn-sm" style="background:var(--danger);color:white;"
                onclick="removeMyCartilla('${cid}')">✕</button>
      </div>
    </div>`;
  }).join('');
}

function removeMyCartilla(cid) {
  try {
    let arr = JSON.parse(localStorage.getItem('my_cartillas') || '[]');
    arr = arr.filter(c => c !== cid);
    localStorage.setItem('my_cartillas', JSON.stringify(arr));
    loadMyCartillas();
    showToast('🗑️ Cartilla eliminada de tu lista');
  } catch(e) { /* storage may be unavailable */ }
}

// ── GENERAR AUTOMÁTICO ────────────────────────────
async function generateAuto() {
  const code = getCode();
  if (!code) { showToast('🔑 Ingresa el código del administrador'); return; }
  const nombre = getName();
  showToast('⏳ Generando tu cartilla…');

  try {
    const res  = await fetch('/api/cartilla/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nombre, count: 1, code }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msgs = {
        bad_code:     '❌ Código inválido. Pide uno al administrador.',
        used_code:    '⚠️ Ese código ya fue usado.',
        game_started: '⛔ El juego ya empezó. No se pueden crear más cartillas.',
      };
      showToast(msgs[data.error] || '❌ ' + (data.error || 'Error'));
      return;
    }
    const cid = data.cartillas[0].id;
    saveMyCartilla(cid);
    showToast(`✅ ¡Cartilla ${cid} creada!`);
    loadMyCartillas();
    // Clear the code so it can't be reused in the UI
    document.getElementById('inp-code').value = '';
  } catch(e) {
    showToast('❌ Error de conexión');
  }
}

// ── PICKER MANUAL ─────────────────────────────────
function buildPicker() {
  const picker = document.getElementById('picker');
  if (!picker) return;
  picker.innerHTML = '';
  picker.style.display = 'grid';
  picker.style.gridTemplateColumns = 'repeat(9, 1fr)';
  picker.style.gap = '8px';

  for (let ci = 0; ci < 9; ci++) {
    const col = document.createElement('div');
    col.style.cssText = 'border:1px solid var(--border);border-radius:14px;background:var(--card);padding:10px;';

    const h = document.createElement('div');
    h.textContent = COL_LABELS[ci];
    h.style.cssText = 'color:var(--muted);font-weight:800;margin-bottom:8px;font-size:.8rem;text-align:center;';
    col.appendChild(h);

    for (const n of COL_RANGES[ci]) {
      const b = document.createElement('button');
      b.textContent = n;
      b.className = 'btn btn-ghost btn-sm';
      b.style.cssText = 'width:100%;margin-bottom:6px;';
      b.onclick = () => toggleNum(n, ci, b);
      col.appendChild(b);
    }
    picker.appendChild(col);
  }
}

function toggleNum(n, ci, btn) {
  if (selected.has(n)) {
    selected.delete(n);
    btn.style.borderColor = '';
    btn.style.color       = '';
    btn.style.background  = '';
    return;
  }
  if (selected.size >= 15) { showToast('⚠️ Máximo 15 números'); return; }
  selected.add(n);

  const colors = ['#5dade2','#f4d03f','#f1948a','#e59866','#58d68d','#a569bd','#48c9b0','#7fb3d3','#95a5a6'];
  btn.style.borderColor = colors[ci];
  btn.style.color       = colors[ci];
}

function clearManual() {
  selected = new Set();
  buildPicker();
  showToast('🧹 Selección limpiada');
}

function selectedToGrid() {
  if (selected.size !== 15) throw new Error('Selecciona exactamente 15 números');

  const cols = Array.from({ length: 9 }, () => []);
  [...selected].sort((a, b) => a - b).forEach(n => {
    const ci = n <= 9 ? 0 : Math.min(8, Math.floor(n / 10));
    cols[ci].push(n);
  });

  for (const arr of cols) {
    if (arr.length > 2) throw new Error('Cada columna puede tener máximo 2 números');
  }

  const grid      = Array.from({ length: 3 }, () => Array(9).fill(null));
  const rowCounts = [0, 0, 0];

  for (let ci = 0; ci < 9; ci++) {
    const nums = cols[ci].slice().sort((a, b) => a - b);
    if (nums.length === 0) continue;

    const rows = [0, 1, 2].sort((r1, r2) => rowCounts[r1] - rowCounts[r2]);
    if (nums.length === 1) {
      const r = rows[0];
      grid[r][ci] = nums[0];
      rowCounts[r]++;
    } else {
      const r1 = rows[0], r2 = rows[1];
      grid[r1][ci] = nums[0]; rowCounts[r1]++;
      grid[r2][ci] = nums[1]; rowCounts[r2]++;
    }
  }

  if (!(rowCounts[0] === 5 && rowCounts[1] === 5 && rowCounts[2] === 5)) {
    throw new Error('Tu selección no permite 5 números por fila. Prueba otra combinación.');
  }
  return grid;
}

async function saveManual() {
  const code = getCode();
  if (!code)             { showToast('🔑 Ingresa el código del administrador'); return; }
  if (selected.size !== 15) { showToast('⚠️ Selecciona exactamente 15 números'); return; }

  let grid;
  try { grid = selectedToGrid(); }
  catch(e) { showToast('❌ ' + e.message); return; }

  const nombre = getName();
  showToast('⏳ Guardando…');

  try {
    const res  = await fetch('/api/cartilla/save_manual', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nombre, grid, code }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msgs = {
        bad_code:     '❌ Código inválido.',
        used_code:    '⚠️ Ese código ya fue usado.',
        game_started: '⛔ El juego ya empezó.',
      };
      showToast(msgs[data.error] || '❌ ' + (data.error || 'Error'));
      return;
    }
    const cid = data.cartilla.id;
    saveMyCartilla(cid);
    showToast(`✅ Cartilla ${cid} guardada`);
    clearManual();
    loadMyCartillas();
    document.getElementById('inp-code').value = '';
  } catch(e) {
    showToast('❌ Error de conexión');
  }
}

// ── INIT ──────────────────────────────────────────
buildPicker();
loadMyCartillas();
checkStarted();
setInterval(checkStarted, 3000);
