import http from "node:http";

const GET_PREFIXES = ["/api/audit/"];
const GET_EXACT = new Set(["/api/state", "/api/events"]);
const POST_EXACT = new Set(["/api/turn", "/api/story/escort-response"]);

export function classifyGameTableRoute(method, pathname) {
  if (method === "GET" && (GET_EXACT.has(pathname) || GET_PREFIXES.some((prefix) => pathname.startsWith(prefix)))) {
    return "proxy";
  }
  if (method === "POST" && POST_EXACT.has(pathname)) return "proxy";
  if (pathname === "/api/frames" || pathname.startsWith("/api/director/")) return "forbidden";
  return "none";
}

export function proxyGameTable(request, response, {
  host = "127.0.0.1",
  port = 17880,
  timeoutMs = 5000,
} = {}) {
  const upstream = http.request({
    host,
    port,
    method: request.method,
    path: request.url,
    headers: {
      "Host": `${host}:${port}`,
      "Accept": request.headers.accept || "*/*",
      "Content-Type": request.headers["content-type"] || undefined,
      "Content-Length": request.headers["content-length"] || undefined,
      "X-GameTable-Token": request.headers["x-gametable-token"] || undefined,
      "Last-Event-ID": request.headers["last-event-id"] || undefined,
      "Cache-Control": "no-cache",
      "Connection": "close",
    },
  }, (upstreamResponse) => {
    const headers = {
      "Content-Type": upstreamResponse.headers["content-type"] || "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    };
    if (upstreamResponse.headers["content-type"]?.startsWith("text/event-stream")) {
      headers["Connection"] = "keep-alive";
    }
    response.writeHead(upstreamResponse.statusCode || 502, headers);
    upstreamResponse.pipe(response);
  });
  upstream.on("error", () => {
    if (!response.headersSent) {
      const body = Buffer.from(JSON.stringify({error: "GameTable backend unavailable"}));
      response.writeHead(502, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": body.length,
        "Cache-Control": "no-store",
      });
      response.end(body);
    } else {
      response.destroy();
    }
  });
  if (request.url?.startsWith("/api/events")) {
    upstream.setTimeout(0);
  } else {
    upstream.setTimeout(timeoutMs, () => upstream.destroy(new Error("backend timeout")));
  }
  request.on("aborted", () => upstream.destroy());
  request.pipe(upstream);
}
