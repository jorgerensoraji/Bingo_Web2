/* ═══════════════════════════════════════════════════
   BINGO PRO — game.js  v4.0
   Made by Renso Ramirez  |  Fixed & Enhanced by Claude
═══════════════════════════════════════════════════ */

// IS_ADMIN is injected by Flask in index.html:
//   <script>const IS_ADMIN = true/false;</script>

// ── COLORES ───────────────────────────────────────
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
const BALL_COLORS  = [
  ['#1a4a7a','#0a1e2e'], ['#7a6010','#2e2504'], ['#7a2020','#3d0a08'],
  ['#7a3810','#3d1800'], ['#0f5a28','#0a2e16'], ['#4a1a6a','#22083d'],
  ['#0a5a4a','#073832'], ['#1a3a5a','#0a1f2e'], ['#2a3540','#151d23'],
];

// ── ESTADO ────────────────────────────────────────
let drawn          = [];
let lastNumber     = null;
let isDrawing      = false;
let autoRunning    = false;
let autoPaused     = false;
let autoJob        = null;
let autoCountdown  = 10;
let mixJob         = null;
let startTime      = null;
let clockJob       = null;
let currentAudio   = null;
let elapsedSeconds = 0;

// ── SEGURIDAD DE ROL ──────────────────────────────
// Llamada desde index.html DESPUÉS de verificar /api/auth/status
// Puede llamarse múltiples veces sin problema (idempotente).
function applyRoleSecurity() {
  // IS_ADMIN es una variable global definida en index.html
  // que se actualiza desde la API antes de llamar esta función.
  const admin = (typeof IS_ADMIN !== 'undefined') && IS_ADMIN === true;

  document.querySelectorAll('.admin-only').forEach(el => {
    if (admin) {
      // Restaurar controles para el admin
      el.disabled           = false;
      el.style.opacity      = '';
      el.style.pointerEvents = '';
      el.removeAttribute('aria-disabled');
      el.tabIndex = 0;
    } else {
      // Bloquear controles para jugadores/espectadores
      el.disabled           = true;
      el.style.opacity      = '0.4';
      el.style.pointerEvents = 'none';
      el.setAttribute('aria-disabled', 'true');
      el.tabIndex = -1;
    }
  });

  // Bloquear / desbloquear atajos de teclado
  // Usamos una bandera para no añadir el listener múltiples veces
  if (!window._keyListenerAttached) {
    window._keyListenerAttached = true;
    window.addEventListener('keydown', (e) => {
      // Re-leer IS_ADMIN en tiempo de ejecución (puede haber cambiado)
      const isAdminNow = (typeof IS_ADMIN !== 'undefined') && IS_ADMIN === true;
      if (!isAdminNow && [' ','r','R','a','A','n','N'].includes(e.key)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }, true);
  }
}

// ── INIT GRID ─────────────────────────────────────
function initGrid() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');
  if (!headers || !grid) return;

  headers.innerHTML = '';
  grid.innerHTML    = '';

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
      const num  = col * 10 + row + 1;
      if (num > 90) continue;   // FIX: skip 91-99 ghost cells
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

// ── SORTEAR ───────────────────────────────────────
async function drawNumber() {
  if (!IS_ADMIN) { showToast('🔒 Solo el admin puede sortear'); return; }
  if (isDrawing)  return;
  isDrawing = true;
  setDrawBtnState(false);
  await runAnimation();
}

function runAnimation() {
  return new Promise(resolve => {
    const speed = parseInt(document.getElementById('speed-slider').value);
    const delay = Math.max(14, 82 - speed * 0.68);
    const steps = 20;
    const bigNum = document.getElementById('big-number');
    const ball   = document.getElementById('ball');
    bigNum.classList.add('animating');
    ball.classList.add('animating');
    let step = 0;

    function tick() {
      if (step < steps) {
        bigNum.textContent = Math.floor(Math.random() * 90) + 1;
        step++;
        mixJob = setTimeout(tick, delay);
      } else {
        bigNum.classList.remove('animating');
        ball.classList.remove('animating');
        fetchDraw().then(resolve);
      }
    }
    tick();
  });
}

async function fetchDraw() {
  try {
    const res  = await fetch('/api/draw', { method: 'POST' });
    const data = await res.json();

    if (!res.ok) {
      showToast('❌ No autorizado / error');
      isDrawing = false;
      setDrawBtnState(true);
      return;
    }

    if (data.status === 'finished') {
      showGameOver();
      isDrawing = false;
      setDrawBtnState(true);
      return;
    }

    if (!startTime) { startTime = Date.now(); startClock(); }

    const num = data.number;
    lastNumber = num;
    drawn      = data.drawn;

    updateDisplay(num, data.words);
    markCell(num);
    updateRecent();
    updateStats(data.count, data.remaining);
    speak(data.phrase);

    isDrawing = false;
    setDrawBtnState(true);
  } catch(e) {
    console.error(e);
    showToast('❌ Error de conexión');
    isDrawing = false;
    setDrawBtnState(true);
  }
}

// ── DISPLAY ───────────────────────────────────────
function updateDisplay(num, words) {
  const g           = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg }      = GROUP_COLORS[g];
  const [mid, dark] = BALL_COLORS[g];
  const ball        = document.getElementById('ball');

  ball.style.background = `radial-gradient(circle at 35% 32%,
    #ffffff44 0%, ${mid}99 30%, ${dark} 70%, #020508 100%)`;
  ball.style.boxShadow  = `
    0 0 0 3px ${fg}55, 0 0 35px ${fg}33,
    inset 0 -8px 20px rgba(0,0,0,0.7),
    inset 0 8px 16px rgba(255,255,255,0.08)`;

  ball.classList.remove('reveal');
  void ball.offsetWidth;
  ball.classList.add('reveal');
  setTimeout(() => ball.classList.remove('reveal'), 600);

  const bigNum = document.getElementById('big-number');
  bigNum.textContent  = num;
  bigNum.style.color  = fg;
  bigNum.style.textShadow = `0 0 20px ${fg}88, 0 2px 4px rgba(0,0,0,0.8)`;

  document.getElementById('words-display').textContent = capitalize(words);
  const gt = document.getElementById('group-tag');
  gt.textContent = `Grupo ${GROUP_LABELS[g]}`;
  gt.style.color = fg;
}

function markCell(num) {
  const cell = document.getElementById(`cell-${num}`);
  if (!cell) return;
  const g          = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg, bg } = GROUP_COLORS[g];
  cell.classList.add('drawn', 'just-drawn');
  cell.style.color       = fg;
  cell.style.background  = bg;
  cell.style.borderColor = fg;
  setTimeout(() => cell.classList.remove('just-drawn'), 600);
}

function updateRecent() {
  const strip = document.getElementById('recent-nums');
  if (!strip) return;
  strip.innerHTML = '';
  [...drawn].slice(-18).reverse().forEach(n => {
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

// ── AUDIO ─────────────────────────────────────────
function getVoice() {
  return document.getElementById('voice-select').value;
}

function speak(text, onEnd) {
  stopAudio();
  const vol = parseInt(document.getElementById('vol-slider').value) / 100;
  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text, voice: getVoice() })
  })
  .then(r => r.blob())
  .then(blob => {
    const url    = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.volume = vol;
    currentAudio.play();
    currentAudio.onended = () => {
      URL.revokeObjectURL(url);
      if (onEnd) onEnd();
    };
  })
  .catch(e => { console.error('Audio error:', e); if (onEnd) onEnd(); });
}

function stopAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio     = null;
  }
}

// ── REPETIR ───────────────────────────────────────
function repeatLast() {
  if (!IS_ADMIN)    { showToast('🔒 Solo el admin puede repetir'); return; }
  if (!lastNumber)  { showToast('Todavía no se sorteó ninguna bolilla'); return; }
  if (autoRunning)  { autoPaused = true; updateAutoCd('⏸ Pausado'); }

  fetch('/api/repeat', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ voice: getVoice() })
  })
  .then(async r => {
    if (!r.ok) { autoPaused = false; showToast('❌ No autorizado / error'); return null; }
    return r.blob();
  })
  .then(blob => {
    if (!blob) return;
    stopAudio();
    const url = URL.createObjectURL(blob);
    const vol = parseInt(document.getElementById('vol-slider').value) / 100;
    currentAudio = new Audio(url);
    currentAudio.volume = vol;
    currentAudio.play();
    currentAudio.onended = () => {
      URL.revokeObjectURL(url);
      autoPaused = false;
      if (autoRunning) updateAutoCd(`🔄 Auto en ${autoCountdown}s`);
    };
  })
  .catch(() => { autoPaused = false; });
}

// ── AUTO SORTEO ───────────────────────────────────
function toggleAuto() {
  if (!IS_ADMIN) { showToast('🔒 Solo el admin puede usar Auto'); return; }
  autoRunning ? stopAuto() : startAuto();
}

function startAuto() {
  autoRunning   = true;
  autoCountdown = parseInt(document.getElementById('auto-interval').value) || 10;
  document.getElementById('btn-auto').textContent = '⏹ Detener [A]';
  document.getElementById('btn-auto').classList.add('active');

  // FIX: use correct IDs (auto-bar-wrap / auto-bar, not auto-bar-wrap2)
  const wrap = document.getElementById('auto-bar-wrap');
  if (wrap) wrap.style.display = 'block';
  tickAuto();
}

function stopAuto() {
  autoRunning = false;
  autoPaused  = false;
  clearTimeout(autoJob);
  document.getElementById('btn-auto').textContent = '⏲ Auto [A]';
  document.getElementById('btn-auto').classList.remove('active');
  document.getElementById('auto-cd').textContent = '';

  const bar  = document.getElementById('auto-bar');
  const wrap = document.getElementById('auto-bar-wrap');
  if (bar)  bar.style.width   = '0%';
  if (wrap) wrap.style.display = 'none';
}

function tickAuto() {
  if (!autoRunning) return;
  if (autoPaused)   { autoJob = setTimeout(tickAuto, 1000); return; }

  const total = parseInt(document.getElementById('auto-interval').value) || 10;
  if (autoCountdown <= 0) {
    if (drawn.length >= 90) { stopAuto(); showGameOver(); return; }
    drawNumber();
    autoCountdown = total;
  } else {
    updateAutoCd(`🔄 Auto en ${autoCountdown}s`);
    const pct = ((total - autoCountdown) / total) * 100;
    const bar = document.getElementById('auto-bar');
    if (bar) bar.style.width = pct + '%';
    autoCountdown--;
  }
  autoJob = setTimeout(tickAuto, 1000);
}

function updateAutoCd(text) {
  const el = document.getElementById('auto-cd');
  if (el) el.textContent = text;
}

// ── NUEVO JUEGO ───────────────────────────────────
async function newGame() {
  if (!IS_ADMIN) { showToast('🔒 Solo el admin puede reiniciar'); return; }
  if (!confirm('¿Iniciar un nuevo juego? Se perderá el progreso actual.')) return;

  stopAuto();
  stopAudio();

  const r = await fetch('/api/reset', { method: 'POST' });
  if (!r.ok) { showToast('❌ No autorizado / error'); return; }

  drawn          = [];
  lastNumber     = null;
  startTime      = null;
  elapsedSeconds = 0;
  clearInterval(clockJob);
  document.getElementById('timer').textContent = '⏱ 00:00';

  const ball   = document.getElementById('ball');
  const bigNum = document.getElementById('big-number');
  bigNum.textContent  = '?';
  bigNum.style.color  = 'var(--accent)';
  bigNum.style.textShadow = '';
  ball.style.background = '';
  ball.style.boxShadow  = '';
  ball.classList.remove('animating', 'reveal');

  document.getElementById('words-display').textContent = '—';
  document.getElementById('group-tag').textContent     = '';
  document.getElementById('auto-cd').textContent       = '';
  document.getElementById('recent-nums').innerHTML     = '';
  updateStats(0, 90);

  for (let i = 1; i <= 90; i++) {
    const cell = document.getElementById(`cell-${i}`);
    if (cell) {
      cell.classList.remove('drawn', 'just-drawn');
      cell.style.color       = '';
      cell.style.background  = '';
      cell.style.borderColor = '';
    }
  }

  showToast('✅ Nuevo juego iniciado');
}

// ── RELOJ ─────────────────────────────────────────
function startClock() {
  clockJob = setInterval(() => {
    elapsedSeconds++;
    const h  = Math.floor(elapsedSeconds / 3600);
    const m  = Math.floor((elapsedSeconds % 3600) / 60);
    const s  = elapsedSeconds % 60;
    const mm = String(m).padStart(2, '0');
    const ss = String(s).padStart(2, '0');
    document.getElementById('timer').textContent =
      h ? `⏱ ${h}:${mm}:${ss}` : `⏱ ${mm}:${ss}`;
  }, 1000);
}

// ── GAME OVER ─────────────────────────────────────
function showGameOver() {
  stopAuto();
  speak('¡Felicidades! Se han sorteado todas las bolillas. ¡Juego completo!');
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
    c.style.width             = (Math.random() * 8 + 4) + 'px';
    c.style.height            = (Math.random() * 8 + 4) + 'px';
    document.body.appendChild(c);
    setTimeout(() => c.remove(), 5000);
  }
}

// ── TECLADO ───────────────────────────────────────
document.addEventListener('keydown', (e) => {
  if (!IS_ADMIN) return;
  // Don't fire if typing in an input
  if (['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)) return;

  switch(e.key) {
    case ' ':         e.preventDefault(); drawNumber();  break;
    case 'r': case 'R': repeatLast();                    break;
    case 'a': case 'A': toggleAuto();                    break;
    case 'Escape':      stopAudio();                     break;
    case 'n': case 'N': newGame();                       break;
  }
});

// ── HELPERS ───────────────────────────────────────
function setDrawBtnState(enabled) {
  const b = document.getElementById('btn-draw');
  if (b) b.disabled = !enabled;
}

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
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


// ── CARGAR ESTADO PREVIO AL ENTRAR ────────────────
// Si el admin entra mientras ya se sortearon bolillas,
// carga todo el historial para mostrar el tablero actualizado.
async function loadExistingState() {
  try {
    const res  = await fetch('/api/state');
    const data = await res.json();
    const serverDrawn = data.drawn || [];
    if (serverDrawn.length === 0) return;

    drawn      = serverDrawn;
    lastNumber = data.last;

    drawn.forEach(n => markCell(n));

    if (data.last) {
      updateDisplay(data.last, '');
      document.getElementById('words-display').textContent = 'Último sorteado: ' + data.last;
    }

    updateRecent();
    updateStats(drawn.length, data.remaining ?? (90 - drawn.length));

    if (!startTime) { startTime = Date.now(); startClock(); }

    showToast('✅ Juego en curso: ' + drawn.length + ' bolillas ya sorteadas');
  } catch(e) {
    console.error('Error al cargar estado previo:', e);
  }
}

let playerPollJob = null;
let lastSeenServerLast = null;

async function pollStateForPlayers() {
  if (IS_ADMIN) return; // solo jugadores

  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    const data = await res.json();

    const serverDrawn = data.drawn || [];
    const serverLast  = data.last || null;

    // primera vez: solo sincroniza (sin hablar)
    if (lastSeenServerLast === null) {
      drawn = serverDrawn;
      lastNumber = serverLast;
      serverDrawn.forEach(n => markCell(n));
      if (serverLast) updateDisplay(serverLast, '');
      updateRecent();
      updateStats(serverDrawn.length, data.remaining ?? (90 - serverDrawn.length));
      lastSeenServerLast = serverLast;
      return;
    }

    // si salió un nuevo número
    if (serverLast && serverLast !== lastSeenServerLast) {
      drawn = serverDrawn;
      lastNumber = serverLast;
      lastSeenServerLast = serverLast;

      // UI
      updateDisplay(serverLast, '');
      markCell(serverLast);
      updateRecent();
      updateStats(serverDrawn.length, data.remaining ?? (90 - serverDrawn.length));

      // AUDIO: requiere unlock por click/tap
      speak(`Bolilla ${serverLast}`);
    }
  } catch (e) {
    // silencioso para no molestar
    // console.error(e);
  }
}

function startPlayerPolling() {
  if (IS_ADMIN) return;
  if (playerPollJob) clearInterval(playerPollJob);
  playerPollJob = setInterval(pollStateForPlayers, 1000);
}

// ── INIT ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const urlEl = document.getElementById('server-url');
  if (urlEl) urlEl.textContent = window.location.href;
  initGrid();
  applyRoleSecurity();
  // Cargar bolillas ya sorteadas antes de que el admin empiece a operar
  loadExistingState();
});
