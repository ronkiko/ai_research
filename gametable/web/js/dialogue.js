import {statLabels} from './scene-renderer.js';

const $ = (id) => document.getElementById(id);
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

export function createDialogue(fetchAudit) {
  let lastHistory = '';
  let initialized = false;

  async function showAudit(id) {
    $('audit-content').replaceChildren(node('p', 'Загрузка…'));
    $('audit').showModal();
    try {
      const audit = await fetchAudit(id);
      const content = $('audit-content');
      content.replaceChildren();
      for (const role of ['heart', 'head']) {
        const item = audit.assessments?.[role];
        if (!item) continue;
        content.append(node('h3', role === 'heart' ? 'Сердце' : 'Голова'),
          node('p', item.report.summary),
          node('pre', JSON.stringify(item.report.impacts, null, 2)));
      }
      content.append(node('h3', 'Расчёт движка'),
        node('pre', JSON.stringify({calculations: audit.calculations,
          contract: audit.contract, checks: audit.checks}, null, 2)));
      if (audit.external?.results?.length) {
        content.append(node('h3', 'Внешние эффекты'),
          node('pre', JSON.stringify(audit.external, null, 2)));
      }
      if (audit.error) content.append(node('p', audit.error));
    } catch (error) {
      $('audit-content').replaceChildren(node('p', error.message));
    }
  }

  function renderHistory(history) {
    const signature = JSON.stringify(history);
    if (signature === lastHistory) return;
    lastHistory = signature;
    const container = $('history');
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    if (!history.length) return;
    container.replaceChildren();
    for (const turn of history) {
      const director = node('div', undefined, 'bubble director');
      director.append(node('span', 'ДИРЕКТОР', 'speaker'), node('span', turn.event.text));
      container.append(director);
      const bubble = node('div', undefined, 'bubble yuki');
      bubble.append(node('span', 'ЮКИ', 'speaker'));
      if (turn.status === 'done') {
        bubble.append(node('span', turn.reply.anchor, 'anchor'));
        if (turn.reply.text) bubble.append(node('p', turn.reply.text, 'dialogue'));
        if (turn.notice) bubble.append(node('small', turn.notice));
        const changes = Object.entries(turn.delta).filter(([, value]) => value !== 0)
          .map(([key, value]) => statLabels[key] + ' ' + (value > 0 ? '+' : '') + value).join(' / ');
        const meta = node('div', undefined, 'turn-meta');
        meta.append(node('span', turn.contract.minutes + ' мин · ' + changes));
        const audit = node('button', 'Сердце и голова', 'audit-link');
        audit.type = 'button';
        audit.onclick = () => showAudit(turn.id);
        meta.append(audit);
        bubble.append(meta);
      } else if (turn.status === 'failed') {
        bubble.append(node('p', turn.error, 'status error'));
        const audit = node('button', 'Диагностика', 'audit-link');
        audit.type = 'button';
        audit.onclick = () => showAudit(turn.id);
        bubble.append(audit);
      } else {
        bubble.append(node('p', turn.stage + '…', 'status'));
      }
      container.append(bubble);
    }
    if (nearBottom || !initialized) container.scrollTop = container.scrollHeight;
    initialized = true;
  }

  function renderPublished(messages) {
    const panel = $('vn-dialogue');
    if (!messages?.length) {
      $('vn-speaker').textContent = 'ЮКИ';
      $('vn-text').textContent = 'Лаборатория ещё тиха. Поздоровайся — ваша история начинается здесь.';
      panel.dataset.messageId = '';
      return;
    }
    const message = messages[messages.length - 1];
    if (panel.dataset.messageId === message.message_id) return;
    panel.dataset.messageId = message.message_id;
    $('vn-speaker').textContent = message.speaker_id === 'director' ? 'ДИРЕКТОР' : 'ЮКИ';
    $('vn-text').textContent = message.text;
  }

  return {renderHistory, renderPublished, showAudit};
}
