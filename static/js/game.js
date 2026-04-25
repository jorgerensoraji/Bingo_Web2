/* ═══════════════════════════════════════════════════
   BINGO PRO — game.js  v4.0
   Made by Renso Ramirez  |  Fixed & Enhanced by Claude
═══════════════════════════════════════════════════ */

// IS_ADMIN is injected by Flask in index.html:
//   <script>const IS_ADMIN = true/false;</script>

// ── SOUND ENGINE ─────────────────────────────────
const _ac = new (window.AudioContext || window.webkitAudioContext)();

// Unlock AudioContext on first touch (required by mobile browsers)
(function() {
  function _unlock() {
    if (_ac.state === 'suspended') _ac.resume();
    document.removeEventListener('touchstart', _unlock);
    document.removeEventListener('click', _unlock);
  }
  document.addEventListener('touchstart', _unlock, { passive: true });
  document.addEventListener('click', _unlock, { passive: true });
})();
let _bgInterval  = null;
let _bgRunning   = false;
let _sfxEnabled  = true;
let _bgChordIdx  = 0;

function _resumeAC() { if (_ac.state === 'suspended') _ac.resume(); }

function _tone(freq, start, dur, type = 'sine', vol = 0.3) {
  const osc = _ac.createOscillator();
  const g   = _ac.createGain();
  osc.connect(g); g.connect(_ac.destination);
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  g.gain.setValueAtTime(vol, start);
  g.gain.exponentialRampToValueAtTime(0.001, start + dur);
  osc.start(start); osc.stop(start + dur + 0.05);
}

function soundBallDraw() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  _tone(900, t,        0.04, 'sine',     0.12);
  _tone(500, t + 0.03, 0.07, 'sine',     0.07);
}

function soundLinea() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  [523, 659, 784, 1047].forEach((f, i) => _tone(f, t + i * 0.13, 0.28, 'sine', 0.28));
}

function soundDoubleLinea() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  [523, 659, 784, 988, 1175].forEach((f, i) => _tone(f, t + i * 0.12, 0.3, 'sine', 0.28));
  [523, 659, 784].forEach(f => _tone(f, t + 0.7, 0.5, 'triangle', 0.18));
}

function soundUO() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  [392, 494, 587, 740, 880, 1109].forEach((f, i) => _tone(f, t + i * 0.11, 0.28, 'sine', 0.27));
}

function soundBingo() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  // Ascending fanfare
  [523, 659, 784, 1047, 1319].forEach((f, i) => _tone(f, t + i * 0.11, 0.22, 'sine', 0.32));
  // Second wave
  [784, 988, 1175, 1568].forEach((f, i) => _tone(f, t + 0.65 + i * 0.1, 0.25, 'triangle', 0.22));
  // Final big chord
  [523, 659, 784, 1047].forEach(f => _tone(f, t + 1.15, 1.2, 'sine', 0.22));
  [1047, 1319, 1568].forEach(f  => _tone(f, t + 1.2,  0.9, 'triangle', 0.12));
}

function soundGameOver() {
  if (!_sfxEnabled) return; _resumeAC();
  const t = _ac.currentTime;
  [784, 740, 659, 587, 523].forEach((f, i) => _tone(f, t + i * 0.18, 0.35, 'sine', 0.25));
  [523, 392, 330].forEach((f, i) => _tone(f, t + 1.0 + i * 0.2, 0.5, 'sine', 0.2));
}

// Background music — festive chord loop (C → Am → F → G)
const _BG_CHORDS = [
  [261, 329, 392, 523],  // C maj
  [220, 277, 329, 440],  // Am
  [174, 220, 261, 349],  // F maj
  [196, 247, 294, 392],  // G maj
];

function _playBgChord() {
  if (!_bgRunning) return;
  _resumeAC();
  const chord = _BG_CHORDS[_bgChordIdx % _BG_CHORDS.length];
  chord.forEach((f, i) => {
    const osc = _ac.createOscillator();
    const g   = _ac.createGain();
    osc.connect(g); g.connect(_ac.destination);
    osc.type = 'sine';
    osc.frequency.value = f;
    g.gain.setValueAtTime(0.001, _ac.currentTime);
    g.gain.linearRampToValueAtTime(0.04, _ac.currentTime + 0.3);
    g.gain.exponentialRampToValueAtTime(0.001, _ac.currentTime + 1.7);
    osc.start(_ac.currentTime); osc.stop(_ac.currentTime + 2);
  });
  _bgChordIdx++;
}

function startBgMusic() {
  if (_bgRunning) return;
  _bgRunning = true; _bgChordIdx = 0;
  _playBgChord();
  _bgInterval = setInterval(_playBgChord, 1800);
  const btn = document.getElementById('btn-music');
  if (btn) { btn.textContent = '🎵 Música ON'; btn.style.color = 'var(--accent)'; btn.style.borderColor = 'var(--accent)'; }
}

function stopBgMusic() {
  _bgRunning = false;
  if (_bgInterval) { clearInterval(_bgInterval); _bgInterval = null; }
  const btn = document.getElementById('btn-music');
  if (btn) { btn.textContent = '🎵 Música OFF'; btn.style.color = 'var(--muted)'; btn.style.borderColor = 'var(--border)'; }
}

function toggleBgMusic() {
  _resumeAC();
  if (_bgRunning) stopBgMusic(); else startBgMusic();
}

function toggleSfx() {
  _sfxEnabled = !_sfxEnabled;
  const btn = document.getElementById('btn-sfx');
  if (btn) {
    btn.textContent  = _sfxEnabled ? '🔔 Sonidos ON' : '🔕 Sonidos OFF';
    btn.style.color  = _sfxEnabled ? 'var(--accent)' : 'var(--muted)';
    btn.style.borderColor = _sfxEnabled ? 'var(--accent)' : 'var(--border)';
  }
}

function _prizeSoundForBanner(winners, lineaWinners, uWinners, oWinners) {
  if (winners && winners.length > 0)           soundBingo();
  else if (uWinners && uWinners.length > 0)    soundUO();
  else if (oWinners && oWinners.length > 0)    soundUO();
  else if (lineaWinners && lineaWinners.length > 0) soundLinea();
}

// ── COLORES ───────────────────────────────────────
const GROUP_COLORS = [
  { fg:'#5dade2', bg:'#0a1e2e' },  // B  1-15
  { fg:'#f4d03f', bg:'#1e1500' },  // I  16-30
  { fg:'#f1948a', bg:'#2a0805' },  // N  31-45
  { fg:'#58d68d', bg:'#051e0f' },  // G  46-60
  { fg:'#a569bd', bg:'#160525' },  // O  61-75
];
const GROUP_LABELS = ['B  1-15','I  16-30','N  31-45','G  46-60','O  61-75'];
const BALL_COLORS  = [
  ['#1a4a7a','#0a1e2e'], ['#7a6010','#2e2504'], ['#7a2020','#3d0a08'],
  ['#0f5a28','#0a2e16'], ['#4a1a6a','#22083d'],
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

  for (let col = 0; col < 5; col++) {
    for (let row = 0; row < 15; row++) {
      const num  = col * 15 + row + 1;
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
        bigNum.textContent = Math.floor(Math.random() * 75) + 1;
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
    const voice = IS_ADMIN ? getVoice() : 'es-PE-CamilaNeural';
    const res  = await fetch('/api/draw', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ voice }),
    });
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

    if (data.status === 'paused') {
      isDrawing = false;
      setDrawBtnState(true);
      showPausedBanner(data.winners || [], data.linea_winners || [], data.u_winners || [], data.o_winners || []);
      return;
    }

    if (!startTime) { startTime = Date.now(); startClock(); }

    const num = data.number;
    lastNumber = num;
    drawn      = data.drawn;

    updateDisplay(num, data.words);
    markCell(num);
    soundBallDraw();
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
  const g           = Math.min(Math.floor((num - 1) / 15), 4);
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
  gt.textContent = GROUP_LABELS[g];
  gt.style.color = fg;
}

function markCell(num) {
  const cell = document.getElementById(`cell-${num}`);
  if (!cell) return;
  const g          = Math.min(Math.floor((num - 1) / 15), 4);
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
    const g  = Math.min(Math.floor((n - 1) / 15), 4);
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
  const pct = Math.round((count / 75) * 100);
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
  // Auto-enable sounds when autoplay starts
  _resumeAC();
  if (!_bgRunning) startBgMusic();

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
    if (drawn.length >= 75) { stopAuto(); showGameOver(); return; }
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
  updateStats(0, 75);

  for (let i = 1; i <= 75; i++) {
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
  soundGameOver();
  speak('¡Felicidades! Se han sorteado todas las bolillas. ¡Juego completo!');
  const timer = document.getElementById('timer').textContent.replace('⏱ ', '');
  document.getElementById('gameover-info').textContent =
    `¡Se sortearon las 75 bolillas en ${timer}!`;
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
    if (serverDrawn.length === 0 || data.session_finished) return;

    drawn      = serverDrawn;
    lastNumber = data.last;

    drawn.forEach(n => markCell(n));

    if (data.last) {
      updateDisplay(data.last, '');
      document.getElementById('words-display').textContent = 'Último sorteado: ' + data.last;
    }

    updateRecent();
    updateStats(drawn.length, data.remaining ?? (75 - drawn.length));

    if (!startTime) { startTime = Date.now(); startClock(); }

    if (data.paused) {
      showPausedBanner(data.winners || [], data.linea_winners || [], data.u_winners || [], data.o_winners || []);
      showToast('⏸ Juego pausado — hay ganador(es)');
    } else {
      showToast('✅ Juego en curso: ' + drawn.length + ' bolillas ya sorteadas');
    }
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
      updateStats(serverDrawn.length, data.remaining ?? (75 - serverDrawn.length));
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
      updateStats(serverDrawn.length, data.remaining ?? (75 - serverDrawn.length));

      // AUDIO: requiere unlock por click/tap
      speak(data.last_phrase || `Bolilla ${serverLast}`);
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

// ── PAUSA POR GANADOR ────────────────────────────
function showPausedBanner(winners, lineaWinners, uWinners, oWinners) {
  stopAuto();
  _prizeSoundForBanner(winners, lineaWinners, uWinners, oWinners);

  const existing = document.getElementById('winner-zone');
  if (!existing) return;
  existing.style.cssText = `
    display:flex;align-items:center;justify-content:center;flex-direction:column;
    text-align:center;padding:28px;
    background:rgba(7,13,20,.92);border-bottom:1px solid rgba(0,229,180,.2);
  `;

  function contactBlock(w, accentColor) {
    var lines = [];
    if (w.yape_plin) lines.push(
      '<div style="margin-top:8px;background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.25);' +
      'border-radius:8px;padding:8px 14px;">' +
      '<span style="font-size:.65rem;color:var(--muted);display:block;letter-spacing:1px;text-transform:uppercase;margin-bottom:2px">📲 Enviar premio a Yape/Plin</span>' +
      '<span style="font-size:1.25rem;font-weight:900;color:' + accentColor + ';letter-spacing:2px">' + escHtml(w.yape_plin) + '</span>' +
      '</div>');
    else lines.push('<div style="font-size:.75rem;color:var(--muted);margin-top:4px">⚠️ Sin Yape/Plin — ver en /admin/payments</div>');
    var extra = [];
    if (w.email)   extra.push('✉️ ' + escHtml(w.email));
    if (w.celular) extra.push('📞 ' + escHtml(w.celular));
    if (extra.length) lines.push(
      '<div style="margin-top:6px;font-size:.78rem;color:var(--muted);line-height:1.8">' + extra.join('  ·  ') + '</div>');
    return lines.join('');
  }

  // Build merge note once (from first winner)
  var mergeNote = '';
  if (winners && winners.length > 0) {
    var fw = winners[0];
    var mo = Number(fw.merged_o || 0), mu = Number(fw.merged_u || 0);
    if (mo > 0 || mu > 0) {
      var base = Number(fw.prize || 0) - mo - mu;
      var rows = '<tr><td style="padding:3px 6px;color:rgba(255,255,255,.5)">BINGO base</td><td style="padding:3px 6px;color:var(--text);text-align:right">S/. ' + base.toFixed(2) + '</td></tr>';
      if (mu > 0) rows += '<tr><td style="padding:3px 6px;color:#3b82f6">+ U (nadie ganó)</td><td style="padding:3px 6px;color:#3b82f6;text-align:right">S/. ' + mu.toFixed(2) + '</td></tr>';
      if (mo > 0) rows += '<tr><td style="padding:3px 6px;color:#ec4899">+ O (nadie ganó)</td><td style="padding:3px 6px;color:#ec4899;text-align:right">S/. ' + mo.toFixed(2) + '</td></tr>';
      mergeNote = '<div style="background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.25);border-radius:8px;padding:10px;margin-bottom:12px;font-size:.78rem">' +
        '<div style="color:var(--accent);font-weight:700;margin-bottom:6px">🎁 Premios acumulados al BINGO</div>' +
        '<table style="width:100%;border-collapse:collapse">' + rows +
        '<tr style="border-top:1px solid rgba(255,255,255,.1)"><td style="padding:4px 6px;color:var(--text);font-weight:700">Total por ganador</td>' +
        '<td style="padding:4px 6px;color:var(--accent);font-weight:900;text-align:right">S/. ' + Number(fw.prize || 0).toFixed(2) + '</td></tr>' +
        '</table></div>';
    }
  }

  const bingoCards = (winners || []).map(function(w) {
    const prize = w.prize ? '<span style="color:var(--accent);font-weight:900"> · S/. ' + Number(w.prize).toFixed(2) + '</span>' : '';
    const nombre = escHtml((w.nombre || '') + (w.apellidos ? ' ' + w.apellidos : '') || w.id);
    return '<div style="margin:8px 0;padding:14px;background:rgba(255,255,255,.04);border-radius:10px;text-align:left">' +
      '<div style="font-size:1.05rem;color:var(--text);font-weight:700">🏆 ' + nombre + prize + '</div>' +
      '<div style="font-size:.78rem;color:var(--muted);margin-top:2px">Cartilla: <strong>' + w.id + '</strong>' +
        (w.drawn_count ? '  ·  Bolilla #' + w.drawn_count : '') + '</div>' +
      contactBlock(w, 'var(--accent)') +
      '</div>';
  }).join('');

  const lineaCards = (lineaWinners || []).map(function(w) {
    const prize = w.linea_prize ? '<span style="color:var(--warning);font-weight:900"> · S/. ' + Number(w.linea_prize).toFixed(2) + '</span>' : '';
    const nombre = escHtml((w.nombre || '') + (w.apellidos ? ' ' + w.apellidos : '') || w.id);
    return '<div style="margin:6px 0;padding:12px;background:rgba(246,195,67,.06);border:1px solid rgba(246,195,67,.2);border-radius:10px;text-align:left">' +
      '<div style="font-size:.95rem;color:var(--text);font-weight:700">⭐ ' + nombre + prize + '</div>' +
      '<div style="font-size:.75rem;color:var(--muted);margin-top:2px">Letra I · Cartilla: <strong>' + w.id + '</strong>' +
        (w.drawn_count ? '  ·  Bolilla #' + w.drawn_count : '') + '</div>' +
      contactBlock(w, 'var(--warning)') +
      '</div>';
  }).join('');

  const uCards = (uWinners || []).map(function(w) {
    const prize = w.u_prize ? '<span style="color:#3b82f6;font-weight:900"> · S/. ' + Number(w.u_prize).toFixed(2) + '</span>' : '';
    const nombre = escHtml((w.nombre || '') + (w.apellidos ? ' ' + w.apellidos : '') || w.id);
    return '<div style="margin:6px 0;padding:12px;background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.2);border-radius:10px;text-align:left">' +
      '<div style="font-size:.95rem;color:var(--text);font-weight:700">🔷 ' + nombre + prize + '</div>' +
      '<div style="font-size:.75rem;color:var(--muted);margin-top:2px">Formar Letra U · Cartilla: <strong>' + w.id + '</strong>' +
        (w.drawn_count ? '  ·  Bolilla #' + w.drawn_count : '') + '</div>' +
      contactBlock(w, '#3b82f6') +
      '</div>';
  }).join('');

  const oCards = (oWinners || []).map(function(w) {
    const prize = w.o_prize ? '<span style="color:#ec4899;font-weight:900"> · S/. ' + Number(w.o_prize).toFixed(2) + '</span>' : '';
    const nombre = escHtml((w.nombre || '') + (w.apellidos ? ' ' + w.apellidos : '') || w.id);
    return '<div style="margin:6px 0;padding:12px;background:rgba(236,72,153,.06);border:1px solid rgba(236,72,153,.2);border-radius:10px;text-align:left">' +
      '<div style="font-size:.95rem;color:var(--text);font-weight:700">⭕ ' + nombre + prize + '</div>' +
      '<div style="font-size:.75rem;color:var(--muted);margin-top:2px">Formar Letra O · Cartilla: <strong>' + w.id + '</strong>' +
        (w.drawn_count ? '  ·  Bolilla #' + w.drawn_count : '') + '</div>' +
      contactBlock(w, '#ec4899') +
      '</div>';
  }).join('');

  existing.innerHTML = `
    <div style="max-width:500px;overflow-y:auto;max-height:90vh;">
      <div style="font-size:4rem;margin-bottom:8px;">🎉</div>
      <h1 style="font-family:'Bebas Neue',sans-serif;font-size:3rem;color:var(--accent);
                 letter-spacing:4px;text-shadow:0 0 30px rgba(0,229,180,.5);margin-bottom:12px;">
        ¡GANADOR!
      </h1>
      ${mergeNote}
      <div style="margin-bottom:12px;">${bingoCards || '<div style="color:var(--muted);">Verificando ganador…</div>'}</div>
      ${oCards ? `<div style="margin-bottom:12px;border-top:1px solid rgba(236,72,153,.2);padding-top:10px;">
        <div style="font-size:.72rem;color:#ec4899;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">⭕ Premio — Formar Letra O</div>
        ${oCards}
      </div>` : ''}
      ${uCards ? `<div style="margin-bottom:12px;border-top:1px solid rgba(59,130,246,.2);padding-top:10px;">
        <div style="font-size:.72rem;color:#3b82f6;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">🔷 Premio — Formar Letra U</div>
        ${uCards}
      </div>` : ''}
      ${lineaCards ? `<div style="margin-bottom:12px;border-top:1px solid rgba(246,195,67,.2);padding-top:10px;">
        <div style="font-size:.72rem;color:var(--warning);letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">⭐ Formar Letra I</div>
        ${lineaCards}
      </div>` : ''}
      <div style="color:var(--muted);font-size:.9rem;margin-bottom:20px;">
        El juego está <strong style="color:var(--warning);">pausado</strong>.<br>
        Como admin puedes continuar o iniciar un nuevo juego.
      </div>
      <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
        <button onclick="resumeGame()" style="
          padding:12px 24px;border:none;border-radius:10px;
          background:var(--accent);color:#041015;
          font-family:'Outfit',sans-serif;font-weight:900;font-size:1rem;cursor:pointer;">
          ▶ Continuar sorteo
        </button>
        <button onclick="newGame(); hidePausedBanner();" style="
          padding:12px 24px;border:1px solid var(--border);border-radius:10px;
          background:var(--card);color:var(--text);
          font-family:'Outfit',sans-serif;font-weight:700;font-size:1rem;cursor:pointer;">
          🔄 Nuevo juego
        </button>
      </div>
    </div>
  `;

  launchConfetti();
}

function hidePausedBanner() {
  const b = document.getElementById('winner-zone');
  if (b) { b.style.display = 'none'; b.innerHTML = ''; }
}

async function resumeGame() {
  const res = await fetch('/api/admin/resume', { method: 'POST' });
  if (res.ok) {
    hidePausedBanner();
    showToast('▶ Juego reanudado');
  } else {
    showToast('❌ Error al reanudar');
  }
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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