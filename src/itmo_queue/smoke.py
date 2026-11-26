"""Repeatable HTTP smoke flow; only the initial administrator needs bootstrap."""

import argparse
import getpass
import http.cookiejar
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


class SmokeError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url, origin=None, cookies=None):
        self.base_url = base_url.rstrip("/")
        self.origin = origin
        self.cookies = cookies if cookies is not None else http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def request(self, method, path, payload=None, expected=200, headers=None):
        outgoing = dict(headers or {})
        if self.origin:
            outgoing["Origin"] = self.origin
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            outgoing["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + "/api/v1" + path, data=data, headers=outgoing, method=method
        )
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            if response.status != expected:
                # Validation errors can echo passwords. Never include response bodies here.
                raise SmokeError(f"{method} {path}: expected {expected}, got {response.status}")
            if self.origin and expected < 400:
                if (
                    response.headers.get("Access-Control-Allow-Origin") != self.origin
                    or response.headers.get("Access-Control-Allow-Credentials") != "true"
                ):
                    raise SmokeError(f"{method} {path}: credentialed CORS is not configured")
            if body and "application/json" in response.headers.get("Content-Type", ""):
                return json.loads(body)
            return None

    def login(self, login, password):
        self.request("POST", "/auth/login", {"login": login, "password": password})
        cookie = next((c for c in self.cookies if c.name == "sessionId"), None)
        if cookie is None or not cookie.has_nonstandard_attr("HttpOnly"):
            raise SmokeError("Login must set an HttpOnly sessionId cookie")
        return self.request("GET", "/me")

    def reloaded(self):
        """New HTTP client retaining only the cookie, as after a browser reload."""
        return Client(self.base_url, self.origin, self.cookies)


def prepare(base_url, admin_login, admin_password, origin=None):
    """Provision unique accounts and a preliminary queue using public HTTP APIs."""
    admin = Client(base_url, origin)
    if origin:
        admin.request(
            "OPTIONS",
            "/auth/login",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    if admin.login(admin_login, admin_password)["role"] != "admin":
        raise SmokeError("An administrator account is required for fixture provisioning")
    suffix = secrets.token_hex(4)
    actors = {}
    for role in ("teacher", "student"):
        login, password = f"smoke.{role}.{suffix}", secrets.token_urlsafe(24)
        profile = admin.request(
            "POST",
            "/auth/register",
            {
                "name": f"Smoke {role} {suffix}",
                "login": login,
                "password": password,
                "role": role,
                "can_create_queues": role == "teacher",
            },
            expected=201,
        )
        actors[role] = {"login": login, "password": password, "id": profile["id"]}
    discipline = admin.request("POST", "/disciplines", {"name": f"Smoke {suffix}"}, expected=201)
    admin.request(
        "PATCH",
        f"/users/{actors['teacher']['id']}",
        {
            "discipline_ids": [discipline["id"]],
        },
    )
    teacher = Client(base_url, origin)
    teacher.login(actors["teacher"]["login"], actors["teacher"]["password"])
    options = teacher.request("GET", "/session-options")
    if discipline["id"] not in [d["id"] for d in options["disciplines"]]:
        raise SmokeError("Assigned discipline is missing from session-options")
    now = datetime.now(UTC)
    queue = teacher.request(
        "POST",
        "/queues",
        {
            "discipline_id": discipline["id"],
            "title": f"Smoke defense {suffix}",
            "planned_start": now.isoformat(),
            "planned_end": (now + timedelta(hours=2)).isoformat(),
            "teacher_ids": [actors["teacher"]["id"]],
            "auditorium": "Smoke room",
            "registration_open": True,
            "quota": 5,
            "avg_duration_min": 10,
            "works": [
                {"title": "Smoke Lab 1", "priority": 2},
                {"title": "Smoke Lab 2", "priority": 1},
            ],
        },
        expected=201,
    )
    # The creation contract now creates a restricted stream automatically.
    # Give the demo student access through the same admin API as a real deployment.
    admin.request("PATCH", f"/users/{actors['student']['id']}", {"stream_id": queue["stream_id"]})
    student = Client(base_url, origin)
    student.login(actors["student"]["login"], actors["student"]["password"])
    if queue["id"] not in [q["id"] for q in student.request("GET", "/queues")]:
        raise SmokeError("The student cannot discover the created session")
    return {"base_url": base_url, "origin": origin, "accounts": actors, "queue": queue}


def run_flow(prepared):
    """Run defense and history over real HTTP, including auth failures and reloads."""
    base_url, origin, actors, queue = (
        prepared[k] for k in ("base_url", "origin", "accounts", "queue")
    )
    teacher, student = Client(base_url, origin), Client(base_url, origin)
    for role, client in (("teacher", teacher), ("student", student)):
        client.login(actors[role]["login"], actors[role]["password"])
    anonymous = Client(base_url)
    anonymous.request("GET", "/me", expected=401)
    anonymous.request(
        "POST",
        "/auth/login",
        {"login": actors["student"]["login"], "password": "incorrect-password"},
        expected=401,
    )
    base = f"/queues/{queue['id']}"
    payload = {
        "works": [
            {"work_id": w["work_id"], "artifact_url": f"https://example.org/smoke/{w['work_id']}"}
            for w in queue["works"]
        ]
    }
    entry = student.request("POST", base + "/entries", payload, expected=201)
    if len(entry["works"]) != 2 or not student.reloaded().request("GET", base)["my_entry"]:
        raise SmokeError("Enrollment was not persisted across reloads")
    student.request("POST", base + "/entries", payload, expected=409)
    student.request("POST", base + "/call-next", expected=403)
    teacher.request("POST", base + "/activate")
    key = {"Idempotency-Key": secrets.token_hex(12)}
    attempt = teacher.request("POST", base + "/call-next", headers=key)
    if teacher.reloaded().request("POST", base + "/call-next", headers=key)["id"] != attempt["id"]:
        raise SmokeError("Retrying a call created a duplicate attempt")
    participant = base + f"/students/{actors['student']['id']}"
    teacher.request("PATCH", participant, {"status": "in_defense", "attempt_id": attempt["id"]})
    evaluation = {
        "attempt_id": attempt["id"],
        "status": "accepted",
        "results": [
            {
                "work_id": queue["works"][0]["work_id"],
                "kind": "score",
                "score": "7.5",
                "comment": "Smoke fractional score",
            },
            {"work_id": queue["works"][1]["work_id"], "kind": "accepted"},
        ],
    }
    teacher.request("POST", participant + "/evaluate", evaluation)
    teacher.reloaded().request("POST", participant + "/evaluate", evaluation)
    history = student.reloaded().request("GET", "/me/history")
    if (
        len(history) != 1
        or history[0]["id"] != attempt["id"]
        or len(history[0]["results"]) != 2
        or not any(r["score"] == "7.5" for r in history[0]["results"])
    ):
        raise SmokeError("Separate work results were not persisted in student history")
    teacher.request("POST", base + "/finish")
    if student.reloaded().request("GET", base)["stage"] != "finished":
        raise SmokeError("Finished stage was not persisted")
    student.request("POST", "/auth/logout", expected=204)
    student.reloaded().request("GET", "/me", expected=401)
    return {
        "queue_id": queue["id"],
        "entry_id": entry["id"],
        "attempt_id": attempt["id"],
        "checks": [
            "cookie auth",
            "credentialed CORS" if origin else "same-origin HTTP",
            "teacher permissions",
            "multiwork enrollment",
            "reload",
            "idempotent call",
            "defense",
            "fractional grades",
            "history",
            "finish",
            "logout",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--origin", help="Frontend origin to check credentialed CORS")
    parser.add_argument(
        "--prepare-only", action="store_true", help="Leave fixtures ready for UI testing"
    )
    parser.add_argument(
        "--credentials-file", type=Path, help="Private JSON for local demo accounts"
    )
    args = parser.parse_args()
    if args.prepare_only and not args.credentials_file:
        parser.error("--prepare-only requires --credentials-file for the UI test accounts")
    if args.credentials_file and args.credentials_file.exists():
        parser.error("Credentials file already exists; choose a new file before provisioning")
    login = os.environ.get("SMOKE_ADMIN_LOGIN") or input("Administrator login: ")
    password = os.environ.get("SMOKE_ADMIN_PASSWORD") or getpass.getpass("Administrator password: ")
    try:
        prepared = prepare(args.base_url, login, password, args.origin)
        if args.credentials_file:
            args.credentials_file.parent.mkdir(parents=True, exist_ok=True)
            # Never overwrite an unrelated file or print passwords into the test log.
            descriptor = os.open(args.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(prepared, output, ensure_ascii=False, indent=2)
        report = {"queue_id": prepared["queue"]["id"], "status": "prepared"}
        if not args.prepare_only:
            report = run_flow(prepared)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (SmokeError, urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
