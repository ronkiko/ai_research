const $ = (id) => document.getElementById(id);
const labels = {health:'Здоровье',fatigue:'Усталость',mood:'Настроение',affection:'Симпатия',trust:'Доверие'};
const colors = {health:'#709988',fatigue:'#b39970',mood:'#8c9dc0',affection:'#bf8796',trust:'#668f9d'};
let token='', busy=false, lastHistory='', initialized=false, posting=false, pending=null, lastError='';
function node(tag, text, className) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(className)el.className=className; return el; }
async function request(path, options) { const r=await fetch(path,options); const value=await r.json(); if(!r.ok)throw Error(value.error||'Ошибка соединения'); return value; }
function renderStats(s) {
  $('stats').replaceChildren();
  for (const [key,label] of Object.entries(labels)) {
    const el=node('div',undefined,'stat'); el.style.setProperty('--color',colors[key]);
    const caption=node('div',undefined,'label'); caption.append(node('span',label),node('span',`${Math.round(s.stats[key])} / 100`,'value'));
    const bar=node('div',undefined,'bar'); const fill=node('div',undefined,'fill'); fill.style.width=s.stats[key]+'%';bar.append(fill);el.append(caption,bar);$('stats').append(el);
  }
  const day=1+Math.floor(s.minutes/1440), hour=Math.floor(s.minutes%1440/60), minute=s.minutes%60;
  $('clock').textContent=`День ${day} · ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`;
  // Display labels only: no extra hidden relationship-stage mechanics.
  $('relation').textContent=s.stats.affection>=65&&s.stats.trust>=60?'Близость и доверие':s.stats.affection>=65?'Тянется к тебе, но сомневается':s.stats.trust>=60?'Полагается на тебя':s.stats.trust<20?'Осторожность':'Узнаёте друг друга';
  $('scene-label').textContent=s.stats.fatigue>=70?'Нужна передышка':s.stats.mood<35?'Непростой разговор':s.stats.affection>60?'Знакомый голос':'Тихий день';
  document.querySelector('.stage').classList.toggle('tired',s.stats.fatigue>=70);
  document.querySelector('.stage').classList.toggle('night',hour>=20||hour<7);
  $('mouth').setAttribute('d',s.stats.mood<35?'M189 268Q200 262 211 268':'M188 263Q200 272 212 262');
}
async function showAudit(id) {
  $('audit-content').replaceChildren(node('p','Загрузка…')); $('audit').showModal();
  try { const audit=await request('/api/audit/'+encodeURIComponent(id));const content=$('audit-content');content.replaceChildren();
    for(const role of ['heart','head']) { const item=audit.assessments?.[role];if(!item)continue;content.append(node('h3',role==='heart'?'Сердце':'Голова'),node('p',item.report.summary),node('pre',JSON.stringify(item.report.impacts,null,2))); }
    content.append(node('h3','Расчёт движка'),node('pre',JSON.stringify({calculations:audit.calculations,contract:audit.contract,checks:audit.checks},null,2)));
    if(audit.laboratory?.tools?.length||audit.laboratory?.uncertain)content.append(node('h3','Лабораторные факты'),node('pre',JSON.stringify(audit.laboratory,null,2)));
    if(audit.error)content.append(node('p',audit.error));
  }catch(e){$('audit-content').replaceChildren(node('p',e.message));}
}
function renderHistory(history) {
  const signature=JSON.stringify(history);if(signature===lastHistory)return;lastHistory=signature;
  if(!history.length)return;
  const container=$('history');const nearBottom=container.scrollHeight-container.scrollTop-container.clientHeight<100;container.replaceChildren();
  for(const turn of history){
    const director=node('div',undefined,'bubble director');director.append(node('span','ДИРЕКТОР','speaker'),node('span',turn.event.text));container.append(director);
    const bubble=node('div',undefined,'bubble yuki');bubble.append(node('span','ЮКИ','speaker'));
    if(turn.status==='done'){
      bubble.append(node('span',turn.reply.anchor,'anchor'));
      if(turn.reply.text)bubble.append(node('p',turn.reply.text,'dialogue'));
      if(turn.notice)bubble.append(node('small',turn.notice));
      const meta=node('div',undefined,'turn-meta');meta.append(node('span',`${turn.contract.minutes} мин · ${Object.entries(turn.delta).filter(([,v])=>v!==0).map(([k,v])=>`${labels[k]} ${v>0?'+':''}${v}`).join(' / ')}`));
      const audit=node('button','Сердце и голова','audit-link');audit.type='button';audit.onclick=()=>showAudit(turn.id);meta.append(audit);bubble.append(meta);
    }else if(turn.status==='failed'){bubble.append(node('p',turn.error,'status error'));const audit=node('button','Диагностика','audit-link');audit.type='button';audit.onclick=()=>showAudit(turn.id);bubble.append(audit);}
    else bubble.append(node('p',turn.stage+'…','status'));
    container.append(bubble);
  }
  if(nearBottom||!initialized)container.scrollTop=container.scrollHeight;
}
async function refresh(){
  try{const value=await request('/api/state');token=value.token;busy=value.busy;renderStats(value.state);renderHistory(value.history);$('model').textContent=value.model||'';
    if(!initialized&&value.initial_prompt&&!value.history.length)$('message').value=value.initial_prompt;
    initialized=true;$('send').disabled=busy||posting;$('activity').disabled=busy||posting;$('status').className=lastError?'status error':'status';$('status').textContent=lastError||(busy?'Юки обдумывает ответ. Черновики остаются за сценой.':pending?'Отправка не подтверждена. Повтор использует тот же ход.':'');
  }catch(e){$('status').className='status error';$('status').textContent='Нет связи с GameTable. '+e.message;}
}
$('composer').onsubmit=async(e)=>{e.preventDefault();if(busy||posting)return;
  let text=$('message').value.trim();const activity=$('activity').value;
  if(!text&&activity==='rest')text='Давай сделаем перерыв на полчаса.';
  if(!text&&activity==='sleep')text='На сегодня всё. Пора выспаться.';
  if(!text)return;
  if(!pending||pending.text!==text||pending.activity!==activity)pending={id:crypto.randomUUID(),text,activity};
  posting=true;lastError='';$('send').disabled=true;
  try{await request('/api/turn',{method:'POST',headers:{'Content-Type':'application/json','X-GameTable-Token':token},body:JSON.stringify(pending)});pending=null;lastError='';$('message').value='';}
  catch(error){lastError=error.message;$('status').className='status error';$('status').textContent=lastError;}
  finally{posting=false;await refresh();}
};
$('message').onkeydown=(e)=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}};
$('close-audit').onclick=()=>$('audit').close();
refresh();setInterval(refresh,1800);
