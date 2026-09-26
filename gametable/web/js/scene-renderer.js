export const statLabels = {
  health: 'Здоровье', fatigue: 'Усталость', mood: 'Настроение',
  affection: 'Симпатия', trust: 'Доверие',
};
const $ = (id) => document.getElementById(id);
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function renderStats(stats) {
  const container = $('stats');
  container.replaceChildren();
  for (const [key, label] of Object.entries(statLabels)) {
    const row = node('div', undefined, 'stat stat-' + key);
    const caption = node('div', undefined, 'label');
    caption.append(node('span', label), node('span', Math.round(stats[key]) + ' / 100', 'value'));
    const bar = document.createElement('progress');
    bar.max = 100; bar.value = stats[key]; bar.setAttribute('aria-label', label);
    row.append(caption, bar); container.append(row);
  }
}
export function renderView(view, story = null) {
  renderStats(view.stats);
  $('clock').textContent = 'День ' + (story?.day_id || view.time.day) + ' · ' +
    String(view.time.hour).padStart(2, '0') + ':' + String(view.time.minute).padStart(2, '0');
  $('relation').textContent = view.relationship_label;
  $('stage').dataset.presentationMode = view.presentation_mode;
  $('world-control-hint').hidden = view.presentation_mode !== 'world_control';
}
