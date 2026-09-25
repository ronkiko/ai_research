export function connectFrames({onFrame,onOpen,onError}) {
  const source = new EventSource('/api/frames');
  source.addEventListener('frame.latest', (message) => {
    let data={}; try { data=JSON.parse(message.data); } catch (_) { return; }
    onFrame(data.frame, Boolean(data.reset), message.lastEventId);
  });
  source.onopen=()=>onOpen?.(); source.onerror=()=>onError?.(); return source;
}
