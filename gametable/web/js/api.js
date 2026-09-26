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
  return request('/api/assets');
}

export function fetchTerrain(zone_id) {
  return request('/api/terrain/' + encodeURIComponent(zone_id));
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

