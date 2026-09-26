import net from "node:net";

const HOST_PROTOCOL_VERSION = 1;
const DEFAULT_MAX_LINE_BYTES = 1024 * 1024;

export class HostClientError extends Error {}

export class HostProtocolClient {
  constructor({
    clientId,
    host = "127.0.0.1",
    port = 17701,
    timeoutMs = 1000,
    maxLineBytes = DEFAULT_MAX_LINE_BYTES,
  }) {
    if (!clientId || typeof clientId !== "string") throw new Error("clientId is required");
    this.clientId = clientId;
    this.host = host;
    this.port = port;
    this.timeoutMs = timeoutMs;
    this.maxLineBytes = maxLineBytes;
    this.socket = null;
    this.buffer = "";
    this.waiter = null;
    this.connecting = null;
    this.queue = Promise.resolve();
  }

  async connect() {
    if (this.socket && !this.socket.destroyed) return;
    if (this.connecting) return this.connecting;
    this.connecting = new Promise((resolve, reject) => {
      const socket = net.createConnection({host: this.host, port: this.port});
      const fail = (error) => {
        socket.destroy();
        reject(new HostClientError(`cannot connect to GameClient Host: ${error.message}`));
      };
      socket.setTimeout(this.timeoutMs);
      socket.once("error", fail);
      socket.once("connect", () => {
        socket.off("error", fail);
        socket.setTimeout(0);
        this.socket = socket;
        this.buffer = "";
        socket.on("data", (chunk) => this.#onData(chunk));
        socket.on("error", (error) => this.#drop(error));
        socket.on("close", () => this.#drop(new Error("Host connection closed")));
        resolve();
      });
    }).finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  request(type, fields = {}) {
    const run = this.queue.then(() => this.#request(type, fields));
    this.queue = run.catch(() => {});
    return run;
  }

  async #request(type, fields) {
    await this.connect();
    if (!this.socket || this.socket.destroyed) {
      throw new HostClientError("GameClient Host connection is unavailable");
    }
    if (this.waiter) throw new HostClientError("Host request overlap");
    const payload = JSON.stringify({
      version: HOST_PROTOCOL_VERSION,
      type,
      client_id: this.clientId,
      ...fields,
    });
    if (Buffer.byteLength(payload) > this.maxLineBytes) {
      throw new HostClientError("Host request is too large");
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#drop(new Error("Host request timed out"));
      }, this.timeoutMs);
      this.waiter = {resolve, reject, timer};
      this.socket.write(payload + "\n", (error) => {
        if (error) this.#drop(error);
      });
    });
  }

  #onData(chunk) {
    this.buffer += chunk.toString("utf8");
    if (Buffer.byteLength(this.buffer) > this.maxLineBytes) {
      this.#drop(new Error("Host response is too large"));
      return;
    }
    const newline = this.buffer.indexOf("\n");
    if (newline < 0) return;
    const raw = this.buffer.slice(0, newline);
    this.buffer = this.buffer.slice(newline + 1);
    const waiter = this.waiter;
    this.waiter = null;
    if (!waiter) {
      this.#drop(new Error("unsolicited Host response"));
      return;
    }
    clearTimeout(waiter.timer);
    try {
      const value = JSON.parse(raw);
      if (!value || value.version !== HOST_PROTOCOL_VERSION || typeof value.type !== "string") {
        throw new Error("invalid Host response");
      }
      if (value.type === "error") {
        waiter.reject(new HostClientError(String(value.error || "Host rejected request")));
      } else {
        waiter.resolve(value);
      }
    } catch (error) {
      waiter.reject(new HostClientError(`invalid Host response: ${error.message}`));
    }
  }

  #drop(error) {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
    }
    this.buffer = "";
    if (this.waiter) {
      const waiter = this.waiter;
      this.waiter = null;
      clearTimeout(waiter.timer);
      waiter.reject(new HostClientError(error.message || String(error)));
    }
  }

  close() {
    this.#drop(new Error("Host client closed"));
  }
}
