import {bootstrap, fetchAudit, submitTurn} from './api.js';
import {connectEvents} from './events.js';
import {renderView, previewTransition} from './scene-renderer.js';
import {createDialogue} from './dialogue.js';
import {createControls} from './controls.js';

const $ = (id) => document.getElementById(id);
const dialogue = createDialogue(fetchAudit);
let posting = false;
let pending = null;
let lastError = '';
let currentRevision = -1;
let currentView = null;
let syncQueued = false;

function showStatus(text, error = false) {
  $('status').className = error ? 'status error' : 'status';
  $('status').textContent = text || '';
}

const controls = createControls(async ({text, intent_id}) => {
  if (posting || currentView?.busy) return;
  if (!pending || pending.text !== text || pending.intent_id !== intent_id) {
    pending = {id: crypto.randomUUID(), text, intent_id};
  }
  posting = true;
  lastError = '';
  controls.setDisabled(true);
  try {
    await submitTurn(pending);
    pending = null;
    $('message').value = '';
    showStatus('Юки обдумывает ответ…');
  } catch (error) {
    lastError = error.message;
    showStatus(lastError, true);
  } finally {
    posting = false;
    queueSync();
  }
});

async function sync() {
  try {
    const value = await bootstrap();
    currentView = value.view;
    currentRevision = value.view.revision;
    renderView(value.view);
    dialogue.renderHistory(value.history);
    controls.render(value.view.affordances, value.view.busy || posting);
    $('model').textContent = value.model || '';
    if (value.initial_prompt && !value.history.length && !$('message').value) {
      $('message').value = value.initial_prompt;
    }
    if (lastError) showStatus(lastError, true);
    else if (value.view.busy) showStatus((value.view.stage || 'Юки обдумывает ответ') + '…');
    else if (pending) showStatus('Отправка не подтверждена. Повтор использует тот же ход.');
    else showStatus('');
    previewTransition(false);
  } catch (error) {
    showStatus('Нет связи с GameTable. ' + error.message, true);
  }
}

function queueSync() {
  if (syncQueued) return;
  syncQueued = true;
  queueMicrotask(async () => {
    syncQueued = false;
    await sync();
  });
}

function handleEvent(name, data) {
  if (typeof data.revision === 'number' && data.revision < currentRevision) return;
  if (name === 'turn.started' || name === 'turn.stage') {
    showStatus((data.stage || 'Юки обдумывает ответ') + '…');
    controls.setDisabled(true);
    return;
  }
  if (name === 'scene.transition') {
    previewTransition(true);
    showStatus('Смена сцены…');
    return;
  }
  if (name === 'turn.failed') {
    lastError = data.error || 'Ход остановлен';
    queueSync();
    return;
  }
  if (name === 'state.changed' || name === 'turn.completed') queueSync();
}

async function start() {
  await sync();
  connectEvents({onEvent: handleEvent, onOpen: queueSync,
    onError: () => showStatus('Переподключение к событиям…')});
}

$('close-audit').onclick = () => $('audit').close();
start();
