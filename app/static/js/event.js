(function(){
  const wheelEl = document.getElementById('wheel');
  const eid = wheelEl ? wheelEl.dataset.eventId : null;
  if(!eid) return;
  const timerEl = document.getElementById('timer');
  const wheel = document.getElementById('wheel');
  const activeEl = document.getElementById('active');
  const outEl = document.getElementById('out');
  const winnerEl = document.getElementById('winner');
  const stagesWrap = document.getElementById('stages');
  const stagesList = document.getElementById('stages-list');
  const ROOT = (document.body && document.body.dataset && document.body.dataset.rootPath) || '';

  let state = null; // will hold API state
  let participantsById = {}; // id -> {id,name,image_url,weight}
  // track current active/out ids on client
  let currentActiveIds = [];
  let currentOutIds = [];
  // finalization flag to ignore late messages
  let isFinishedClient = false;
  // wheel strip state
  let stripEl = null; // .strip element inside #wheel
  let baseIds = []; // current active ids (unique order)
  let extIds = [];  // extended repeated ids for long scroll
  const ROW_H = 70; // must match CSS height of .item
  const VISIBLE = 3; // rows visible in viewport
  const CENTER_ROW = 1; // 0-based index of the highlighted row
  let currentIndex = 0; // current top index (0 at initial)

  function cardHtml(p){
    const src = p.image_url || (ROOT + '/static/img/avatar-placeholder.png');
    const img = `<img src="${src}" alt="${p.name}" style="width:48px;height:48px;object-fit:cover;border-radius:4px;margin-right:8px;">`;
    return `<div class="row" style="display:flex;align-items:center;margin:4px 0;">
      ${img}
      <div><div><b>${p.name}</b></div><div style="font-size:12px;color:#888;">вес: ${p.weight}</div></div>
    </div>`;
  }

  function renderParticipants(activeIds, outIds){
    activeEl.innerHTML = activeIds.map(id => participantsById[id] ? cardHtml(participantsById[id]) : `#${id}`).join('') || '<i>пусто</i>';
    outEl.innerHTML = outIds.map(id => participantsById[id] ? cardHtml(participantsById[id]) : `#${id}`).join('') || '<i>нет</i>';
  }

  function renderListIds(el, ids){
    el.innerHTML = ids.map(id=>`<div>#${id}</div>`).join('') || '<i>пусто</i>';
  }

  function itemHtmlById(id){
    const p = participantsById[id];
    const src = (p && p.image_url) ? p.image_url : (ROOT + '/static/img/avatar-placeholder.png');
    const name = p ? p.name : (id ? ('#'+id) : '—');
    const meta = p ? `вес: ${p.weight}` : '';
    return `<div class="item"><img src="${src}" alt="${name}"><div><div class="name">${name}</div>${meta? `<div class="meta">${meta}</div>`:''}</div></div>`;
  }

  function buildWheel(ids){
    wheel.innerHTML = '';
    stripEl = document.createElement('div');
    stripEl.className = 'strip';
    // normalize base ids
    baseIds = Array.isArray(ids) && ids.length ? ids.slice() : ['_empty_'];

    // Build extended list to allow long scrolls
    const repeats = Math.max(8, 6 * Math.ceil(10 / baseIds.length));
    extIds = [];
    for(let r=0;r<repeats;r++){
      for(let i=0;i<baseIds.length;i++) extIds.push(baseIds[i]);
    }

    // Fill DOM once
    stripEl.innerHTML = extIds.map(id=>itemHtmlById(id)).join('');
    wheel.appendChild(stripEl);

    // Place start index somewhere in the middle so we can scroll down a lot
    currentIndex = Math.floor(baseIds.length * 2);
    const startTop = (currentIndex)*ROW_H;
    stripEl.style.transition = 'none';
    stripEl.style.transform = `translateY(${-startTop}px)`;
  }

  let tickTimer = null;
  function startCountdownTo(dateIso){
    clearInterval(tickTimer);
    function pad(n){ return n<10? '0'+n : ''+n; }
    function update(){
      const now = new Date();
      const target = new Date(dateIso);
      let diff = Math.floor((target - now)/1000);
      if(diff <= 0){
        clearInterval(tickTimer);
        timerEl.textContent = 'Скоро начало...';
        return;
      }
      const d = Math.floor(diff/86400); diff%=86400;
      const h = Math.floor(diff/3600); diff%=3600;
      const m = Math.floor(diff/60); const s = diff%60;
      const dstr = d>0 ? d+`д ` : '';
      timerEl.textContent = `Старт через ${dstr}${pad(h)}:${pad(m)}:${pad(s)}`;
    }
    update();
    tickTimer = setInterval(update, 1000);
  }

  let spinTimer = null;
  function shortCountdown(sec, labelStart='Старт через', labelEnd='Выбор...'){
    clearInterval(spinTimer);
    let left = sec;
    timerEl.textContent = `${labelStart} ${left} сек.`;
    spinTimer = setInterval(()=>{
      left -= 1;
      if(left <= 0){ clearInterval(spinTimer); timerEl.textContent = labelEnd; }
      else timerEl.textContent = `${labelStart} ${left} сек.`;
    }, 1000);
  }

  function findNextIndexOf(pid, startIdx){
    for(let i=startIdx;i<extIds.length;i++) if(extIds[i] === pid) return i;
    // if not found (shouldn't happen), fallback to last element
    return extIds.length-1;
  }

  function ensureScrollRoom(minCycles=3){
    // make sure currentIndex starts far enough from the end to allow long scrolls
    const remain = extIds.length - currentIndex - 1;
    const need = minCycles * baseIds.length + VISIBLE + 2;
    if(remain < need){
      // rebuild strip by appending more repeats
      const addRepeats = Math.ceil((need - remain) / baseIds.length) + 4;
      const chunk = [];
      for(let r=0;r<addRepeats;r++) for(let i=0;i<baseIds.length;i++) chunk.push(baseIds[i]);
      extIds = extIds.concat(chunk);
      // append to DOM
      const frag = document.createElement('div');
      frag.innerHTML = chunk.map(id=>itemHtmlById(id)).join('');
      while(frag.firstChild) stripEl.appendChild(frag.firstChild);
    }
  }

  function highlightPick(pid){
    if(!stripEl) return Promise.resolve();
    ensureScrollRoom(3);
    // choose a target index at least several cycles ahead so it looks like a spin
    const minAhead = currentIndex + baseIds.length * (baseIds.length >= 3 ? 3 : 5);
    let targetIndex = findNextIndexOf(pid, minAhead);
    // final top index should position chosen item at the center row
    const finalTopIndex = Math.max(0, targetIndex - CENTER_ROW);
    const targetTop = finalTopIndex * ROW_H;

    // compute current top from currentIndex
    const currentTop = currentIndex * ROW_H;

    // single-phase animation with tuned easing: longer fast section, swift final alignment
    const distance = targetTop - currentTop;
    const cycles = Math.max(1, Math.floor(distance / (ROW_H * baseIds.length)));
    const duration = 4.4 + Math.min(1.2, cycles * 0.22);

    // Use an easing that keeps velocity high for most of the path and eases out late
    const easing = 'cubic-bezier(.18,.98,.24,1)';

    // apply transform with one continuous transition
    stripEl.style.transition = `transform ${duration}s ${easing}`;
    return new Promise(resolve => {
      requestAnimationFrame(()=>{
        stripEl.style.transform = `translateY(${-targetTop}px)`;
      });
      setTimeout(()=>{
        const p = participantsById[pid];
        timerEl.textContent = p ? `Выбран: ${p.name}` : `Выпал участник #${pid}`;
        currentIndex = finalTopIndex;
        resolve();
      }, duration*1000);
    });
  }

  function showWinner(pid, finishedAt){
    const p = participantsById[pid];
    if(!p || !winnerEl) return;
    const endStr = finishedAt ? new Date(finishedAt).toLocaleString() : '';
    winnerEl.style.display = 'block';
    winnerEl.innerHTML = `<h3>Победитель</h3>${cardHtml(p)}${endStr? `<div style="margin-top:6px;font-size:12px;color:#666;">Завершено: ${endStr}</div>`:''}`;
  }

  function addStage(timeIso, pid, eliminated){
    if(!stagesWrap || !stagesList) return;
    if(state && state.event && state.event.type !== 'reverse') return; // stages relevant only for reverse
    stagesWrap.style.display = 'block';
    const p = participantsById[pid];
    const timeStr = new Date(timeIso).toLocaleTimeString();
    const li = document.createElement('li');
    li.textContent = `${timeStr}: ${p? p.name : ('#'+pid)} ${eliminated? '— выбыл' : '— победитель'}`;
    stagesList.appendChild(li);
  }

  async function init(){
    // fetch initial state
    try{
      const res = await fetch(`${ROOT}/api/events/${eid}/state`);
      if(!res.ok) throw new Error('failed to load state');
      state = await res.json();
    }catch(e){ console.error(e); }
    if(!state) return;
    // build participants map
    (state.participants||[]).forEach(p=>{ participantsById[p.id] = p; });

    // initial render
    const startsAt = state.event && state.event.starts_at;
    if(startsAt && !(state.event.in_progress || state.event.finished)){
      startCountdownTo(startsAt);
    } else if(state.event.finished){
      timerEl.textContent = 'Событие завершено';
    } else if(state.event.in_progress){
      timerEl.textContent = 'Идет событие';
    }

    // active/out based on stages if finished/in_progress
    const eliminatedIds = (state.stages||[]).filter(s=>s.eliminated).map(s=>s.participant_id);
    const activeIds = (state.participants||[]).map(p=>p.id).filter(id=>!eliminatedIds.includes(id));
    currentActiveIds = activeIds.slice();
    currentOutIds = eliminatedIds.slice();
    renderParticipants(currentActiveIds, currentOutIds);

    // if finished, hide the wheel and show winner instead
    if(state.event && state.event.finished){
      if(wheel) wheel.style.display = 'none';
    } else {
      buildWheel(currentActiveIds);
    }

    // render past stages for reverse events
    (state.stages||[]).forEach(s=> addStage(s.time, s.participant_id, s.eliminated));

    // winner if present
    if(state.winner_id){
      showWinner(state.winner_id, state.event.finished_at);
    }
  }

  init();

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}${ROOT}/ws/events/${eid}`);
  ws.onopen = ()=>{
    setInterval(()=>{ try{ ws.send('ping'); }catch(e){} }, 15000);
  };
  ws.onmessage = (ev)=>{
    try{
      if(isFinishedClient) return; // ignore any messages after final handled
      const msg = JSON.parse(ev.data);
      if(msg.type === 'stage_start'){
        // msg.time can be used for stage timing
        currentActiveIds = (msg.active||[]).slice();
        currentOutIds = (msg.eliminated||[]).slice();
        renderParticipants(currentActiveIds, currentOutIds);
        buildWheel(currentActiveIds);
        // reset strip position (handled in buildWheel), just ensure no transition
        if(stripEl){ stripEl.style.transition = 'none'; }
        shortCountdown(5, 'Старт через', 'Выбор...');
      } else if(msg.type === 'pick'){
        const pid = msg.participant_id;
        const isElim = !!msg.eliminated;
        const isReverse = !!(state && state.event && state.event.type === 'reverse');
        if(typeof msg.eliminated === 'boolean'){
          addStage(new Date().toISOString(), pid, isElim);
        }
        const endAt = msg.finished_at || null;

        // Decide visual target for the wheel
        let skipSpin = false;
        let visualTargetId = pid;
        if(isReverse){
          if(isElim){
            visualTargetId = pid; // point to eliminated participant
          } else if(msg.final){
            // final winner message in reverse: do not spin again
            skipSpin = true;
          }
        }

        const afterSpin = ()=>{
          // Update lists if someone eliminated in this pick
          if(isElim){
            currentActiveIds = currentActiveIds.filter(id=>id!==pid);
            if(!currentOutIds.includes(pid)) currentOutIds.push(pid);
            renderParticipants(currentActiveIds, currentOutIds);
          }
          if(msg.final){
            const endAt2 = endAt || new Date().toISOString();
            timerEl.textContent = `Событие завершено${endAt2? ' ('+ new Date(endAt2).toLocaleString() +')' : ''}`;
            if(wheel) wheel.style.display = 'none';
            isFinishedClient = true;
            // Determine winner
            let winnerId = pid;
            if(isReverse){
              if(isElim){
                // winner is the only remaining active
                if(currentActiveIds.length === 1) winnerId = currentActiveIds[0];
              } else {
                // if server sends winner as non-eliminated final message, trust it
                winnerId = pid;
              }
            }
            showWinner(winnerId, endAt2);
          }
        };

        if(skipSpin){
          afterSpin();
        } else {
          highlightPick(visualTargetId).then(afterSpin);
        }
      }
    }catch(e){ console.error(e); }
  };
  ws.onclose = ()=>{ timerEl.textContent = 'Соединение закрыто'; };
})();
