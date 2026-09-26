import {
  bootstrap, fetchAssetCatalog, fetchAudit, fetchTerrain,
  respondEscort, submitTurn,
} from './api.js';
import {connectEvents} from './events.js';
import {
  connectFrames, gatewayAcquire, gatewayInput, gatewayRelease,
} from './frames.js';
import {
  acceptFrame, markFrameStale, setAssetCatalog, setTerrain,
} from './frame-renderer.js';
import {renderView} from './scene-renderer.js';
import {createDialogue} from './dialogue.js';
import {createControls} from './controls.js';

const $=(id)=>document.getElementById(id);
const dialogue=createDialogue(fetchAudit);
const sourceId=sessionStorage.getItem('director-source')||crypto.randomUUID();
sessionStorage.setItem('director-source',sourceId);
let posting=false,pending=null,lastError='',currentRevision=-1,currentView=null,currentStory=null;
let syncQueued=false,assetsReady=false,directorControl=false,heldDirection=0,directorBusy=false;
let currentTerrainRevision=null,gatewayError='';

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
  const retryableEscort=intro.response==='accept'&&
    ['blocked','failed','reconciling'].includes(escort.status);
  const offerReady=
    (intro.phase==='escort_offer_published'&&!intro.response)||retryableEscort;
  choices.hidden=!offerReady;
  $('escort-accept').textContent=retryableEscort?'Продолжить сопровождение':'Провести Юки';
  $('escort-decline').hidden=retryableEscort;
  $('escort-clarify').hidden=retryableEscort;
  $('story-status').textContent=
    escort.status==='escort_active'
      ? 'Сопровождение · '+(escort.phase||'active')
      : retryableEscort
        ? 'Сопровождение прервано · можно продолжить'
        : story?.day_phase==='waking'
          ? 'Начинается новый день…'
          : intro.phase==='escort_offer_pending'
            ? 'Юки собирается кое-что попросить…'
            : '';
  $('vn-dialogue').hidden=story?.presentation_mode==='world_control';
  $('stage').dataset.presentationMode=story?.presentation_mode||'vn_dialogue';
  $('world-control-hint').hidden=story?.presentation_mode!=='world_control';
  if(story?.presentation_mode!=='world_control'&&directorControl){
    releaseDirector().catch(()=>{});
  }
}

async function sync(){
  try{
    await ensureAssets(); const value=await bootstrap();
    currentView=value.view;currentRevision=value.view.revision;
    renderView(value.view,value.story);renderStory(value.story);
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
  if(directorControl)return true;
  const value=await gatewayAcquire();
  directorControl=value.control?.owned_by_you===true;
  if(!directorControl)throw Error('Player Gateway не выдал управление Директором');
  return true;
}

async function sendDirection(direction){
  if(currentStory?.presentation_mode!=='world_control')return;
  if(direction===heldDirection)return;
  heldDirection=direction;
  try{
    await acquireDirector();
    await gatewayInput(direction);
  }catch(error){
    directorControl=false;heldDirection=0;showStatus(error.message,true);
  }
}

async function releaseDirector(){
  const hadControl=directorControl;
  heldDirection=0;directorControl=false;
  if(!hadControl)return;
  try{await gatewayRelease();}catch(_){}
}

async function handleFrame(frame,reset){
  try{
    if(currentTerrainRevision!==frame.terrain_revision){
      const terrain=await fetchTerrain(frame.zone_id);
      setTerrain(terrain);
      currentTerrainRevision=terrain.terrain_revision;
    }
    if(acceptFrame(frame,{reset}))markFrameStale(false);
  }catch(error){
    markFrameStale(true);
    showStatus('Ошибка Player Gateway. '+error.message,true);
  }
}

window.addEventListener('keydown',(event)=>{
  if(typingTarget(event.target)||currentStory?.presentation_mode!=='world_control')return;
  if(event.repeat)return;
  if(event.key==='ArrowLeft'){event.preventDefault();sendDirection(-1);}
  else if(event.key==='ArrowRight'){event.preventDefault();sendDirection(1);}
});
window.addEventListener('keyup',(event)=>{
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    if(typingTarget(event.target)){
      // Focus may have moved into the composer while an arrow was held.
      // Release the old manual lease without stealing cursor navigation.
      if(heldDirection!==0||directorControl)releaseDirector();
      return;
    }
    event.preventDefault();
    sendDirection(0);
  }
});
document.addEventListener('focusin',(event)=>{
  if(typingTarget(event.target)&&(heldDirection!==0||directorControl)){
    releaseDirector();
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
  connectFrames({
    onFrame:(frame,reset)=>{void handleFrame(frame,reset);},
    onOpen:()=>markFrameStale(false),
    onError:()=>{directorControl=false;markFrameStale(true);},
    onStatus:(value)=>{
      if(value?.control?.owned_by_you===false)directorControl=false;
      if(value?.error){
        gatewayError=value.error;
        if(value?.host_freshness?.stale)markFrameStale(true);
        showStatus('Player Gateway: '+gatewayError,true);
      }else if(gatewayError){
        const previous='Player Gateway: '+gatewayError;
        gatewayError='';
        if(value?.host_freshness?.stale===false)markFrameStale(false);
        if($('status').textContent===previous)queueSync();
      }
    },
  });
}
$('close-audit').onclick=()=>$('audit').close();start();
