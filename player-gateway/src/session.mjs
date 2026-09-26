import crypto from "node:crypto";

function parseCookies(header = "") {
  const values = new Map();
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    values.set(part.slice(0, index).trim(), part.slice(index + 1).trim());
  }
  return values;
}

function safeEqualHex(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  if (left.length !== right.length || !/^[0-9a-f]+$/i.test(left) || !/^[0-9a-f]+$/i.test(right)) {
    return false;
  }
  return crypto.timingSafeEqual(Buffer.from(left, "hex"), Buffer.from(right, "hex"));
}

export class SessionCapabilities {
  constructor(secret, {secure = false, maxAgeSeconds = 86400} = {}) {
    if (typeof secret !== "string" || secret.length < 32) {
      throw new Error("Player Gateway session secret must be at least 32 characters");
    }
    this.secret = secret;
    this.secure = secure;
    this.maxAgeSeconds = maxAgeSeconds;
    this.cookieName = "pg_session";
  }

  #sign(id) {
    return crypto.createHmac("sha256", this.secret).update(id).digest("hex");
  }

  issue() {
    const id = crypto.randomBytes(24).toString("base64url");
    return `${id}.${this.#sign(id)}`;
  }

  validate(value) {
    if (typeof value !== "string") return null;
    const index = value.lastIndexOf(".");
    if (index <= 0) return null;
    const id = value.slice(0, index);
    const signature = value.slice(index + 1);
    if (!/^[A-Za-z0-9_-]{16,}$/.test(id)) return null;
    return safeEqualHex(signature, this.#sign(id)) ? id : null;
  }

  fromRequest(request) {
    const value = parseCookies(request.headers.cookie || "").get(this.cookieName);
    return this.validate(value);
  }

  cookie(value) {
    const secure = this.secure ? "; Secure" : "";
    return `${this.cookieName}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${this.maxAgeSeconds}${secure}`;
  }
}

export {parseCookies};
