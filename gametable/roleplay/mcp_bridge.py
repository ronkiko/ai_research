"""Private authenticated loopback bridge, never proxied to the player browser."""
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MCPBridge:
    def __init__(self, actions):
        self.secret = secrets.token_urlsafe(32)
        secret = self.secret

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + secret):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 8192:
                        raise ValueError('invalid payload')
                    body = json.loads(self.rfile.read(length))
                    service, operation = body.get('service'), body.get('operation')
                    if service not in {'navigation', 'learning'}:
                        raise ValueError('invalid service')
                    if operation == 'execute_approved' and set(body) == {'service', 'operation', 'approval_id'}:
                        result = actions.execute_approved(body['approval_id'], service)
                    elif operation in {'describe', 'skills', 'observe'} and set(body) == {'service', 'operation'}:
                        allowed = {'describe', 'skills'} if service == 'learning' else {'describe', 'observe'}
                        if operation not in allowed:
                            raise ValueError('invalid read')
                        obj = actions.workbench.service() if service == 'learning' else actions._service()
                        result = getattr(obj, operation)()
                    else:
                        raise ValueError('invalid operation')
                    payload = {'ok': True, 'result': result}
                except Exception as exc:
                    # No implementation paths or credentials reach models.
                    payload = {'ok': False, 'error': {'code': getattr(exc, 'code', 'action_rejected')}}
                raw = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/call'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
