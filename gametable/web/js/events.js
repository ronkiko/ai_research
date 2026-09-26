export const EVENT_TYPES = [
  'turn.started',
  'turn.stage',
  'scene.transition',
  'state.changed',
  'turn.completed',
  'turn.failed',
  'action.updated',
  'story.intro_started',
  'story.offer_due',
  'story.offer_published',
  'story.escort_declined',
  'story.escort_clarify',
  'story.escort_updated',
  'story.day_started',
];

export function connectEvents({onEvent, onOpen, onError, sourceId = ''}) {
  const suffix = sourceId ? '?source_id=' + encodeURIComponent(sourceId) : '';
  const source = new EventSource('/api/events' + suffix);
  for (const name of EVENT_TYPES) {
    source.addEventListener(name, (message) => {
      let data = {};
      try { data = JSON.parse(message.data); } catch (_) { return; }
      onEvent(name, data, message.lastEventId);
    });
  }
  source.onopen = () => onOpen?.();
  source.onerror = () => onError?.();
  return source;
}
