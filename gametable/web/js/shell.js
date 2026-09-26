import {
  bootstrap, directorAcquire, directorInput, directorRelease,
  fetchAssetCatalog, fetchAudit, respondEscort, submitTurn,
} from './api.js';
import {connectEvents} from './events.js';
import {connectFrames} from './frames.js';
import {acceptFrame, markFrameStale, renderGraphics, setAssetCatalog} from './frame-renderer.js';
import {renderView} from './scene-renderer.js';
import {createDialogue} from './dialogue.js';
import {createControls} from './controls.js';

const $=(id)=>document.getElementById(id);
const dialogue=createDialogue(fetchAudit);
const sourceId=sessionStorage.getItem('director-source')||crypto.randomUUID();
sessionStorage.setItem('director-source',sourceId);
let posting=false,pending=null,lastError='',currentRevision=-1,currentView=null,currentStory=null;
let syncQueued=false,assetsReady=false,directorLease=null,heldDirection=0,directorBusy=false;

function showStatus(text,error=false){$('status').className=error?'status error':'status';$('status').textContent=text||'';}

const controls=createControls(async({text,intent_id})=>{
  if(posting||currentView?.busy)return;
  if(!pending||pending.text!==text||pending.intent_id!==intent_id)pending={id:crypto.randomUUID(),text,intent_id};
  posting=true;lastError='';controls.setDisabled(true);
  try{await submitTurn(pending);pending=null;$('message').value='';showStatus('Юки обдумывает ответ…');}
  catch(error){lastError=error.message;showStatus(lastError,true);}
  finally{posting=false;queueSync();}
});

async function ensureAssets(){if(assetsReady)return;setAssetCatalog(await fetchAssetCatalog());assetsReady=true;}

function renderStory(story){
  currentStory=story;
  const intro=story?.intro||{};
  const escort=story?.escort||{};
  const choices=$('escort-choices');
  const offerReady=intro.phase==='escort_offer_published'&&!intro.response;
  choices.hidden=!offerReady;
  $('story-status').textContent=
    escort.status==='escort_active'
      ? 'Сопровождение · '+(escort.phase||'active')
      : story?.day_phase==='waking'
        ? 'Начинается новый день…'
        : intro.phase==='escort_offer_pending'
          ? 'Юки собирается кое-что попросить…'
          : '';
  $('vn-dialogue').hidden=story?.presentation_mode==='world_control';
  $('stage').dataset.presentationMode=story?.presentation_mode||'vn_dialogue';
  $('world-control-hint').hidden=story?.presentation_mode!=='world_control';
  if(story?.presentation_mode!=='world_control'&&directorLease){
    releaseDirector().catch(()=>{});
  }
}

async function sync(){
  try{
    await ensureAssets(); const value=await bootstrap();
    currentView=value.view;currentRevision=value.view.revision;
    renderView(value.view,value.story);renderGraphics(value.graphics);renderStory(value.story);
    dialogue.renderPublished(value.dialogue);dialogue.renderHistory(value.history);
    controls.render(value.view.affordances,value.view.busy||posting);$('model').textContent=value.model||'';
    if(value.initial_prompt&&!value.history.length&&!$('message').value)$('message').value=value.initial_prompt;
    if(lastError)showStatus(lastError,true);
    else if(value.view.busy)showStatus((value.view.stage||'Юки обдумывает ответ')+'…');
    else if(pending)showStatus('Отправка не подтверждена. Повтор использует тот же ход.');
    else showStatus('');
  }catch(error){markFrameStale(true);showStatus('Нет связи с GameTable. '+error.message,true);}
}
function queueSync(){if(syncQueued)return;syncQueued=true;queueMicrotask(async()=>{syncQueued=false;await sync();});}

function handleEvent(name,data){
  if(typeof data.revision==='number'&&data.revision<currentRevision)return;
  if(name==='turn.started'){showStatus((data.stage||'Юки обдумывает ответ')+'…');controls.setDisabled(true);queueSync();return;}
  if(name==='turn.stage'){showStatus((data.stage||'Юки обдумывает ответ')+'…');controls.setDisabled(true);return;}
  if(name==='scene.transition'){showStatus('Переход подтверждается…');return;}
  if(name==='turn.failed'){lastError=data.error||'Ход остановлен';queueSync();return;}
  if(
    name==='state.changed'||name==='turn.completed'||name==='action.updated'||
    name.startsWith('story.')
  )queueSync();
}

async function storyResponse(response){
  const intro=currentStory?.intro;
  if(!intro?.offer_id||directorBusy)return;
  directorBusy=true;
  try{
    let text=$('message').value.trim();
    if(!text){
      text=response==='accept'?'Да, я провожу тебя.':
        response==='decline'?'Нет, сейчас не смогу.':'Уточни, пожалуйста, куда именно тебя проводить?';
    }
    await respondEscort(intro.offer_id,response,text);
    $('message').value='';
    await sync();
  }catch(error){showStatus(error.message,true);}
  finally{directorBusy=false;}
}

$('escort-accept').onclick=()=>storyResponse('accept');
$('escort-decline').onclick=()=>storyResponse('decline');
$('escort-clarify').onclick=()=>storyResponse('clarify');

function typingTarget(target){
  if(!target)return false;
  return target.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target.tagName);
}

async function acquireDirector(){
  if(directorLease)return directorLease;
  const value=await directorAcquire(sourceId,true);
  directorLease=value.lease?.lease_id||null;
  if(!directorLease)throw Error('Director control lease was not issued');
  return directorLease;
}

async function sendDirection(direction,{force=false}={}){
  if(currentStory?.presentation_mode!=='world_control')return;
  if(direction===heldDirection&&!force)return;
  heldDirection=direction;
  try{
    const lease=await acquireDirector();
    await directorInput(sourceId,lease,direction);
  }catch(error){
    directorLease=null;heldDirection=0;showStatus(error.message,true);
  }
}

async function releaseDirector(){
  const lease=directorLease;
  heldDirection=0;directorLease=null;
  if(!lease)return;
  try{await directorInput(sourceId,lease,0);}catch(_){}
  try{await directorRelease(sourceId,lease);}catch(_){}
}

window.addEventListener('keydown',(event)=>{
  if(typingTarget(event.target)||currentStory?.presentation_mode!=='world_control')return;
  if(event.key==='ArrowLeft'){event.preventDefault();sendDirection(-1,{force:event.repeat});}
  else if(event.key==='ArrowRight'){event.preventDefault();sendDirection(1,{force:event.repeat});}
});
window.addEventListener('keyup',(event)=>{
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    if(!typingTarget(event.target)){event.preventDefault();sendDirection(0);}
  }
});
window.addEventListener('blur',()=>{releaseDirector();});
document.addEventListener('visibilitychange',()=>{
  if(document.hidden)releaseDirector();
});
window.addEventListener('pagehide',()=>{releaseDirector();});

async function start(){
  await sync();
  connectEvents({
    onEvent:handleEvent,
    onOpen:queueSync,
    onError:()=>showStatus('Переподключение к событиям…'),
    sourceId,
  });
  connectFrames({onFrame:(frame,reset)=>{if(acceptFrame(frame,{reset}))markFrameStale(false);},
    onOpen:()=>markFrameStale(false),onError:()=>markFrameStale(true)});
}
$('close-audit').onclick=()=>$('audit').close();start();
