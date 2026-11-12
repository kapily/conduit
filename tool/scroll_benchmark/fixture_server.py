"""Fresh synthetic scroll workloads. Loopback only; no external data is read."""

import base64
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

stamp = int(time.time())
user = {
    "id": "scroll-demo",
    "name": "Demo",
    "email": "demo@example.test",
    "role": "user",
    "profile_image_url": "",
    "permissions": {
        "features": {"notes": True},
        "workspace": {
            "models": False,
            "knowledge": False,
            "prompts": False,
            "tools": False,
        },
    },
}


def body(kind, i):
    if kind == "indicators":
        return (
            "| Tile | Shape | Color | Border | Row | Column | Count | Pattern |\n|---|---|---|---|---|---|---|---|\n"
            + "\n".join(
                f"| {j} | Hexagon | Silver | Amber | {j} | {i} | {j + 1} | Striped |"
                for j in range(4)
            )
        )
    if i % 4 == 0:
        return f"### Sample {i}\n\nThe silver hexagon occupies the first tile."
    if i % 4 == 1:
        return "\n".join(
            f"- Tile {j}: the turquoise oval has a thin border." for j in range(36)
        )
    if i % 4 == 2:
        return (
            "```python\n"
            + "\n".join(
                f'tile_{j} = "silver_hexagon_turquoise_oval_amber_star" * 8'
                for j in range(28)
            )
            + "\n```"
        )
    return "| Tile | Shape | Count |\n|---|---|---|\n" + "\n".join(
        f"| {j} | Turquoise oval | {j + 1} |" for j in range(18)
    )


def chat(kind, count):
    cid = "chat-" + kind
    messages = []
    for i in range(count):
        mid = f"{cid}-{i}"
        messages.append(
            {
                "id": mid,
                "parentId": f"{cid}-{i - 1}" if i else None,
                "childrenIds": [f"{cid}-{i + 1}"] if i + 1 < count else [],
                "role": "user" if i % 2 == 0 else "assistant",
                "model": "demo-model",
                "content": f"Describe tile arrangement {i}."
                if i % 2 == 0
                else body(kind, i // 2),
                "done": True,
                "timestamp": stamp + i,
            }
        )
    return {
        "id": cid,
        "title": kind.title() + " scroll fixture",
        "user_id": user["id"],
        "created_at": stamp,
        "updated_at": stamp,
        "last_read_at": stamp,
        "pinned": False,
        "archived": False,
        "folder_id": None,
        "meta": {},
        "chat": {
            "id": cid,
            "title": kind.title() + " scroll fixture",
            "models": ["demo-model"],
            "history": {
                "currentId": messages[-1]["id"],
                "messages": {m["id"]: m for m in messages},
            },
            "messages": messages,
        },
    }


chats = [
    chat("mixed", 1000),
    chat("indicators", 120),
]
model = {
    "id": "demo-model",
    "name": "Shapes",
    "object": "model",
    "owned_by": "openai",
    "info": {
        "id": "demo-model",
        "name": "Shapes",
        "meta": {"capabilities": {"vision": False}},
    },
}


def summary(c):
    return {k: v for k, v in c.items() if k != "chat"}


def matches(c, q):
    return q.lower() in c["title"].lower()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, value, status=200):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/")
        q = parse_qs(u.query)
        page = int(q.get("page", ["1"])[0])
        text = q.get("text", q.get("query", [""]))[0]
        print("GET", p, flush=True)
        if p == "/health":
            return self.respond({"status": True})
        if p == "/api/version":
            return self.respond({"version": "0.13.0"})
        if p == "/api/config":
            return self.respond(
                {
                    "name": "Open WebUI",
                    "version": "0.13.0",
                    "id": "scroll-fixture",
                    "status": True,
                    "auth": True,
                    "features": {
                        "auth": True,
                        "enable_signup": False,
                        "enable_websocket": True,
                        "enable_notes": True,
                        "enable_channels": False,
                        "enable_direct_connections": False,
                    },
                    "default_models": "demo-model",
                    "default_prompt_suggestions": [],
                    "oauth": {"providers": {}},
                }
            )
        if p in ["/api/models", "/api/v1/models"]:
            return self.respond({"data": [model]})
        if p == "/api/v1/auths":
            return self.respond(user)
        if p in ["/api/v1/users/user/permissions", "/api/v1/users/permissions"]:
            return self.respond(user["permissions"])
        if p == "/api/v1/users/user/settings":
            return self.respond({"ui": {}})
        if p in ["/api/v1/chats", "/api/v1/chats/list"]:
            return self.respond(
                [
                    summary(c)
                    for c in chats
                    if q.get("include_folders") == ["true"] or not c["folder_id"]
                ]
                if page == 1
                else []
            )
        if p == "/api/v1/chats/search":
            return self.respond(
                [summary(c) for c in chats if matches(c, text)] if page == 1 else []
            )
        if p.startswith("/api/v1/chats/") and p.endswith("/search/snippet"):
            c = next((c for c in chats if c["id"] == p.split("/")[4]), None)
            return self.respond(
                " ".join(c["chat"]["messages"][0]["content"].split())[:200]
                if c
                else None
            )
        if p in [
            "/api/v1/chats/pinned",
            "/api/v1/chats/archived",
            "/api/v1/chats/all/tags",
            "/api/v1/chats/tags",
            "/api/v1/chats/active",
            "/api/v1/chats/all",
            "/api/v1/folders/shared",
            "/api/v1/terminal/servers",
        ]:
            return self.respond([])
        if p in ["/api/v1/chats/archived/count"]:
            return self.respond(0)
        if p.startswith("/api/v1/chats/"):
            c = next((c for c in chats if c["id"] == p.split("/")[4]), None)
            if c:
                return self.respond(c)
        if p == "/api/v1/folders":
            return self.respond([])
        if p == "/api/v1/notes/search":
            return self.respond({"items": [], "total": 0})
        if p == "/api/v1/notes":
            return self.respond([])
        if p == "/api/v1/users/scroll-demo":
            return self.respond(user)
        if p.startswith("/api/v1/configs"):
            return self.respond({})
        if p in ["/api/v1/knowledge", "/api/v1/files"]:
            return self.respond({"items": [], "total": 0})
        if any(
            s in p
            for s in [
                "channels",
                "tools",
                "prompts",
                "suggestions",
                "memories",
                "notifications",
                "automations",
                "calendar",
            ]
        ):
            return self.respond([])
        return self.respond({"detail": "Not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        print("POST", p, flush=True)
        if p == "/api/v1/auths/signin":
            encode = lambda x: (
                base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip("=")
            )
            token = (
                encode({"alg": "none", "typ": "JWT"})
                + "."
                + encode({"sub": user["id"], "exp": int(time.time()) + 86400})
                + ".synthetic"
            )
            return self.respond(
                {
                    **user,
                    "token": token,
                    "token_type": "Bearer",
                    "expires_at": int(time.time()) + 86400,
                }
            )
        if p.endswith("/read"):
            return self.respond({"updated_count": 1, "folder_unread_counts": {}})
        if "/chat-" in p:
            return self.respond(next((c for c in chats if c["id"] in p), chats[0]))
        return self.respond({})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18911), Handler).serve_forever()
