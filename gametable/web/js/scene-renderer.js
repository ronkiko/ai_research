export const statLabels = {
  health: 'Здоровье',
  fatigue: 'Усталость',
  mood: 'Настроение',
  affection: 'Симпатия',
  trust: 'Доверие',
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
    bar.max = 100;
    bar.value = stats[key];
    bar.setAttribute('aria-label', label);
    row.append(caption, bar);
    container.append(row);
  }
}

export function renderView(view) {
  renderStats(view.stats);
  $('clock').textContent = 'День ' + view.time.day + ' · ' +
    String(view.time.hour).padStart(2, '0') + ':' + String(view.time.minute).padStart(2, '0');
  $('location-label').textContent = view.scene.label;
  $('scene-label').textContent = view.scene_label;
  $('relation').textContent = view.relationship_label;
  const stage = $('stage');
  stage.dataset.background = view.scene.background;
  stage.dataset.expression = view.scene.character.expression;
  stage.dataset.slot = view.scene.character.slot;
  stage.dataset.tired = String(Boolean(view.scene.character.tired));
  stage.dataset.night = String(Boolean(view.time.night));
  stage.dataset.transition = view.scene.transition || '';
  for (const pose of stage.querySelectorAll('[data-pose]')) {
    pose.hidden = pose.dataset.pose !== view.scene.character.pose;
  }
  const props = new Set(view.scene.props);
  for (const prop of stage.querySelectorAll('[data-prop]')) {
    prop.hidden = !props.has(prop.dataset.prop);
  }
}

export function previewTransition(active) {
  $('stage').dataset.transition = active ? 'moving' : '';
}
