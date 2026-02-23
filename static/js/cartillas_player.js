/* Player cartilla page */
const COL_LABELS = ['1-9','10-19','20-29','30-39','40-49','50-59','60-69','70-79','80-90'];
let selected = new Set();

let toastJob=null;
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');clearTimeout(toastJob);toastJob=setTimeout(()=>t.classList.remove('show'),2800);}

function goToGame(){ location.href = '/'; }

async function checkStarted(){
  try{
    const s = await (await fetch('/api/state')).json();
    if ((s.drawn||[]).length>0){
      document.getElementById('started-overlay').classList.add('show');
      // auto redirect after 3s
      setTimeout(()=>{ location.href='/?started=1'; }, 3000);
    }
  }catch(e){}
}

function toggleManual(){
  const area = document.getElementById('manual-area');
  area.style.display = area.style.display==='none' ? 'block' : 'none';
}

function getCode(){ return (document.getElementById('inp-code').value||'').trim().toUpperCase(); }
function getName(){ return (document.getElementById('inp-name').value||'').trim() || 'Jugador'; }

function saveMyCartilla(cid){
  const key='my_cartillas';
  const arr = JSON.parse(localStorage.getItem(key)||'[]');
  if(!arr.includes(cid)){ arr.unshift(cid); }
  localStorage.setItem(key, JSON.stringify(arr.slice(0,30)));
}

function loadMyCartillas(){
  const arr = JSON.parse(localStorage.getItem('my_cartillas')||'[]');
  if(!arr.length){ document.getElementById('mylist').innerHTML = '<div style="color:var(--muted);margin-top:10px;">Aún no generas cartillas.</div>'; return; }
  document.getElementById('mylist').innerHTML = arr.map(cid=>{
    const pdf = `/api/cartilla/${cid}/pdf`;
    const png = `/api/cartilla/${cid}/png`;
    const msg = encodeURIComponent(`Hola! Esta es mi cartilla de Bingo.\nID: ${cid}\n(Puedes descargar PNG/PDF desde el link)`);
    const wa  = `https://wa.me/?text=${msg}`;
    return `<div class="myitem">
      <div class="meta"><div style="font-weight:900;">Cartilla ${cid}</div><div class="hint">Guarda el ID por si lo necesitas</div></div>
      <div class="actions">
        <a class="btn btn-ghost btn-sm" href="${png}" target="_blank" rel="noopener">🖼 PNG</a>
        <a class="btn btn-ghost btn-sm" href="${pdf}" target="_blank" rel="noopener">📄 PDF</a>
        <a class="btn btn-sm" href="${wa}" target="_blank" rel="noopener">📲 WhatsApp</a>
      </div>
    </div>`;
  }).join('');
}

async function generateAuto(){
  const code = getCode();
  if(!code){ showToast('🔑 Ingresa el código'); return; }
  const nombre = getName();
  showToast('⏳ Generando…');
  const res = await fetch('/api/cartilla/generate', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nombre, count:1, code})
  });
  const data = await res.json();
  if(!res.ok){
    if(data.error==='bad_code') showToast('❌ Código inválido');
    else if(data.error==='used_code') showToast('⚠️ Código ya fue usado');
    else if(data.error==='game_started') showToast('⛔ El juego ya empezó');
    else showToast('❌ ' + (data.error||'Error'));
    return;
  }
  const cid = data.cartillas[0].id;
  saveMyCartilla(cid);
  showToast(`✅ Cartilla ${cid} creada`);
  loadMyCartillas();
}

function buildPicker(){
  const picker = document.getElementById('picker');
  picker.innerHTML='';
  picker.style.display='grid';
  picker.style.gridTemplateColumns='repeat(9, 1fr)';
  picker.style.gap='8px';

  for(let ci=0; ci<9; ci++){
    const col = document.createElement('div');
    col.style.border='1px solid var(--border)';
    col.style.borderRadius='14px';
    col.style.background='var(--card)';
    col.style.padding='10px';

    const h=document.createElement('div');
    h.textContent = COL_LABELS[ci];
    h.style.color='var(--muted)';
    h.style.fontWeight='800';
    h.style.marginBottom='8px';
    col.appendChild(h);

    const start = ci===0 ? 1 : ci*10;
    const end   = ci===0 ? 9 : ci===8 ? 90 : ci*10+9;
    for(let n=start; n<=end; n++){
      const b=document.createElement('button');
      b.textContent=n;
      b.className='btn btn-ghost btn-sm';
      b.style.width='100%';
      b.style.marginBottom='6px';
      b.onclick=()=>toggleNum(n,b);
      col.appendChild(b);
    }

    picker.appendChild(col);
  }
}

function toggleNum(n, btn){
  if(selected.has(n)){ selected.delete(n); btn.style.borderColor=''; btn.style.color=''; return; }
  if(selected.size>=15){ showToast('Máximo 15 números'); return; }
  selected.add(n);
  btn.style.borderColor='var(--accent)';
  btn.style.color='var(--accent)';
}

function clearManual(){
  selected = new Set();
  buildPicker();
  showToast('🧹 Selección limpiada');
}

function selectedToGrid(){
  // Build grid like admin picker: 3x9 with 15 nums, 5 per row. We'll use a simple deterministic placement.
  // Group numbers by column.
  const cols = Array.from({length:9}, ()=>[]);
  [...selected].sort((a,b)=>a-b).forEach(n=>{
    let ci=0;
    if(n<=9) ci=0;
    else ci = Math.min(8, Math.floor(n/10));
    cols[ci].push(n);
  });
  // Validate counts: each column 1 or 2, total 15
  if(selected.size !== 15) throw new Error('Selecciona exactamente 15 números');
  for(const arr of cols){
    if(arr.length<1 || arr.length>2) throw new Error('Cada columna debe tener 1 o 2 números');
  }
  // Assign rows to enforce 5 per row: greedy
  const grid = Array.from({length:3}, ()=>Array(9).fill(null));
  const rowCounts=[0,0,0];
  for(let ci=0; ci<9; ci++){
    const nums = cols[ci].slice().sort((a,b)=>a-b);
    // pick rows with least count
    const rows = [0,1,2].sort((r1,r2)=>rowCounts[r1]-rowCounts[r2]);
    if(nums.length===1){
      const r = rows[0];
      grid[r][ci]=nums[0]; rowCounts[r]++;
    }else{
      const r1 = rows[0], r2=rows[1];
      grid[r1][ci]=nums[0]; rowCounts[r1]++;
      grid[r2][ci]=nums[1]; rowCounts[r2]++;
    }
  }
  if(!(rowCounts[0]===5 && rowCounts[1]===5 && rowCounts[2]===5)){
    throw new Error('Tu selección no permite 5 números por fila. Prueba otra combinación.');
  }
  return grid;
}

async function saveManual(){
  const code = getCode();
  if(!code){ showToast('🔑 Ingresa el código'); return; }
  if(selected.size!==15){ showToast('Selecciona 15 números'); return; }
  let grid;
  try{ grid = selectedToGrid(); }
  catch(e){ showToast('❌ ' + e.message); return; }

  const nombre = getName();
  showToast('⏳ Guardando…');
  const res = await fetch('/api/cartilla/save_manual', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nombre, grid, code})
  });
  const data = await res.json();
  if(!res.ok){
    if(data.error==='bad_code') showToast('❌ Código inválido');
    else if(data.error==='used_code') showToast('⚠️ Código ya fue usado');
    else if(data.error==='game_started') showToast('⛔ El juego ya empezó');
    else showToast('❌ ' + (data.error||'Error'));
    return;
  }
  const cid = data.cartilla.id;
  saveMyCartilla(cid);
  showToast(`✅ Cartilla ${cid} guardada`);
  loadMyCartillas();
}

buildPicker();
loadMyCartillas();
checkStarted();
setInterval(checkStarted, 3000);
