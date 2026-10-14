import json
import secrets
import time
import uuid
from datetime import datetime, timezone

import jwt
from flask import Flask, Response, redirect, request
from werkzeug.serving import make_server

try:
    import pyotp
except ImportError:
    pyotp = None

JWT_SECRET = "swarm-onyx-test-secret-do-not-use-in-prod"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _j(data, status=200):
    return Response(json.dumps(data, separators=(",", ":"), default=str),
                    status=status, mimetype="application/json")


def _make_token(username, role="user"):
    now = int(time.time())
    return jwt.encode({"sub": username, "role": role, "iat": now,
                       "exp": now + 3600, "iss": "onyx-test"},
                      JWT_SECRET, algorithm="HS256")


def _read_token():
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("access_token")


USERS = [
    {"id": 1, "username": "admin", "password": "admin123", "role": "admin",
     "email": "admin@onyx.local", "api_key": "sk_live_admin_" + secrets.token_hex(4),
     "requires_2fa": False},
    {"id": 2, "username": "alice", "password": "alice123", "role": "user",
     "email": "alice@onyx.local", "api_key": "sk_live_alice_" + secrets.token_hex(4),
     "requires_2fa": False},
    {"id": 3, "username": "bob", "password": "bob123", "role": "user",
     "email": "bob@onyx.local", "api_key": "sk_live_bob_" + secrets.token_hex(4),
     "requires_2fa": False},
    {"id": 4, "username": "carol", "password": "carol123", "role": "user",
     "email": "carol@onyx.local", "api_key": "sk_live_carol_" + secrets.token_hex(4),
     "requires_2fa": False},
    {"id": 5, "username": "admin_2fa", "password": "admin123", "role": "admin",
     "email": "admin2fa@onyx.local", "api_key": "sk_live_admin2fa_" + secrets.token_hex(4),
     "requires_2fa": True},
]

_COMMENTS = [
    {"id": 1, "author": "admin", "body": "Welcome to the O.N.Y.X. test site.", "user_id": 1},
    {"id": 2, "author": "alice", "body": "Please test the new search endpoint.", "user_id": 2},
    {"id": 3, "author": "bob", "body": "I saw something weird in /files", "user_id": 3},
]

_NOTES = {
    1: {"id": 1, "user_id": 1, "title": "Deployment creds",
        "body": "prod-db.internal:5432  admin:hunter2"},
    2: {"id": 2, "user_id": 1, "title": "TODO",
        "body": "Rotate the JWT secret quarterly"},
    3: {"id": 3, "user_id": 2, "title": "Alice",
        "body": "Remember to bring snacks on Friday"},
}

_ISSUED_TOKENS = set()
_MFA_CHALLENGES = {}


def _build_app():
    app = Flask("onyx_test_server")
    app.config["JSON_SORT_KEYS"] = False

    def _user_by_name(name):
        for u in USERS:
            if u["username"] == name:
                return u
        return None

    def _issue_token(user):
        token = _make_token(user["username"], user["role"])
        _ISSUED_TOKENS.add(token)
        return token

    def _valid_issued(token):
        return bool(token) and token in _ISSUED_TOKENS

    @app.route("/")
    def index():
        return Response(
            "<!doctype html><html><head><title>O.N.Y.X.</title></head>"
            "<body><h1>O.N.Y.X. test site</h1></body></html>",
            mimetype="text/html")

    @app.route("/robots.txt")
    def robots():
        return Response("User-agent: *\nDisallow: /admin\nDisallow: /secret\n",
                        mimetype="text/plain")

    @app.route("/api")
    def api_index():
        return _j({"ok": True, "name": "O.N.Y.X. Test Site"})

    @app.route("/api/health")
    def health():
        return _j({"ok": True, "time": datetime.now(timezone.utc).isoformat()})

    @app.route("/api/echo",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def echo():
        if request.method == "HEAD":
            return Response("", status=200, mimetype="application/json")
        return _j({
            "method": request.method,
            "args": request.args.to_dict(flat=True),
            "form": request.form.to_dict(flat=True),
            "json": request.get_json(silent=True),
            "headers": dict(request.headers),
            "body": request.get_data(as_text=True),
        })

    @app.route("/api/error/<int:code>")
    def err(code):
        if code not in (400, 401, 403, 404, 418, 500, 503):
            code = 400
        return _j({"ok": False, "code": code, "error": "requested"}, status=code)

    @app.route("/api/redirect")
    def redir():
        n = request.args.get("n", type=int)
        if n is None:
            return redirect("/api/redirect?n=0", code=302)
        if n <= 0:
            return _j({"ok": True, "redirected": True, "chain_end": True})
        return redirect(f"/api/redirect?n={n - 1}", code=302)

    @app.route("/api/slow")
    def slow():
        try:
            delay = float(request.args.get("delay", "1"))
        except ValueError:
            delay = 1.0
        time.sleep(max(0.0, min(delay, 60.0)))
        return _j({"ok": True, "slept": delay})

    @app.route("/api/users")
    def users():
        safe = [{k: v for k, v in u.items()
                 if k not in ("password", "requires_2fa")} for u in USERS]
        return _j({"count": len(safe), "users": safe})

    @app.route("/api/users/<int:uid>")
    def user(uid):
        for u in USERS:
            if u["id"] == uid:
                return _j({"ok": True,
                           "user": {k: v for k, v in u.items()
                                    if k not in ("password", "requires_2fa")}})
        return _j({"ok": False, "error": "not found"}, 404)

    @app.route("/api/notes/<int:nid>")
    def note(nid):
        if nid in _NOTES:
            return _j({"ok": True, "note": _NOTES[nid]})
        return _j({"ok": False, "error": "not found"}, 404)

    def _auth_check(required_role=None):
        token = _read_token()
        if not token:
            return None, _j({"ok": False, "error": "missing token"}, 401)
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except Exception as e:
            return None, _j({"ok": False, "error": "invalid token",
                             "detail": str(e)}, 401)
        if not _valid_issued(token):
            return None, _j({"ok": False, "error": "unknown token"}, 401)
        if required_role and payload.get("role") != required_role:
            return None, _j({"ok": False, "error": "forbidden"}, 403)
        return payload, None

    @app.route("/api/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or request.form.to_dict()
        u = data.get("username", "")
        p = data.get("password", "")
        user = _user_by_name(u)
        if not user or user["password"] != p:
            return _j({"ok": False, "error": "invalid credentials"}, 401)
        if user.get("requires_2fa"):
            cid = "ch-" + uuid.uuid4().hex[:10]
            _MFA_CHALLENGES[cid] = user["username"]
            return _j({
                "ok": True,
                "status": "mfa_required",
                "challenge_id": cid,
                "message": "enter 6-digit code",
            })
        token = _issue_token(user)
        resp = _j({"ok": True, "token": token,
                   "user": {"id": user["id"], "username": user["username"],
                            "role": user["role"]}})
        resp.set_cookie("access_token", token, max_age=3600)
        return resp

    @app.route("/api/2fa/verify", methods=["POST"])
    def verify_2fa():
        data = request.get_json(silent=True) or request.form.to_dict()
        cid = data.get("challenge_id", "")
        code = (data.get("code") or "").strip()
        if not cid or cid not in _MFA_CHALLENGES:
            return _j({"ok": False, "error": "unknown challenge"}, 400)
        if pyotp is None:
            return _j({"ok": False, "error": "pyotp not installed on server"}, 500)
        if not code or len(code) < 6:
            return _j({"ok": False, "error": "invalid code"}, 400)
        totp = pyotp.TOTP(TOTP_SECRET)
        if not totp.verify(code, valid_window=1):
            return _j({"ok": False, "error": "wrong code"}, 401)
        username = _MFA_CHALLENGES.pop(cid)
        user = _user_by_name(username)
        if not user:
            return _j({"ok": False, "error": "user not found"}, 404)
        token = _issue_token(user)
        return _j({"ok": True, "token": token,
                   "user": {"id": user["id"], "username": user["username"],
                            "role": user["role"]}})

    @app.route("/api/login/form", methods=["POST"])
    def login_form():
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        user = _user_by_name(u)
        if not user or user["password"] != p:
            return _j({"ok": False, "error": "invalid credentials"}, 401)
        if user.get("requires_2fa"):
            return _j({"ok": False, "error": "2fa required"}, 401)
        token = _issue_token(user)
        return _j({"ok": True, "token": token,
                   "user": {"username": user["username"], "role": user["role"]}})

    @app.route("/api/register", methods=["POST"])
    def register():
        data = request.get_json(silent=True) or request.form.to_dict()
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        if not username or not password:
            return _j({"ok": False, "error": "username and password required"}, 400)
        if any(u["username"] == username for u in USERS):
            return _j({"ok": False, "error": "username taken"}, 409)
        USERS.append({"id": len(USERS) + 1, "username": username,
                      "password": password, "role": "user",
                      "email": data.get("email", ""),
                      "api_key": "sk_live_" + secrets.token_hex(8),
                      "requires_2fa": False})
        return _j({"ok": True, "username": username})

    @app.route("/api/me")
    def me():
        payload, err = _auth_check()
        if err is not None:
            return err
        return _j({"ok": True, "user": payload})

    @app.route("/api/admin")
    def admin():
        payload, err = _auth_check(required_role="admin")
        if err is not None:
            return err
        return _j({"ok": True, "message": "Welcome, admin", "user": payload})

    @app.route("/api/comments", methods=["GET", "POST"])
    def comments():
        if request.method == "POST":
            data = request.get_json(silent=True) or request.form.to_dict()
            _COMMENTS.append({
                "id": len(_COMMENTS) + 1,
                "author": data.get("author") or "anonymous",
                "body": data.get("body") or "",
                "user_id": None,
            })
            return _j({"ok": True})
        return _j({"ok": True, "count": len(_COMMENTS), "comments": _COMMENTS})

    @app.route("/api/comments/<int:cid>", methods=["GET", "DELETE"])
    def comment_one(cid):
        if request.method == "DELETE":
            return _j({"ok": True, "deleted": cid})
        for c in _COMMENTS:
            if c["id"] == cid:
                return _j({"ok": True, "comment": c})
        return _j({"ok": False, "error": "not found"}, 404)

    @app.route("/api/debug")
    def debug():
        return _j({"ok": True, "jwt_secret": JWT_SECRET,
                   "totp_secret": TOTP_SECRET})

    @app.route("/admin")
    def admin_page():
        return Response("Forbidden", status=403, mimetype="text/plain")

    @app.route("/secret")
    def secret_page():
        return Response("Forbidden", status=403, mimetype="text/plain")

    @app.route("/uploads")
    def uploads_page():
        return Response("uploads listing", mimetype="text/plain")

    @app.errorhandler(404)
    def on_404(e):
        if request.path.startswith("/api/"):
            return _j({"ok": False, "error": "not found"}, 404)
        return Response("404", status=404, mimetype="text/plain")

    return app


class TestServer:
    def __init__(self):
        self.app = _build_app()
        self._server = None
        self._thread = None
        self.port = None

    def start(self) -> bool:
        if self._server is not None:
            return True
        try:
            self._server = make_server("127.0.0.1", 0, self.app, threaded=True)
            self.port = self._server.server_port
            import threading
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            print(f"[test_server] start failed: {e}")
            self._server = None
            self._thread = None
            self.port = None
            return False

    def stop(self):
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None
        self.port = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}" if self.port else None