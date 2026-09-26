import assert from "node:assert/strict";
import net from "node:net";
import test from "node:test";

import {HostProtocolClient} from "../src/host-client.mjs";

test("Host client reuses one local TCP connection and speaks Host Protocol v1", async () => {
  let connections = 0;
  const requests = [];
  const server = net.createServer((socket) => {
    connections += 1;
    let buffer = "";
    socket.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      while (buffer.includes("\n")) {
        const index = buffer.indexOf("\n");
        const raw = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        const request = JSON.parse(raw);
        requests.push(request);
        socket.write(JSON.stringify({
          version: 1,
          type: request.type,
          echoed_client_id: request.client_id,
        }) + "\n");
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const client = new HostProtocolClient({
    clientId: "gateway-test",
    host: "127.0.0.1",
    port: server.address().port,
    timeoutMs: 500,
  });
  try {
    const health = await client.request("health");
    const describe = await client.request("describe");
    assert.equal(health.echoed_client_id, "gateway-test");
    assert.equal(describe.type, "describe");
    assert.equal(connections, 1);
    assert.deepEqual(requests.map((value) => value.type), ["health", "describe"]);
  } finally {
    client.close();
    await new Promise((resolve) => server.close(resolve));
  }
});
