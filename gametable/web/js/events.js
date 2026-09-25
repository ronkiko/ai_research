export const EVENT_TYPES = [
  'turn.started',
  'turn.stage',
  'scene.transition',
  'state.changed',
  'turn.completed',
  'turn.failed',
  'action.updated',
];

export function connectEvents({onEvent, onOpen, onError}) {
  const source = new EventSource('/api/events');
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
