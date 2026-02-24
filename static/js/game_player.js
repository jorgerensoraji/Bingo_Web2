/* ═══════════════════════════════════════════════════
   BINGO PRO — game_player.js  v4.1
   Pantalla de espectador/jugador — SOLO lectura.
   No tiene controles de admin, no verifica sesión,
   simplemente sincroniza el estado del juego cada 3s.
═══════════════════════════════════════════════════ */

const GROUP_COLORS = [
  { fg:'#5dade2', bg:'#0a1e2e' },
  { fg:'#f4d03f', bg:'#1e1500' },
  { fg:'#f1948a', bg:'#2a0805' },
  { fg:'#e59866', bg:'#2a1000' },
  { fg:'#58d68d', bg:'#051e0f' },
  { fg:'#a569bd', bg:'#160525' },
  { fg:'#48c9b0', bg:'#032420' },
  { fg:'#7fb3d3', bg:'#061320' },
  { fg:'#95a5a6', bg:'#0e1315' },
];
const GROUP_LABELS = ['1–9','10–19','20–29','30–39','40–49','50–59','60–69','70–79','80–90'];

let drawnLocal  = [];   // estado local sincronizado
let lastLocal   = null;
let clockJob    = null;
let elapsedSec  = 0;
let gameStarted = false;
let gameId = null;

// ── PLAYER CARTILLA (auto-mark) ───────────────────
let myCartillaId = null;
let myCartilla   = null;
let myBingoFired = false;

// ── INIT GRID ─────────────────────────────────────
function initGrid() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');

  GROUP_LABELS.forEach((lbl, g) => {
    const h = document.createElement('div');
    h.className   = 'group-header';
    h.textContent = lbl;
    h.style.color      = GROUP_COLORS[g].fg;
    h.style.background = GROUP_COLORS[g].bg;
    headers.appendChild(h);
  });

  for (let col = 0; col < 9; col++) {
    for (let row = 0; row < 10; row++) {
      const num = col * 10 + row + 1;
      if (num > 90) continue;
      const cell = document.createElement('div');
      cell.className    = 'num-cell';
      cell.id           = `cell-${num}`;
      cell.textContent  = num;
      cell.style.gridColumn = col + 1;
      cell.style.gridRow    = row + 1;
      grid.appendChild(cell);
    }
  }
}

// ── SYNC CON EL SERVIDOR ──────────────────────────
async function syncState() {
  try {
    const res  = await fetch('/api/state');
    const data = await res.json();
    const serverDrawn = data.drawn || [];
    gameId = data.game_id || gameId;

    // Actualizar estado del sync
    document.getElementById('sync-status').textContent = '✅ Sincronizado';

    // Solo procesar si hay cambios
    if (serverDrawn.length === drawnLocal.length) return;

    // Detectar bolillas nuevas desde la última sincronización
    const newNums = serverDrawn.filter(n => !drawnLocal.includes(n));

    drawnLocal = serverDrawn;

    // Iniciar reloj la primera vez
    if (serverDrawn.length > 0 && !gameStarted) {
      gameStarted = true;
      startClock();
      updateStatusMsg(serverDrawn.length, data.remaining ?? (90 - serverDrawn.length));
    }

    // Marcar todas las celdas (por si se recargó la página)
    drawnLocal.forEach(n => markCell(n, false));

    // Resaltar la última bolilla nueva con animación
    if (newNums.length > 0) {
      const latest = newNums[newNums.length - 1];
      markCell(latest, true);  // con animación
      updateDisplay(latest);
      updateStatusMsg(serverDrawn.length, data.remaining ?? (90 - serverDrawn.length));
    }

    updateRecent();
    updateStats(serverDrawn.length, data.remaining ?? (90 - serverDrawn.length));

    // Auto-marcado de la cartilla del jugador
    updateMyCartillaAutoMark();

    // Juego terminado
    if (data.remaining === 0 && serverDrawn.length === 90) {
      showGameOver();
    }

  } catch(e) {
    document.getElementById('sync-status').textContent = '⚠️ Sin conexión…';
  }
}

// ── DISPLAY ───────────────────────────────────────
function updateDisplay(num) {
  const g           = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg }      = GROUP_COLORS[g];
  const ball        = document.getElementById('ball');
  const bigNum      = document.getElementById('big-number');

  // Colores de la bolilla
  const ballMids  = ['#1a4a7a','#7a6010','#7a2020','#7a3810','#0f5a28','#4a1a6a','#0a5a4a','#1a3a5a','#2a3540'];
  const ballDarks = ['#0a1e2e','#2e2504','#3d0a08','#3d1800','#0a2e16','#22083d','#073832','#0a1f2e','#151d23'];

  ball.style.background = `radial-gradient(circle at 35% 32%,
    #ffffff44 0%, ${ballMids[g]}99 30%, ${ballDarks[g]} 70%, #020508 100%)`;
  ball.style.boxShadow = `
    0 0 0 3px ${fg}55, 0 0 35px ${fg}33,
    inset 0 -8px 20px rgba(0,0,0,0.7),
    inset 0 8px 16px rgba(255,255,255,0.08)`;

  ball.classList.remove('reveal');
  void ball.offsetWidth;
  ball.classList.add('reveal');
  setTimeout(() => ball.classList.remove('reveal'), 600);

  bigNum.textContent       = num;
  bigNum.style.color       = fg;
  bigNum.style.textShadow  = `0 0 20px ${fg}88, 0 2px 4px rgba(0,0,0,0.8)`;

  const gt = document.getElementById('group-tag');
  gt.textContent = `Grupo ${GROUP_LABELS[g]}`;
  gt.style.color = fg;

  // Panel lateral "última bolilla grande"
  const lb = document.getElementById('last-big');
  if (lb) { lb.textContent = num; lb.style.color = fg; }

  lastLocal = num;
}

function markCell(num, animate = false) {
  const cell = document.getElementById(`cell-${num}`);
  if (!cell) return;
  const g          = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg, bg } = GROUP_COLORS[g];
  cell.classList.add('drawn');
  if (animate) cell.classList.add('just-drawn');
  cell.style.color       = fg;
  cell.style.background  = bg;
  cell.style.borderColor = fg;
  if (animate) setTimeout(() => cell.classList.remove('just-drawn'), 600);
}

function updateRecent() {
  const strip = document.getElementById('recent-nums');
  strip.innerHTML = '';
  [...drawnLocal].slice(-18).reverse().forEach(n => {
    const g  = Math.min(Math.floor((n - 1) / 10), 8);
    const el = document.createElement('div');
    el.className   = 'recent-num';
    el.textContent = n;
    el.style.color = GROUP_COLORS[g].fg;
    strip.appendChild(el);
  });
}

function updateStats(count, remaining) {
  document.getElementById('stat-drawn').textContent = count;
  document.getElementById('stat-rem').textContent   = remaining;
  const pct = Math.round((count / 90) * 100);
  document.getElementById('progress').style.width = pct + '%';
  document.getElementById('stat-pct').textContent = pct + '%';
}

function updateStatusMsg(count, remaining) {
  const el = document.getElementById('game-status-msg');
  if (!el) return;
  if (count === 0) {
    el.textContent = 'Esperando que el administrador inicie el sorteo…';
  } else if (remaining === 0) {
    el.innerHTML = `<strong style="color:var(--accent)">🎉 ¡Juego completo!</strong><br>Se sortearon las 90 bolillas.`;
  } else {
    el.innerHTML = `
      Bolillas sorteadas: <strong style="color:var(--accent)">${count}</strong><br>
      Quedan: <strong style="color:var(--warning)">${remaining}</strong> bolillas
    `;
  }
}

// ── RELOJ ─────────────────────────────────────────
function startClock() {
  clockJob = setInterval(() => {
    elapsedSec++;
    const h  = Math.floor(elapsedSec / 3600);
    const m  = Math.floor((elapsedSec % 3600) / 60);
    const s  = elapsedSec % 60;
    const mm = String(m).padStart(2, '0');
    const ss = String(s).padStart(2, '0');
    document.getElementById('timer').textContent =
      h ? `⏱ ${h}:${mm}:${ss}` : `⏱ ${mm}:${ss}`;
  }, 1000);
}

// ── GAME OVER ─────────────────────────────────────
function showGameOver() {
  const timer = document.getElementById('timer').textContent.replace('⏱ ', '');
  document.getElementById('gameover-info').textContent =
    `¡Se sortearon las 90 bolillas en ${timer}!`;
  document.getElementById('gameover').classList.add('show');
  launchConfetti();
}

function hideGameOver() {
  document.getElementById('gameover').classList.remove('show');
}

function launchConfetti() {
  const colors = ['#00e5b4','#f6c343','#e74c3c','#2f80ed','#a569bd','#58d68d'];
  for (let i = 0; i < 80; i++) {
    const c = document.createElement('div');
    c.className = 'confetti-piece';
    c.style.left              = Math.random() * 100 + 'vw';
    c.style.background        = colors[Math.floor(Math.random() * colors.length)];
    c.style.animationDuration = (Math.random() * 2 + 2) + 's';
    c.style.animationDelay    = Math.random() * 1.5 + 's';
    c.style.width  = (Math.random() * 8 + 4) + 'px';
    c.style.height = (Math.random() * 8 + 4) + 'px';
    document.body.appendChild(c);
    setTimeout(() => c.remove(), 5000);
  }
}

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



// ── CARTILLA UI ───────────────────────────────────
function getMyCartillasFromStorage() {
  try { return JSON.parse(localStorage.getItem('my_cartillas') || '[]') || []; }
  catch(e) { return []; }
}

function populateMyCartillaSelect() {
  const sel = document.getElementById('my-cartilla-select');
  if (!sel) return;

  const arr = getMyCartillasFromStorage();
  sel.innerHTML = '';

  const opt0 = document.createElement('option');
  opt0.value = '';
  opt0.textContent = arr.length ? '— Selecciona tu cartilla —' : 'Aún no tienes cartillas en este dispositivo';
  sel.appendChild(opt0);

  arr.forEach(cid => {
    const o = document.createElement('option');
    o.value = cid;
    o.textContent = `Cartilla ${cid}`;
    sel.appendChild(o);
  });

  // Auto-select last used
  const last = localStorage.getItem('active_cartilla') || '';
  if (last && arr.includes(last)) sel.value = last;
}

function renderMyCartilla(grid, drawnSet) {
  const wrap = document.getElementById('my-cartilla-grid');
  if (!wrap) return;
  wrap.innerHTML = '';

  for (let ri = 0; ri < 3; ri++) {
    for (let ci = 0; ci < 9; ci++) {
      const num = grid[ri][ci];
      const g   = GROUP_COLORS[ci];
      const cell = document.createElement('div');
      cell.className = 'c-cell';
      if (num === null || num === undefined) {
        cell.classList.add('empty');
        cell.textContent = '·';
      } else {
        cell.classList.add('filled');
        if (drawnSet && drawnSet.has(num)) {
          cell.classList.add('marked');
          cell.style.borderColor = g.fg;
          const s = document.createElement('span');
          s.textContent = num;
          s.style.background = g.fg;
          cell.appendChild(s);
        } else {
          cell.textContent = num;
          cell.style.color = g.fg + '44';
        }
      }
      wrap.appendChild(cell);
    }
  }
}

async function loadSelectedCartilla() {
  const sel = document.getElementById('my-cartilla-select');
  const cid = (sel?.value || '').trim().toUpperCase();
  if (!cid) {
    showToast('🎴 Selecciona una cartilla');
    return;
  }

  myCartillaId = cid;
  localStorage.setItem('active_cartilla', cid);
  myBingoFired = false;

  try {
    const res = await fetch(`/api/cartilla/${cid}`);
    const data = await res.json();
    if (!res.ok) {
      showToast('❌ No se encontró esa cartilla');
      return;
    }
    myCartilla = data;

    const meta = document.getElementById('my-cartilla-meta');
    if (meta) {
      const tel = (data.telefono || '').trim();
      meta.innerHTML = `ID: <strong style="color:var(--text)">${data.id}</strong>` +
        (tel ? ` &nbsp;·&nbsp; Tel: <strong style="color:var(--accent)">${tel}</strong>` : '');
    }

    updateMyCartillaAutoMark(true);
    showToast(`✅ Cartilla ${cid} cargada`);

    // Ask notification permission (best-effort)
    try {
      if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
      }
    } catch(e) {}

  } catch(e) {
    showToast('❌ Error al cargar cartilla');
  }
}

function isMyCartillaBingo(drawnSet) {
  if (!myCartilla || !myCartilla.grid) return false;
  const nums = [];
  for (const row of myCartilla.grid) {
    for (const n of row) if (n !== null && n !== undefined) nums.push(n);
  }
  return nums.length && nums.every(n => drawnSet.has(n));
}

function playWinAlert() {
  // Short beep with WebAudio (works on desktop/mobile if user interacted at least once)
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    o.frequency.value = 880;
    o.connect(g);
    g.connect(ctx.destination);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.4, ctx.currentTime + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.2);
    o.start();
    o.stop(ctx.currentTime + 1.25);
    setTimeout(() => ctx.close(), 1400);
  } catch(e) {}

  try { if (navigator.vibrate) navigator.vibrate([200, 100, 200, 100, 400]); } catch(e) {}
}

function showWinNotification(title, body) {
  try {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      new Notification(title, { body });
    }
  } catch(e) {}
}

async function claimWinnerOnce() {
  if (!myCartillaId || !gameId) return;
  const key = `winner_claimed_${gameId}_${myCartillaId}`;
  if (localStorage.getItem(key) === '1') return;

  try {
    const res = await fetch('/api/winner/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: myCartillaId }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      localStorage.setItem(key, '1');
      return data;
    }
  } catch(e) {}
  return null;
}

function updateMyCartillaAutoMark(force = false) {
  const status = document.getElementById('my-cartilla-status');
  const btnBuy = document.getElementById('btn-go-cartillas');

  // Disable buying when game already started (but allow viewing)
  if (btnBuy) {
    btnBuy.disabled = gameStarted;
    btnBuy.textContent = gameStarted ? 'Ver/Ya compré' : 'Comprar';
  }

  if (!myCartilla || !myCartilla.grid) {
    if (status) status.textContent = gameStarted
      ? '💡 El juego ya empezó. Carga tu cartilla para marcar automáticamente.'
      : '💡 Puedes comprar/generar tu cartilla antes de que empiece el juego.';
    return;
  }

  const drawnSet = new Set(drawnLocal);
  renderMyCartilla(myCartilla.grid, drawnSet);

  const total = 15;
  let marked = 0;
  for (const row of myCartilla.grid) {
    for (const n of row) if (n !== null && n !== undefined && drawnSet.has(n)) marked++;
  }

  if (status) {
    status.innerHTML = `Marcadas: <strong style="color:var(--accent)">${marked} / ${total}</strong>`;
  }

  // Fire BINGO alert once
  if (!myBingoFired && isMyCartillaBingo(drawnSet)) {
    myBingoFired = true;
    playWinAlert();
    showToast('🎉 ¡BINGO!');
    showWinNotification('🎉 ¡BINGO!', `Cartilla ${myCartillaId} — ¡Felicidades!`);

    claimWinnerOnce().then((r) => {
      if (r?.sms_sent) showToast('📩 Te enviamos un SMS (si Twilio está configurado)');
    });
  }
}

function initMyCartillaUI() {
  populateMyCartillaSelect();

  const btn = document.getElementById('btn-load-cartilla');
  if (btn) btn.addEventListener('click', loadSelectedCartilla);

  const sel = document.getElementById('my-cartilla-select');
  if (sel) sel.addEventListener('change', () => {
    const cid = (sel.value || '').trim();
    if (cid) loadSelectedCartilla();
  });

  // Refresh list if user comes back from /cartillas
  window.addEventListener('focus', populateMyCartillaSelect);
}
// ── INIT ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('server-url').textContent = window.location.href;
  initGrid();
  initMyCartillaUI();
  syncState();
  setInterval(syncState, 3000);
});
