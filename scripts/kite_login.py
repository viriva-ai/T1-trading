#!/usr/bin/env python3
"""Daily Kite login (Zerodha expires the access token at ~06:00 IST every day).

Option A (paste):   python scripts/kite_login.py
   Opens nothing; prints the login URL. Log in on any browser; you land on http://127.0.0.1:5000/callback?...;
   the page will not load - copy the whole address bar and paste it at the prompt.
Option B (tunnel):  on your laptop: ssh -L 5000:127.0.0.1:5000 ubuntu@<vm-ip>   then on the VM:
                    python scripts/kite_login.py --listen
   After login, the redirect reaches the VM through the tunnel and the token is saved automatically.
Option C:           python scripts/kite_login.py --url "<redirect url or request_token>"
Check only:         python scripts/kite_login.py --status
The token is stored chmod 600 at paths.token_file; it is never printed.
"""
import http.server
import threading

from _common import load_cfg, log, parser

from t1.data import kite

ap = parser(__doc__)
ap.add_argument("--url", default=None)
ap.add_argument("--listen", action="store_true")
ap.add_argument("--port", type=int, default=5000)
ap.add_argument("--status", action="store_true")
a = ap.parse_args()
cfg = load_cfg(a)
if a.status:
    log(f"Kite token: {kite.token_status(cfg.token_file)}")
    raise SystemExit(0)
print("Log in here (redirect URL in the Kite app must be http://127.0.0.1:%d/callback):\n  %s\n" % (a.port, kite.login_url()))
if a.url:
    text = a.url
elif a.listen:
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "request_token=" in self.path:
                got["url"] = "http://127.0.0.1" + self.path
                body = b"T1: login received. You can close this tab."
            else:
                body = b"waiting for Kite redirect"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *x):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", a.port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log(f"listening on 127.0.0.1:{a.port} for the redirect (Ctrl-C to abort)")
    import time
    while "url" not in got:
        time.sleep(0.5)
    srv.shutdown()
    text = got["url"]
else:
    text = input("Paste the full redirect URL (or the request_token): ")
info = kite.exchange_request_token(kite.request_token_from(text), cfg.token_file)
log(f"Kite login OK for {info['user_name']}; token saved to {cfg.token_file} (chmod 600). Status: {kite.token_status(cfg.token_file)}")
