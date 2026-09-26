let token = '';

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Ошибка соединения');
  return value;
}

export async function bootstrap() {
  const value = await request('/api/state');
  token = value.token;
  return value;
}

export function submitTurn(turn) {
  return request('/api/turn', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-GameTable-Token': token},
    body: JSON.stringify(turn),
  });
}

export function fetchAudit(id) {
  return request('/api/audit/' + encodeURIComponent(id));
}

export function fetchAssetCatalog() {
  return request('/api/graphics/assets');
}

function postJson(path, body) {
  return request(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-GameTable-Token': token},
    body: JSON.stringify(body),
  });
}

export function respondEscort(offer_id, response, text) {
  return postJson('/api/story/escort-response', {offer_id, response, text});
}

export function directorAcquire(source_id, transfer = false) {
  return postJson('/api/director/control/acquire', {source_id, transfer});
}

export function directorInput(source_id, lease_id, move_x) {
  return postJson('/api/director/input', {source_id, lease_id, move_x});
}

export function directorRelease(source_id, lease_id) {
  return postJson('/api/director/control/release', {source_id, lease_id});
}
