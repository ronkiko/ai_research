const $ = (id) => document.getElementById(id);
let assets = {}, terrain = null, current = null, pending = null, raf = 0;
export function setAssetCatalog(value) { assets = value?.assets || {}; }
function asset(id) { return assets[id] || {glyph:'?',label:id||'unknown',fill:'#6f7b78',stroke:'#34413e'}; }
function schedule(frame) {
  pending = frame;
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; if (!pending) return; current = pending; pending = null; draw(); });
}
export function setTerrain(value) { terrain = value || null; if (current) schedule(current); }
export function acceptFrame(frame, {reset=false} = {}) {
  if (!frame) return false;
  if (current) {
    if (frame.source_world_epoch === current.source_world_epoch) {
      if (frame.source_world_revision < current.source_world_revision) return false;
      if (frame.source_world_revision === current.source_world_revision &&
          frame.source_world_tick <= current.source_world_tick) return false;
    } else if (!reset) return false;
  }
  if (terrain && frame.terrain_revision !== terrain.terrain_revision) return false;
  schedule(frame); return true;
}
export function renderGraphics(value) {
  if (!value) return;
  setTerrain(value.terrain);
  acceptFrame(value.frame, {reset: !current || value.frame.source_world_epoch !== current.source_world_epoch});
  markFrameStale(false);
}
export function markFrameStale(stale) {
  $('frame-stale').hidden = !stale;
  $('stage').dataset.frameState = stale ? 'stale' : 'current';
}
function draw() {
  const frame = current;
  if (!frame || !terrain) return;
  const canvas = $('world-canvas');
  const width = Math.max(1, Math.round(canvas.clientWidth || 960));
  const height = Math.max(1, Math.round(canvas.clientHeight || 560));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext('2d'), background = asset(terrain.background_asset);
  ctx.fillStyle = background.fill || '#d9e2df'; ctx.fillRect(0,0,width,height);
  const left=42, right=width-42, groundY=Math.round(height*.68), span=Math.max(1,right-left);
  ctx.strokeStyle=background.ground||'#6d7b78'; ctx.lineWidth=5;
  ctx.beginPath(); ctx.moveTo(left,groundY); ctx.lineTo(right,groundY); ctx.stroke();
  const toX=(worldX)=>left+((worldX-frame.camera.world_min)/(frame.camera.world_max-frame.camera.world_min))*span;
  for (const prop of frame.props) {
    const spec=asset(prop.visual_asset), x=toX(prop.x);
    ctx.fillStyle=spec.fill||'#687b76'; ctx.font='700 22px system-ui'; ctx.textAlign='center';
    ctx.fillText(spec.glyph||'?',x,groundY-18); ctx.font='11px system-ui';
    ctx.fillText(spec.label||prop.object_id,x,groundY+28);
  }
  const byCell=new Map();
  for (const entity of frame.entities) { const group=byCell.get(entity.display_cell)||[]; group.push(entity); byCell.set(entity.display_cell,group); }
  for (const group of byCell.values()) group.forEach((entity,index)=>{
    const spec=asset(entity.visual_asset);
    const x=left+entity.screen_x*span+(index-(group.length-1)/2)*18;
    const seated=entity.animation_state==='seated_working', y=seated?groundY-22:groundY-38;
    ctx.fillStyle=spec.fill||'#6f7b78'; ctx.strokeStyle=spec.stroke||'#34413e'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(x,y,seated?15:18,0,Math.PI*2); ctx.fill(); ctx.stroke();
    ctx.fillStyle='#fff'; ctx.font='700 15px system-ui'; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(spec.glyph||'•',x,y); ctx.textBaseline='alphabetic'; ctx.fillStyle=spec.stroke||'#28343e';
    ctx.font='11px system-ui'; ctx.fillText(spec.label||entity.entity_id,x,y-28);
  });
  $('location-label').textContent=frame.zone_id;
  const authority=frame.freshness?.authoritative?'WORLD':'COMPAT';
  $('frame-meta').textContent=authority+' · tick '+frame.source_world_tick+' · rev '+frame.source_world_revision;
}
