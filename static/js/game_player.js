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

// ── INIT ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('server-url').textContent = window.location.href;
  initGrid();
  syncState();
  setInterval(syncState, 3000);
});
