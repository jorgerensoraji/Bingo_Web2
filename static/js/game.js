/* ═══════════════════════════════════════════════════
   BINGO PRO — game.js
   Lógica del juego principal
   Made by Renso Ramirez
═══════════════════════════════════════════════════ */

// ── COLORES DE GRUPOS ──────────────────────────────
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
const GROUP_LABELS = ['1–10','11–20','21–30','31–40','41–50','51–60','61–70','71–80','81–90'];

const BALL_COLORS = [
  ['#1a4a7a','#0a1e2e'],
  ['#7a6010','#2e2504'],
  ['#7a2020','#3d0a08'],
  ['#7a3810','#3d1800'],
  ['#0f5a28','#0a2e16'],
  ['#4a1a6a','#22083d'],
  ['#0a5a4a','#073832'],
  ['#1a3a5a','#0a1f2e'],
  ['#2a3540','#151d23'],
];

// ── ESTADO ────────────────────────────────────────

// ── ROL (admin / jugador) ─────────────────────────────
let IS_ADMIN = false;
async function detectRole(){
  try{
    const me = await (await fetch('/api/me')).json();
    IS_ADMIN = !!me.is_admin;
    if(!IS_ADMIN){
      ['btn-draw','btn-repeat','btn-new','btn-auto'].forEach(id=>{
        const el=document.getElementById(id);
        if(el) el.disabled = true;
      });
    }
  }catch(e){}
}

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

// ── INIT GRID ─────────────────────────────────────
function initGrid() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');
  headers.innerHTML = '';
  grid.innerHTML    = '';

  GROUP_LABELS.forEach((lbl, g) => {
    const h = document.createElement('div');
    h.className    = 'group-header';
    h.textContent  = lbl;
    h.style.color      = GROUP_COLORS[g].fg;
    h.style.background = GROUP_COLORS[g].bg;
    headers.appendChild(h);
  });

  for (let col = 0; col < 9; col++) {
    for (let row = 0; row < 10; row++) {
      const num  = col * 10 + row + 1;
      const cell = document.createElement('div');
      cell.className      = 'num-cell';
      cell.id             = `cell-${num}`;
      cell.textContent    = num;
      cell.style.gridColumn = col + 1;
      cell.style.gridRow    = row + 1;
      grid.appendChild(cell);
    }
  }
}

// ── DRAW ──────────────────────────────────────────
async function drawNumber() {
  if(!IS_ADMIN){ showToast('🔒 Solo el admin puede sortear'); return; }

  if (isDrawing) return;
  isDrawing = true;
  setDrawBtnState(false);
  await runAnimation();
}

function runAnimation() {
  return new Promise(resolve => {
    const speed  = parseInt(document.getElementById('speed-slider').value);
    const delay  = Math.max(14, 82 - speed * 0.68);
    const steps  = 20;
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
    addHistory(data.count, num, data.words);
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
  const g          = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg }     = GROUP_COLORS[g];
  const ball       = document.getElementById('ball');
  const [mid, dark] = BALL_COLORS[g];

  ball.style.background = `radial-gradient(circle at 35% 32%,
    #ffffff44 0%, ${mid}99 30%, ${dark} 70%, #020508 100%)`;
  ball.style.boxShadow = `
    0 0 0 3px ${fg}55,
    0 0 35px ${fg}33,
    inset 0 -8px 20px rgba(0,0,0,0.7),
    inset 0 8px 16px rgba(255,255,255,0.08)`;

  ball.classList.remove('reveal');
  void ball.offsetWidth;
  ball.classList.add('reveal');
  setTimeout(() => ball.classList.remove('reveal'), 600);

  document.getElementById('big-number').textContent     = num;
  document.getElementById('big-number').style.color     = fg;
  document.getElementById('big-number').style.textShadow = `0 0 20px ${fg}88, 0 2px 4px rgba(0,0,0,0.8)`;
  document.getElementById('words-display').textContent  = capitalize(words);
  document.getElementById('group-tag').textContent      = `Grupo ${GROUP_LABELS[g]}`;
  document.getElementById('group-tag').style.color      = fg;
}

function markCell(num) {
  const cell = document.getElementById(`cell-${num}`);
  if (!cell) return;
  const g         = Math.min(Math.floor((num - 1) / 10), 8);
  const { fg, bg } = GROUP_COLORS[g];
  cell.classList.add('drawn', 'just-drawn');
  cell.style.color       = fg;
  cell.style.background  = bg;
  cell.style.borderColor = fg;
  setTimeout(() => cell.classList.remove('just-drawn'), 600);
}

function updateRecent() {
  const strip = document.getElementById('recent-nums');
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
  document.getElementById('progress').style.width   = pct + '%';
  document.getElementById('stat-pct').textContent   = pct + '%';
}

function addHistory(idx, num, words) {
  const list = document.getElementById('hist-list');
  const g    = Math.min(Math.floor((num - 1) / 10), 8);
  const now  = new Date().toLocaleTimeString('es', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
  const div  = document.createElement('div');
  div.className   = 'hist-entry';
  div.style.color = GROUP_COLORS[g].fg;
  div.textContent = `#${String(idx).padStart(2,'0')}  ${String(num).padStart(2,'0')}  —  ${words}  (${now})`;
  list.insertBefore(div, list.firstChild);
}

// ── AUDIO ─────────────────────────────────────────
function getVoice() {
  return document.getElementById('voice-select').value;
}

function speak(text, onEnd) {
  stopAudio();
  const vol = parseInt(document.getElementById('vol-slider').value) / 100;
  fetch('/api/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice: getVoice() })
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
  .catch(e => {
    console.error('Audio error:', e);
    if (onEnd) onEnd();
  });
}

function stopAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio = null;
  }
}

// ── REPETIR ───────────────────────────────────────
function repeatLast() {
  if (!lastNumber) { showToast('Todavía no se sorteó ninguna bolilla'); return; }
  if (autoRunning) { autoPaused = true; updateAutoCd('⏸ Pausado'); }

  fetch('/api/repeat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice: getVoice() })
  })
  .then(r => r.blob())
  .then(blob => {
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
  autoRunning ? stopAuto() : startAuto();
}

function startAuto() {
  autoRunning   = true;
  autoCountdown = parseInt(document.getElementById('auto-interval').value) || 10;
  document.getElementById('btn-auto').textContent = '⏹ Detener [A]';
  document.getElementById('btn-auto').classList.add('active');
  document.getElementById('auto-bar-wrap2').style.display = 'block';
  tickAuto();
}

function stopAuto() {
  autoRunning = false;
  autoPaused  = false;
  clearTimeout(autoJob);
  document.getElementById('btn-auto').textContent = '⏲ Auto [A]';
  document.getElementById('btn-auto').classList.remove('active');
  document.getElementById('auto-cd').textContent   = '';
  document.getElementById('auto-bar2').style.width  = '0%';
  document.getElementById('auto-bar-wrap2').style.display = 'none';
}

function tickAuto() {
  if (!autoRunning) return;
  if (autoPaused) { autoJob = setTimeout(tickAuto, 1000); return; }

  const total = parseInt(document.getElementById('auto-interval').value) || 10;
  if (autoCountdown <= 0) {
    if (drawn.length >= 90) { stopAuto(); showGameOver(); return; }
    drawNumber();
    autoCountdown = total;
  } else {
    updateAutoCd(`🔄 Auto en ${autoCountdown}s`);
    const pct = ((total - autoCountdown) / total) * 100;
    document.getElementById('auto-bar2').style.width = pct + '%';
    autoCountdown--;
  }
  autoJob = setTimeout(tickAuto, 1000);
}

function updateAutoCd(text) {
  document.getElementById('auto-cd').textContent = text;
}

// ── NUEVO JUEGO ───────────────────────────────────
async function newGame() {
  if(!IS_ADMIN){ showToast('🔒 Solo el admin puede reiniciar'); return; }

  if (!confirm('¿Iniciar un nuevo juego? Se perderá el progreso actual.')) return;
  stopAuto();
  stopAudio();
  await fetch('/api/reset', { method: 'POST' });

  drawn          = [];
  lastNumber     = null;
  startTime      = null;
  elapsedSeconds = 0;
  clearInterval(clockJob);
  document.getElementById('timer').textContent = '⏱ 00:00';

  const ball = document.getElementById('ball');
  document.getElementById('big-number').textContent      = '?';
  document.getElementById('big-number').style.color      = 'var(--accent)';
  document.getElementById('big-number').style.textShadow = '';
  ball.style.background = '';
  ball.style.boxShadow  = '';
  ball.classList.remove('animating', 'reveal');
  document.getElementById('words-display').textContent   = '—';
  document.getElementById('group-tag').textContent       = '';
  document.getElementById('auto-cd').textContent         = '';
  document.getElementById('recent-nums').innerHTML       = '';
  document.getElementById('hist-list').innerHTML         = '';
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
}

// ── RELOJ ─────────────────────────────────────────
function startClock() {
  clockJob = setInterval(() => {
    elapsedSeconds++;
    const m  = Math.floor(elapsedSeconds / 60);
    const s  = elapsedSeconds % 60;
    const h  = Math.floor(m / 60);
    const mm = h ? String(m % 60).padStart(2,'0') : String(m).padStart(2,'0');
    const ss = String(s).padStart(2,'0');
    document.getElementById('timer').textContent =
      h ? `⏱ ${h}:${mm}:${ss}` : `⏱ ${mm}:${ss}`;
  }, 1000);
}

// ── HISTORIAL ─────────────────────────────────────
function copyHistory() {
  if (!drawn.length) { showToast('No hay bolillas sorteadas'); return; }
  navigator.clipboard.writeText(drawn.join(', ')).then(() => showToast('✅ Historial copiado'));
}

function exportHistory() {
  if (!drawn.length) { showToast('No hay bolillas sorteadas'); return; }
  const lines = [
    '══════════════════════════════════════════',
    '  BINGO PRO — Registro de Partida',
    `  Fecha: ${new Date().toLocaleString('es')}`,
    `  Sorteadas: ${drawn.length} / 90`,
    '══════════════════════════════════════════',
    '',
    ...drawn.map((n, i) => `  ${String(i+1).padStart(2,'0')}.  ${String(n).padStart(2,'0')}`)
  ].join('\n');
  const a = document.createElement('a');
  a.href     = 'data:text/plain;charset=utf-8,' + encodeURIComponent(lines);
  a.download = `bingo_${new Date().toISOString().slice(0,10)}.txt`;
  a.click();
}

function clearHistory() {
  if (!drawn.length) return;
  if (confirm('¿Limpiar el historial visual?')) {
    document.getElementById('hist-list').innerHTML = '';
  }
}

// ── GAME OVER ─────────────────────────────────────
function showGameOver() {
  stopAuto();
  speak('¡Felicidades! Se han sorteado todas las bolillas. ¡Juego completo!');
  document.getElementById('gameover-info').textContent =
    `¡Se sortearon las 90 bolillas en ${document.getElementById('timer').textContent.replace('⏱ ', '')}!`;
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

// ── HELPERS ───────────────────────────────────────
function setDrawBtnState(enabled) {
  document.getElementById('btn-draw').disabled = !enabled;
}
function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

let toastJob = null;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastJob);
  toastJob = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── TECLADO ───────────────────────────────────────
document.addEventListener('keydown', e => {
  const tag = document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'SELECT') return;
  if (e.code === 'Space')                    { e.preventDefault(); drawNumber(); }
  if (e.key  === 'r' || e.key === 'R')       repeatLast();
  if (e.key  === 'a' || e.key === 'A')       toggleAuto();
  if (e.key  === 'n' || e.key === 'N')       newGame();
  if (e.key  === 'Escape')                   stopAudio();
});

// ── INIT ──────────────────────────────────────────
document.getElementById('server-url').textContent = window.location.href;
initGrid();
detectRole();