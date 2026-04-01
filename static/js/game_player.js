/* ═══════════════════════════════════════════════════════
   BINGO PRO — game_player.js  v7.2
   Made by Renso Ramirez  |  Enhanced v7.2

   Base: v5.0 original (todas las funciones preservadas)
   v6.0 agrega:
   ✅ Botón ¡RECLAMAR BINGO! aparece automáticamente
   ✅ Botón ¡RECLAMAR LÍNEA! con monto del premio
   ✅ Alerta "¡Falta 1!" pulsante + voz
   ✅ Pozo de premios visible en tiempo real
   ✅ Ganadores en live en el panel de estado
   v7.1 agrega:
   ✅ Hook countdown banner (_v71_cdHook)
   v7.2 agrega:
   ✅ Pozo se divide entre todos los ganadores (empate)
   ✅ Jugador puede ganar LÍNEA y BINGO en el mismo juego
   ✅ Mensaje de empate en overlay y toast
═══════════════════════════════════════════════════════ */

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
const COL_LABELS_MINI = ['1-10','11-20','21-30','31-40','41-50','51-60','61-70','71-80','81-90'];

// ── ESTADO ────────────────────────────────────────────
let drawnLocal     = [];
let lastLocal      = null;
let clockJob       = null;
let elapsedSec     = 0;
let gameStarted    = false;
let gameId         = null;
let lastPhraseKey  = null;
let soundEnabled   = false;
let currentAudio   = null;
let testAudio      = null;
let adminWasOnline = true;
let resetPending   = false;

// ── MI CARTILLA ───────────────────────────────────────
let myCartillaId  = null;
let myCartilla    = null;
let myBingoFired  = false;
let claimedBingo  = false;   // v6 — reclamo de bingo
let claimedLinea  = false;   // v6 — reclamo de línea
let almostSpoken  = false;   // v6 — evitar repetir "¡Falta 1!"

// ── TOAST ─────────────────────────────────────────────
let toastJob = null;
function showToast(msg, duration) {
  duration = duration || 2800;
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastJob);
  toastJob = setTimeout(function() { t.classList.remove('show'); }, duration);
}

// ── NAVIGATION ────────────────────────────────────────
function goToGame() { location.href = '/'; }

// ── INIT GRID ─────────────────────────────────────────
function initGrid() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');
  if (!headers || !grid) return;

  GROUP_LABELS.forEach(function(lbl, g) {
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
      cell.id           = 'cell-' + num;
      cell.textContent  = num;
      cell.style.gridColumn = col + 1;
      cell.style.gridRow    = row + 1;
      grid.appendChild(cell);
    }
  }
}

function initGridReset() {
  for (let n = 1; n <= 90; n++) {
    const cell = document.getElementById('cell-' + n);
    if (!cell) continue;
    cell.classList.remove('drawn', 'just-drawn');
    cell.style.color       = '';
    cell.style.background  = '';
    cell.style.borderColor = '';
  }
}

// ── AUDIO ─────────────────────────────────────────────
function stopAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio     = null;
  }
}

function stopTestAudio() {
  if (testAudio) {
    testAudio.pause();
    testAudio.src = '';
    testAudio     = null;
  }
}

function playPhrase(text, voice) {
  if (!soundEnabled || !text) return;
  stopAudio();
  voice = voice || 'es-PE-CamilaNeural';

  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text: text, voice: voice })
  })
  .then(function(r) {
    if (!r.ok) throw new Error('speak HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('audio')) throw new Error('speak returned non-audio: ' + ct);
    return r.blob();
  })
  .then(function(blob) {
    if (!blob || blob.size < 100) throw new Error('empty audio blob');
    const url    = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.volume = 0.9;
    var playPromise = currentAudio.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function(e) {
        console.warn('Audio play blocked:', e);
        showToast('🔇 Haz clic en la página para activar sonido');
      });
    }
    currentAudio.onended = function() {
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
  })
  .catch(function(e) {
    console.error('playPhrase error:', e);
  });
}

function enableSound() {
  soundEnabled = true;
  const btn = document.getElementById('btn-sound');
  if (btn) {
    btn.textContent       = '🔊 Sonido ON';
    btn.style.background  = 'var(--accent)';
    btn.style.color       = '#041015';
    btn.style.borderColor = 'var(--accent)';
  }

  const voice = (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural';
  showToast('🔊 Probando sonido…');

  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text: 'Sonido activado. ¡Buena suerte!', voice: voice })
  })
  .then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.blob();
  })
  .then(function(blob) {
    if (!blob || blob.size < 100) throw new Error('empty');
    stopTestAudio();
    const url  = URL.createObjectURL(blob);
    testAudio  = new Audio(url);
    testAudio.volume = 0.9;
    var p = testAudio.play();
    if (p && p.catch) p.catch(function(e) {
      if (e.name === 'NotAllowedError') {
        showToast('❌ El navegador bloqueó el audio. Intenta de nuevo.');
        soundEnabled = false;
        if (btn) { btn.textContent = '🔈 Activar sonido'; btn.style.background = ''; btn.style.color = ''; }
      }
    });
    testAudio.onended = function() {
      URL.revokeObjectURL(url);
      testAudio = null;
      showToast('✅ Sonido OK — escucharás cada bolilla');
    };
  })
  .catch(function(e) {
    console.error('Test audio error:', e);
    showToast('❌ Error de audio: ' + e.message);
  });
}

// ── v6: Reproducir alerta de falta 1 ─────────────────
function playAlmostAlert(voice) {
  if (!soundEnabled) return;
  const v = voice || 'es-PE-CamilaNeural';
  fetch('/api/speak', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: '¡Falta uno para el bingo!', voice: v }),
  })
  .then(function(r) { return r.ok ? r.blob() : null; })
  .then(function(blob) {
    if (!blob || blob.size < 100) return;
    stopAudio();
    const url = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.volume = 0.9;
    currentAudio.play().catch(function() {});
    currentAudio.onended = function() { URL.revokeObjectURL(url); currentAudio = null; };
  })
  .catch(function() {});
}

// ── ADMIN OFFLINE HANDLING ────────────────────────────
function handleAdminOffline() {
  if (!adminWasOnline || resetPending) return;
  adminWasOnline = false;
  resetPending   = true;

  const statusEl = document.getElementById('sync-status');
  if (statusEl) {
    statusEl.innerHTML = '<span style="color:var(--danger)">⚠️ Admin desconectado — reiniciando en 5s…</span>';
  }
  showToast('⚠️ El administrador se desconectó. Reiniciando tablero en 5s…', 5500);

  setTimeout(function() {
    drawnLocal    = [];
    lastLocal     = null;
    gameStarted   = false;
    gameId        = null;
    lastPhraseKey = null;
    elapsedSec    = 0;
    resetPending  = false;
    claimedBingo  = false;
    claimedLinea  = false;
    almostSpoken  = false;
    if (clockJob) { clearInterval(clockJob); clockJob = null; }
    stopAudio();

    initGridReset();
    removeClaimButtons();
    const bn = document.getElementById('big-number');
    if (bn) bn.textContent = '?';
    const wd = document.getElementById('words-display');
    if (wd) wd.textContent = '—';
    const gt = document.getElementById('group-tag');
    if (gt) gt.textContent = '';
    const rn = document.getElementById('recent-nums');
    if (rn) rn.innerHTML = '';
    const lb = document.getElementById('last-big');
    if (lb) { lb.textContent = '—'; lb.style.color = ''; }
    document.getElementById('timer').textContent = '⏱ 00:00';
    updateStats(0, 90);
    updateStatusMsg(0, 90);

    adminWasOnline = true;
    if (statusEl) statusEl.textContent = '🔄 Esperando al administrador…';
    showToast('🔄 Tablero reiniciado. Esperando nuevo sorteo…');
  }, 5000);
}

// ── SYNC CON EL SERVIDOR ──────────────────────────────
async function syncState() {
  if (resetPending) return;

  try {
    const res  = await fetch('/api/state');
    const data = await res.json();

    // v7.1 — actualizar countdown banner si está activo
    if (window._v71_cdHook) window._v71_cdHook(data);

    const serverDrawn  = data.drawn || [];
    const serverGameId = data.game_id;

    const statusEl = document.getElementById('sync-status');
    if (statusEl && adminWasOnline) {
      statusEl.innerHTML = '✅ Sincronizado';
    }

    // Admin timeout detection
    if (gameStarted && data.admin_online === false) {
      handleAdminOffline();
      return;
    }

    // New game ID = admin reset
    if (gameId && serverGameId && gameId !== serverGameId && drawnLocal.length > 0) {
      drawnLocal    = [];
      lastLocal     = null;
      gameStarted   = false;
      lastPhraseKey = null;
      elapsedSec    = 0;
      claimedBingo  = false;
      claimedLinea  = false;
      almostSpoken  = false;
      if (clockJob) { clearInterval(clockJob); clockJob = null; }
      stopAudio();
      initGridReset();
      removeClaimButtons();
      document.getElementById('timer').textContent = '⏱ 00:00';
      const lb = document.getElementById('last-big');
      if (lb) { lb.textContent = '—'; lb.style.color = ''; }
      showToast('🔄 El juego fue reiniciado por el administrador');
    }
    gameId = serverGameId;

    // No change → skip
    if (serverDrawn.length === drawnLocal.length) {
      // Aun sin cambio, actualizar pozo y ganadores en vivo
      updatePrizeDisplay(data);
      updateWinnersDisplay(data.winners || []);
      if (data.paused) {
        showPausedOverlay(data.winners || []);
      } else {
        hidePausedOverlay();
      }
      return;
    }

    const newNums = serverDrawn.filter(function(n) { return !drawnLocal.includes(n); });
    drawnLocal = serverDrawn;

    if (serverDrawn.length > 0 && !gameStarted) {
      gameStarted = true;
      startClock();
      updateStatusMsg(serverDrawn.length, data.remaining);
    }

    // Re-mark all (recovery after page reload)
    drawnLocal.forEach(function(n) { markCell(n, false); });

    if (newNums.length > 0) {
      const latest = newNums[newNums.length - 1];
      markCell(latest, true);
      updateDisplay(latest);
      updateStatusMsg(serverDrawn.length, data.remaining);

      const phrase = data.last_phrase;
      const pKey   = serverDrawn.length;
      if (phrase && pKey !== lastPhraseKey) {
        lastPhraseKey = pKey;
        const pVoice = (document.getElementById('player-voice-select') || {}).value
                    || data.last_voice
                    || 'es-PE-CamilaNeural';
        playPhrase(phrase, pVoice);
      }
    }

    updateRecent();
    updateStats(serverDrawn.length, data.remaining);
    updatePrizeDisplay(data);
    updateWinnersDisplay(data.winners || []);
    updateMyCartillaAutoMark();

    // Juego pausado por ganador
    if (data.paused) {
      showPausedOverlay(data.winners || []);
    } else {
      hidePausedOverlay();
    }

    if (data.remaining === 0 && serverDrawn.length === 90) {
      showGameOver();
    }

  } catch(e) {
    const statusEl = document.getElementById('sync-status');
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--warning)">⚠️ Sin conexión…</span>';
  }
}

// ── DISPLAY ───────────────────────────────────────────
function updateDisplay(num) {
  const g       = Math.min(Math.floor((num - 1) / 10), 8);
  const fg      = GROUP_COLORS[g].fg;
  const ball    = document.getElementById('ball');
  const bigNum  = document.getElementById('big-number');

  const ballMids  = ['#1a4a7a','#7a6010','#7a2020','#7a3810','#0f5a28','#4a1a6a','#0a5a4a','#1a3a5a','#2a3540'];
  const ballDarks = ['#0a1e2e','#2e2504','#3d0a08','#3d1800','#0a2e16','#22083d','#073832','#0a1f2e','#151d23'];

  ball.style.background = 'radial-gradient(circle at 35% 32%, #ffffff44 0%, ' +
    ballMids[g] + '99 30%, ' + ballDarks[g] + ' 70%, #020508 100%)';
  ball.style.boxShadow = '0 0 0 3px ' + fg + '55, 0 0 35px ' + fg + '33, ' +
    'inset 0 -8px 20px rgba(0,0,0,0.7), inset 0 8px 16px rgba(255,255,255,0.08)';

  ball.classList.remove('reveal');
  void ball.offsetWidth;
  ball.classList.add('reveal');
  setTimeout(function() { ball.classList.remove('reveal'); }, 600);

  bigNum.textContent      = num;
  bigNum.style.color      = fg;
  bigNum.style.textShadow = '0 0 20px ' + fg + '88, 0 2px 4px rgba(0,0,0,0.8)';

  const gt = document.getElementById('group-tag');
  if (gt) { gt.textContent = 'Grupo ' + GROUP_LABELS[g]; gt.style.color = fg; }

  const lb = document.getElementById('last-big');
  if (lb) { lb.textContent = num; lb.style.color = fg; }

  lastLocal = num;
}

function markCell(num, animate) {
  const cell = document.getElementById('cell-' + num);
  if (!cell) return;
  const g  = Math.min(Math.floor((num - 1) / 10), 8);
  const fg = GROUP_COLORS[g].fg;
  const bg = GROUP_COLORS[g].bg;
  cell.classList.add('drawn');
  if (animate) cell.classList.add('just-drawn');
  cell.style.color       = fg;
  cell.style.background  = bg;
  cell.style.borderColor = fg;
  if (animate) setTimeout(function() { cell.classList.remove('just-drawn'); }, 600);
}

function updateRecent() {
  const strip = document.getElementById('recent-nums');
  if (!strip) return;
  strip.innerHTML = '';
  drawnLocal.slice(-18).reverse().forEach(function(n) {
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
  const pct  = Math.round((count / 90) * 100);
  const prog = document.getElementById('progress');
  if (prog) prog.style.width = pct + '%';
  const pctEl = document.getElementById('stat-pct');
  if (pctEl) pctEl.textContent = pct + '%';
}

function updateStatusMsg(count, remaining) {
  const el = document.getElementById('game-status-msg');
  if (!el) return;
  if (count === 0) {
    el.textContent = 'Esperando que el administrador inicie el sorteo…';
  } else if (remaining === 0) {
    el.innerHTML = '<strong style="color:var(--accent)">🎉 ¡Juego completo!</strong><br>Se sortearon las 90 bolillas.';
  } else {
    el.innerHTML = 'Bolillas sorteadas: <strong style="color:var(--accent)">' + count + '</strong><br>' +
      'Quedan: <strong style="color:var(--warning)">' + remaining + '</strong> bolillas';
  }
}

// ── v6: Mostrar pozo en tiempo real ──────────────────
function updatePrizeDisplay(data) {
  const el = document.getElementById('game-status-msg');
  if (!el || !gameStarted) return;
  const pool  = data.prize_pool  || 0;
  const linea = data.linea_pool  || 0;
  const nW    = data.winners_count || 0;
  // Agregar línea de pozo al panel si no está ya
  let prizeEl = document.getElementById('prize-pool-display');
  if (!prizeEl) {
    prizeEl = document.createElement('div');
    prizeEl.id = 'prize-pool-display';
    prizeEl.style.cssText = 'margin-top:10px;font-size:.82rem;border-top:1px solid var(--border);padding-top:8px';
    el.parentNode.insertBefore(prizeEl, el.nextSibling);
  }
  const splitNote = nW > 1
    ? ' <span style="color:var(--warning);font-size:.75rem">(÷' + nW + ' ganadores)</span>'
    : '';
  prizeEl.innerHTML =
    '🎉 Premio BINGO: <strong style="color:var(--accent)">S/. ' + pool.toFixed(2) + '</strong>' + splitNote + '<br>' +
    '⭐ Premio LÍNEA: <strong style="color:var(--warning)">S/. ' + linea.toFixed(2) + '</strong>';
}

// ── v6: Mostrar ganadores en vivo ─────────────────────
function updateWinnersDisplay(winners) {
  if (!winners || !winners.length) return;
  let wEl = document.getElementById('live-winners-display');
  if (!wEl) {
    wEl = document.createElement('div');
    wEl.id = 'live-winners-display';
    wEl.style.cssText = 'margin-top:10px;font-size:.82rem;border-top:1px solid var(--border);padding-top:8px';
    const statusEl = document.getElementById('game-status-msg');
    if (statusEl && statusEl.parentNode) {
      statusEl.parentNode.appendChild(wEl);
    }
  }
  wEl.innerHTML = '🏆 <strong>Ganador(es):</strong> ' +
    winners.map(function(w) {
      const p = Number(w.prize || 0).toFixed(2);
      return '<span style="color:var(--accent)">' + (w.nombre || w.id) + '</span> (S/. ' + p + ')';
    }).join(', ');
}

// ── RELOJ ─────────────────────────────────────────────
function startClock() {
  elapsedSec = 0;
  if (clockJob) clearInterval(clockJob);
  clockJob = setInterval(function() {
    elapsedSec++;
    const h  = Math.floor(elapsedSec / 3600);
    const m  = Math.floor((elapsedSec % 3600) / 60);
    const s  = elapsedSec % 60;
    const mm = String(m).padStart(2, '0');
    const ss = String(s).padStart(2, '0');
    const timerEl = document.getElementById('timer');
    if (timerEl) timerEl.textContent = h ? ('⏱ ' + h + ':' + mm + ':' + ss) : ('⏱ ' + mm + ':' + ss);
  }, 1000);
}

// ── GAME OVER ─────────────────────────────────────────
function showGameOver() {
  const timerEl = document.getElementById('timer');
  const timer   = timerEl ? timerEl.textContent.replace('⏱ ', '') : '—';
  const infoEl  = document.getElementById('gameover-info');
  if (infoEl) infoEl.textContent = '¡Se sortearon las 90 bolillas en ' + timer + '!';
  const go = document.getElementById('gameover');
  if (go) go.classList.add('show');
  launchConfetti();
}

function hideGameOver() {
  const go = document.getElementById('gameover');
  if (go) go.classList.remove('show');
}

function launchConfetti() {
  const colors = ['#00e5b4','#f6c343','#e74c3c','#2f80ed','#a569bd','#58d68d'];
  for (let i = 0; i < 80; i++) {
    const c = document.createElement('div');
    c.className = 'confetti-piece';
    c.style.left              = (Math.random() * 100) + 'vw';
    c.style.background        = colors[Math.floor(Math.random() * colors.length)];
    c.style.animationDuration = (Math.random() * 2 + 2) + 's';
    c.style.animationDelay    = (Math.random() * 1.5) + 's';
    c.style.width             = (Math.random() * 8 + 4) + 'px';
    c.style.height            = (Math.random() * 8 + 4) + 'px';
    document.body.appendChild(c);
    setTimeout(function() { c.remove(); }, 5000);
  }
}

// ── CARTILLA UI ───────────────────────────────────────
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
  opt0.textContent = arr.length
    ? '— Selecciona tu cartilla —'
    : 'Aún no tienes cartillas en este dispositivo';
  sel.appendChild(opt0);
  arr.forEach(function(cid) {
    const o = document.createElement('option');
    o.value = cid; o.textContent = 'Cartilla ' + cid;
    sel.appendChild(o);
  });
  const last = localStorage.getItem('active_cartilla') || '';
  if (last && arr.includes(last)) sel.value = last;
}

function renderMyCartilla(grid, drawnSet) {
  const wrap = document.getElementById('my-cartilla-grid');
  if (!wrap) return;
  wrap.innerHTML = '';

  const miniWrap = document.getElementById('cartilla-mini-wrap');
  if (miniWrap) miniWrap.style.display = 'block';

  const colsEl = document.getElementById('cartilla-mini-cols');
  if (colsEl && !colsEl.dataset.built) {
    colsEl.dataset.built = '1';
    colsEl.innerHTML = '';
    COL_LABELS_MINI.forEach(function(lbl, ci) {
      var d = document.createElement('div');
      d.className   = 'cartilla-mini-col-label';
      d.textContent = lbl;
      d.style.color      = GROUP_COLORS[ci].fg;
      d.style.background = GROUP_COLORS[ci].bg;
      colsEl.appendChild(d);
    });
  }

  for (let ri = 0; ri < 3; ri++) {
    for (let ci = 0; ci < 9; ci++) {
      const num  = grid[ri][ci];
      const g    = GROUP_COLORS[ci];
      const cell = document.createElement('div');
      cell.className = 'c-cell';
      if (num === null || num === undefined) {
        cell.classList.add('empty'); cell.textContent = '·';
      } else {
        cell.classList.add('filled');
        if (drawnSet && drawnSet.has(num)) {
          cell.classList.add('marked');
          cell.style.borderColor = g.fg;
          const s = document.createElement('span');
          s.textContent = num; s.style.background = g.fg;
          cell.appendChild(s);
        } else {
          cell.textContent = num; cell.style.color = g.fg + '44';
        }
      }
      wrap.appendChild(cell);
    }
  }
}

async function loadSelectedCartilla() {
  const sel = document.getElementById('my-cartilla-select');
  const cid = (sel ? sel.value : '').trim().toUpperCase();
  if (!cid) { showToast('🎴 Selecciona una cartilla'); return; }
  myCartillaId  = cid;
  claimedBingo  = false;
  claimedLinea  = false;
  almostSpoken  = false;
  myBingoFired  = false;
  localStorage.setItem('active_cartilla', cid);
  try {
    const res  = await fetch('/api/cartilla/' + cid);
    const data = await res.json();
    if (!res.ok) { showToast('❌ No se encontró esa cartilla'); return; }
    myCartilla = data;
    const meta = document.getElementById('my-cartilla-meta');
    if (meta) meta.innerHTML = 'ID: <strong style="color:var(--text)">' + data.id + '</strong>';
    updateMyCartillaAutoMark(true);
    showToast('✅ Cartilla ' + cid + ' cargada');
    try {
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    } catch(e) {}
  } catch(e) { showToast('❌ Error al cargar cartilla'); }
}

function isMyCartillaBingo(drawnSet) {
  if (!myCartilla || !myCartilla.grid) return false;
  const nums = [];
  for (const row of myCartilla.grid) for (const n of row) if (n !== null && n !== undefined) nums.push(n);
  return nums.length && nums.every(function(n) { return drawnSet.has(n); });
}

function isMyCartillaLinea(drawnSet) {
  if (!myCartilla || !myCartilla.grid) return false;
  for (const row of myCartilla.grid) {
    const rowNums = row.filter(function(n) { return n !== null && n !== undefined; });
    if (rowNums.length && rowNums.every(function(n) { return drawnSet.has(n); })) return true;
  }
  return false;
}

function countMarked(drawnSet) {
  if (!myCartilla || !myCartilla.grid) return 0;
  let c = 0;
  for (const row of myCartilla.grid) for (const n of row) if (n && drawnSet.has(n)) c++;
  return c;
}

// ── v6: Botones de reclamo ────────────────────────────
function removeClaimButtons() {
  ['claim-bingo-btn','claim-linea-btn','almost-alert'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.remove();
  });
}

function manageClaimButtons(isBingo, isLinea, isAlmost, voice) {
  const status = document.getElementById('my-cartilla-status');
  if (!status) return;

  // ── Botón BINGO ──
  if (isBingo && !claimedBingo) {
    let btn = document.getElementById('claim-bingo-btn');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'claim-bingo-btn';
      btn.style.cssText = [
        'width:100%;padding:14px;margin-top:10px;border:none;border-radius:12px;',
        'background:linear-gradient(135deg,#00e5b4,#2f80ed);',
        'color:#041015;font-family:Outfit,sans-serif;font-weight:900;',
        'font-size:1.1rem;cursor:pointer;',
        'animation:glowBingo 1.8s ease-in-out infinite;',
      ].join('');
      btn.innerHTML = '🎉 ¡RECLAMAR BINGO!';
      btn.onclick = claimBingo;
      status.parentNode.insertBefore(btn, status.nextSibling);
    }
  }

  // ── Botón LÍNEA ──
  if (isLinea && !claimedLinea) {
    let btn = document.getElementById('claim-linea-btn');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'claim-linea-btn';
      btn.style.cssText = [
        'width:100%;padding:12px;margin-top:8px;border:none;border-radius:10px;',
        'background:linear-gradient(135deg,#f6c343,#e59866);',
        'color:#1a0a00;font-family:Outfit,sans-serif;font-weight:900;',
        'font-size:.95rem;cursor:pointer;',
      ].join('');
      btn.innerHTML = '⭐ ¡RECLAMAR LÍNEA!';
      btn.onclick = claimLinea;
      status.parentNode.insertBefore(btn, status.nextSibling);
    }
  }

  // ── Alerta Falta 1 ──
  if (isAlmost && !isBingo) {
    let al = document.getElementById('almost-alert');
    if (!al) {
      al = document.createElement('div');
      al.id = 'almost-alert';
      al.style.cssText = [
        'background:rgba(246,195,67,.1);border:2px solid var(--warning);',
        'border-radius:12px;padding:14px;text-align:center;margin-top:10px;',
      ].join('');
      al.innerHTML = [
        '<h3 style="color:var(--warning);font-family:Bebas Neue,sans-serif;',
        'font-size:1.6rem;letter-spacing:2px;margin:0 0 4px;',
        'animation:pulse 1s ease-in-out infinite">¡FALTA 1!</h3>',
        '<p style="color:var(--muted);font-size:.82rem;margin:0">',
        'Solo te falta un número para el BINGO</p>',
      ].join('');
      status.parentNode.insertBefore(al, status.nextSibling);
      if (!almostSpoken) {
        almostSpoken = true;
        playAlmostAlert(voice);
      }
    }
  } else {
    // Quitar alerta si ya no falta 1
    var alEl = document.getElementById('almost-alert');
    if (alEl && !isAlmost) alEl.remove();
    if (!isAlmost) almostSpoken = false;
  }
}

// ── Audio de victoria ─────────────────────────────────
function playWinAlert() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = 880;
    o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.4, ctx.currentTime + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.2);
    o.start(); o.stop(ctx.currentTime + 1.25);
    setTimeout(function() { ctx.close(); }, 1400);
  } catch(e) {}
  try { if (navigator.vibrate) navigator.vibrate([200, 100, 200, 100, 400]); } catch(e) {}
}

function showWinNotification(title, body) {
  try {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') new Notification(title, { body: body });
  } catch(e) {}
}

// ── Actualizar cartilla del jugador ──────────────────
function updateMyCartillaAutoMark(force) {
  const status = document.getElementById('my-cartilla-status');
  const btnBuy = document.getElementById('btn-go-cartillas');
  if (btnBuy) {
    btnBuy.disabled    = gameStarted;
    btnBuy.textContent = gameStarted ? 'Ver/Ya compré' : '🎴 Comprar cartilla';
  }
  if (!myCartilla || !myCartilla.grid) {
    if (status) status.textContent = gameStarted
      ? '💡 El juego ya empezó. Carga tu cartilla para marcar automáticamente.'
      : '💡 Puedes comprar/generar tu cartilla antes de que empiece el juego.';
    return;
  }

  const drawnSet = new Set(drawnLocal);
  renderMyCartilla(myCartilla.grid, drawnSet);

  const marked  = countMarked(drawnSet);
  const isBingo = isMyCartillaBingo(drawnSet);
  const isLinea = isMyCartillaLinea(drawnSet);
  const total   = 15;
  const isAlmost = !isBingo && (total - marked === 1);

  if (status) {
    if (isBingo) {
      status.innerHTML = '<strong style="color:var(--accent)">🎉 ¡BINGO! Marcadas: ' + marked + '/15</strong>';
    } else if (isAlmost) {
      status.innerHTML = '<strong style="color:var(--warning)">🔥 ¡Solo falta 1 número!</strong>';
    } else {
      status.innerHTML = 'Marcadas: <strong style="color:var(--accent)">' + marked + ' / 15</strong>';
    }
  }

  const voice = (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural';
  manageClaimButtons(isBingo, isLinea, isAlmost, voice);

  // Auto-trigger bingo alert (vibración + sonido) la primera vez
  if (!myBingoFired && isBingo) {
    myBingoFired = true;
    playWinAlert();
    showToast('🎉 ¡BINGO! Haz clic en el botón para reclamar.', 5000);
    showWinNotification('🎉 ¡BINGO!', 'Haz clic en el botón para reclamar tu premio.');
  }
}

function initMyCartillaUI() {
  populateMyCartillaSelect();
  const btn = document.getElementById('btn-load-cartilla');
  if (btn) btn.addEventListener('click', loadSelectedCartilla);
  const sel = document.getElementById('my-cartilla-select');
  if (sel) sel.addEventListener('change', function() {
    const cid = (sel.value || '').trim();
    if (cid) loadSelectedCartilla();
  });
  window.addEventListener('focus', populateMyCartillaSelect);
}

// ── v7.2: Reclamar BINGO (pozo dividido) ─────────────
async function claimBingo() {
  if (!myCartilla) return;
  try {
    const res  = await fetch('/api/winner/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: myCartilla.id }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      const msgs = {
        not_bingo: '❌ Tu cartilla no tiene bingo todavía (' + (data.marked||0) + '/' + (data.total||15) + ')',
        not_found: '❌ Cartilla no encontrada',
      };
      showToast(msgs[data.error] || '❌ ' + data.error);
      return;
    }
    claimedBingo = true;
    document.getElementById('claim-bingo-btn')?.remove();

    const winner  = data.winner || {};
    const prize   = Number(data.prize_each || winner.prize || 0).toFixed(2);
    const nW      = data.n_winners || 1;

    // v7.2 — mensaje según si hay empate o no
    if (data.split && nW > 1) {
      showToast('🎉 ¡BINGO! Empate con ' + nW + ' ganadores — Tu premio: S/. ' + prize, 6000);
      showWinNotification('🎉 ¡BINGO! — Empate',
        'Compartes el pozo con ' + nW + ' ganadores. Tu parte: S/. ' + prize);
    } else {
      showToast('🎉 ¡BINGO registrado! Premio: S/. ' + prize + '. El admin confirmará.', 5000);
      showWinNotification('🎉 ¡BINGO!', 'Premio: S/. ' + prize);
    }
    showGameOverPlayer({ ...winner, prize: Number(prize), n_winners: nW, split: data.split });
  } catch(e) { showToast('❌ Error de conexión'); }
}

// ── v7.2: Reclamar LÍNEA (jugador puede ganar bingo también) ──
async function claimLinea() {
  if (!myCartilla) return;
  try {
    const res  = await fetch('/api/winner/claim_linea', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: myCartilla.id }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      if (data.error === 'not_linea') showToast('❌ Tu cartilla no tiene línea completa');
      else if (data.already)         showToast('ℹ️ Ya habías reclamado la línea');
      else                           showToast('❌ ' + data.error);
      return;
    }
    claimedLinea = true;
    document.getElementById('claim-linea-btn')?.remove();

    const lineaPrize = Number(data.linea_prize || 0).toFixed(2);
    const nWL        = data.n_winners || 1;

    if (data.split && nWL > 1) {
      showToast('⭐ ¡Línea! Empate con ' + nWL + ' — Tu parte: S/. ' + lineaPrize + '. ¡Sigue jugando para el BINGO!', 5000);
    } else {
      showToast('⭐ ¡Línea registrada! Premio: S/. ' + lineaPrize + '. ¡Sigue jugando para el BINGO!', 5000);
    }
    // v7.2 — ganar LÍNEA NO impide reclamar BINGO después
    claimedBingo = false;
  } catch(e) { showToast('❌ Error de conexión'); }
}

// ── Pantalla de victoria del jugador ─────────────────
function showGameOverPlayer(winner) {
  const go   = document.getElementById('gameover');
  if (!go) return;
  const info = document.getElementById('gameover-info');
  if (info && winner) {
    const prize  = Number(winner.prize || 0).toFixed(2);
    const nW     = winner.n_winners || 1;
    const splitLine = (winner.split && nW > 1)
      ? '<br><span style="color:var(--warning);font-size:.85rem">Empate entre ' + nW + ' ganadores — S/. ' + prize + ' cada uno</span>'
      : '';
    const yapePlin = (localStorage.getItem('bingo_yape_plin') || '').trim();
    const yapeBox  = yapePlin
      ? '<div style="margin-top:14px;padding:10px 16px;background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.3);border-radius:10px;font-size:.85rem">' +
        '<div style="color:var(--muted);margin-bottom:4px">Tu premio sera enviado a Yape/Plin:</div>' +
        '<div style="font-size:1.2rem;font-weight:900;color:var(--accent);letter-spacing:2px">' + yapePlin + '</div>' +
        '<div style="font-size:.72rem;color:var(--muted);margin-top:4px">Numero que registraste al comprar tu cartilla</div>' +
        '</div>'
      : '<div style="margin-top:10px;font-size:.8rem;color:var(--muted)">El premio sera enviado al Yape/Plin que registraste.</div>';
    info.innerHTML = '¡Felicidades <strong>' + (winner.nombre || '') + '</strong>!<br>' +
      'Premio: <span style="color:var(--accent);font-size:1.3rem">S/. ' + prize + '</span> 🎉' + splitLine + yapeBox;
  }
  go.classList.add('show');
  launchConfetti();
  if (soundEnabled) {
    playPhrase('¡Felicidades! ¡Ganaste el bingo!',
      (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural');
  }
}

// ── PAUSA POR GANADOR (overlay jugador) ───────────────
let pausedOverlayShown = false;

function showPausedOverlay(winners) {
  if (pausedOverlayShown) return;
  pausedOverlayShown = true;

  const iWon = myCartillaId && winners.some(function(w) { return w.id === myCartillaId; });

  let existing = document.getElementById('player-paused-overlay');
  if (!existing) {
    existing = document.createElement('div');
    existing.id = 'player-paused-overlay';
    existing.style.cssText = [
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);',
      'background:var(--panel);border:2px solid var(--accent);',
      'border-radius:16px;padding:18px 28px;z-index:400;',
      'text-align:center;max-width:380px;width:90%;',
      'box-shadow:0 0 30px rgba(0,229,180,.2);',
    ].join('');
    document.body.appendChild(existing);
  }

  if (iWon) {
    const myWinner = winners.find(function(w) { return w.id === myCartillaId; });
    const prize    = myWinner ? Number(myWinner.prize || 0).toFixed(2) : '0.00';
    const nW       = myWinner ? (myWinner.n_winners || 1) : 1;
    const splitMsg = (myWinner && myWinner.split && nW > 1)
      ? '<div style="font-size:.8rem;color:var(--warning);margin-top:4px">Empate entre ' + nW + ' jugadores · S/. ' + prize + ' cada uno</div>'
      : '<div style="font-size:.9rem;color:var(--accent);margin-top:4px;font-weight:700">Premio: S/. ' + prize + '</div>';
    const yapePlin = (localStorage.getItem('bingo_yape_plin') || '').trim();
    const yapeBox  = yapePlin
      ? '<div style="margin-top:10px;padding:8px 12px;background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.3);border-radius:8px">' +
        '<div style="font-size:.7rem;color:var(--muted)">Tu premio sera enviado a Yape/Plin:</div>' +
        '<div style="font-size:1.1rem;font-weight:900;color:var(--accent);letter-spacing:2px">' + yapePlin + '</div>' +
        '</div>'
      : '';
    existing.style.borderColor = 'var(--accent)';
    existing.innerHTML = [
      '<div style="font-size:2.5rem;margin-bottom:4px;">🎉</div>',
      '<div style="font-family:Bebas Neue,sans-serif;font-size:2rem;color:var(--accent);letter-spacing:3px;">¡GANASTE!</div>',
      splitMsg,
      yapeBox,
      '<div style="font-size:.8rem;color:var(--muted);margin-top:6px;">El admin verificará tu cartilla.</div>',
    ].join('');
    playWinAlert();
  } else {
    const names = winners.map(function(w) {
      return '<strong style="color:var(--accent)">' + (w.nombre || w.id) + '</strong>';
    }).join(', ');
    existing.style.borderColor = 'var(--warning)';
    existing.innerHTML = [
      '<div style="font-size:1.5rem;margin-bottom:4px;">⏸</div>',
      '<div style="font-weight:700;color:var(--warning);margin-bottom:6px;">Juego Pausado</div>',
      '<div style="font-size:.85rem;color:var(--muted);">',
        names ? ('Ganador(es): ' + names) : 'Hay un ganador.',
      '</div>',
      '<div style="font-size:.75rem;color:var(--muted);margin-top:5px;">Esperando al administrador…</div>',
    ].join('');
  }
}

function hidePausedOverlay() {
  if (!pausedOverlayShown) return;
  pausedOverlayShown = false;
  var el = document.getElementById('player-paused-overlay');
  if (el) el.remove();
}

// ── INIT ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  const urlEl = document.getElementById('server-url');
  if (urlEl) urlEl.textContent = window.location.href;

  initGrid();
  initMyCartillaUI();

  syncState();
  setInterval(syncState, 1500);
});