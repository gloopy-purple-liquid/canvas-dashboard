import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_module, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from canvas_client import CanvasClient

load_dotenv()

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
IGNORE_FILE = os.path.join(_DATA_DIR, "ignore.json")
SCHEDULE_CONFIG_FILE = os.path.join(_DATA_DIR, "schedule_config.json")


def _load_schedule_config():
    try:
        with open(SCHEDULE_CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_schedule_config(entries):
    os.makedirs(os.path.dirname(SCHEDULE_CONFIG_FILE), exist_ok=True)
    with open(SCHEDULE_CONFIG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def _time_sort_key(time_str):
    try:
        t = datetime.strptime(time_str, "%I:%M %p")
        return t.hour * 60 + t.minute
    except ValueError:
        return -1


def _load_ignore():
    try:
        with open(IGNORE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"assignment_ids": [], "course_ids": []}


def _save_ignore(data):
    os.makedirs(os.path.dirname(IGNORE_FILE), exist_ok=True)
    with open(IGNORE_FILE, "w") as f:
        json.dump(data, f, indent=2)

app = Flask(__name__)

canvas_client = CanvasClient(
    os.getenv("CANVAS_BASE_URL", ""),
    os.getenv("CANVAS_TOKEN", ""),
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/day")
def api_day():
    date_str = request.args.get("date", date_module.today().isoformat())
    tzoffset = int(request.args.get("tzoffset", 0))
    try:
        courses = [c for c in canvas_client.get_active_courses() if c.get("name")]
        course_ids = [str(c["id"]) for c in courses]
        course_map = {str(c["id"]): c["name"] for c in courses}

        tz = timezone(timedelta(minutes=-tzoffset))
        d = date_module.fromisoformat(date_str)
        today_start_utc = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # +2h buffer so assignments stored in PST still appear on PDT days
        today_end_utc = (datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        with ThreadPoolExecutor(max_workers=2) as pool:
            schedule_fut = pool.submit(canvas_client.get_schedule, date_str, course_ids, tzoffset)
            assignments_fut = pool.submit(canvas_client.get_assignments_due, date_str, course_ids, tzoffset=tzoffset)
            schedule = schedule_fut.result()
            raw_assignments = assignments_fut.result()

        ignore = _load_ignore()
        ignored_aids = set(ignore.get("assignment_ids", []))
        ignored_cids = set(ignore.get("course_ids", []))

        seen_ids = set()
        assignments = []
        for a in raw_assignments:
            if a["id"] in ignored_aids or a["course_id"] in ignored_cids:
                continue
            if a["id"] in seen_ids:
                continue
            seen_ids.add(a["id"])
            due = a["due_at"] or ""
            if due <= today_end_utc:  # today and past; no future assignments
                assignments.append({
                    "id": a["id"],
                    "course_id": a["course_id"],
                    "title": a["title"],
                    "course_name": course_map.get(a["course_id"], ""),
                    "due_at": a["due_at"],
                    "points_possible": a["points_possible"],
                })

        assignments.sort(key=lambda a: a["due_at"] or "")

        day_abbr = d.strftime("%a")  # "Mon", "Tue", …
        for entry in _load_schedule_config():
            if day_abbr in entry.get("days", []):
                schedule.append({
                    "time": entry.get("time", ""),
                    "title": entry.get("title", ""),
                    "zoom_url": entry.get("zoom_url") or None,
                    "manual": True,
                    "manual_id": entry.get("id", ""),
                })
        schedule.sort(key=lambda e: _time_sort_key(e.get("time", "")))

        return jsonify({
            "date": date_str,
            "schedule": schedule,
            "assignments": assignments,
            "today_start_utc": today_start_utc,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/submissions", methods=["POST"])
def api_submissions():
    items = request.get_json() or []
    if not items:
        return jsonify([])
    try:
        canvas_client._resolve_student_id()

        def _fetch(item):
            try:
                s = canvas_client.get_submission_details(item["course_id"], item["id"])
                state = s.get("workflow_state", "unsubmitted")
                submitted = (
                    state in ("submitted", "graded", "pending_review", "excused")
                    or bool(s.get("submitted_at"))
                )
                return {
                    "id": item["id"],
                    "submitted": submitted,
                    "graded": state == "graded" or bool(s.get("grade")),
                    "grade": s.get("grade"),
                    "score": s.get("score"),
                    "comments": [c["comment"] for c in s.get("submission_comments", [])],
                }
            except Exception as exc:
                app.logger.warning("submission details failed for %s/%s: %s", item["course_id"], item["id"], exc)
                return {"id": item["id"], "submitted": False, "graded": False, "grade": None, "score": None, "comments": []}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_fetch, items))
        return jsonify(results)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/missing")
def api_missing():
    try:
        courses = [c for c in canvas_client.get_active_courses() if c.get("name")]
        course_ids = [str(c["id"]) for c in courses]
        course_map = {str(c["id"]): c["name"] for c in courses}
        ignore = _load_ignore()
        ignored_aids = set(ignore.get("assignment_ids", []))
        ignored_cids = set(ignore.get("course_ids", []))
        raw = [
            a for a in canvas_client.get_missing_assignments()
            if str(a.get("id", "")) not in ignored_aids
            and str(a.get("course_id", "")) not in ignored_cids
        ]
        missing = [
            {
                "id": str(a["id"]),
                "course_id": str(a.get("course_id", "")),
                "title": a["name"],
                "course_name": course_map.get(str(a.get("course_id", "")), ""),
                "due_at": a.get("due_at"),
            }
            for a in raw
        ]
        return jsonify({"missing": missing})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/schedule-config", methods=["GET"])
def api_schedule_config_get():
    return jsonify(_load_schedule_config())


@app.route("/api/schedule-config", methods=["POST"])
def api_schedule_config_add():
    body = request.get_json() or {}
    title = body.get("title", "").strip()
    time_str = body.get("time", "").strip()
    days = body.get("days") or []
    zoom_url = body.get("zoom_url", "").strip()
    if not title or not time_str or not days:
        return jsonify({"error": "title, time, and days are required"}), 400
    entry = {
        "id": str(uuid.uuid4()),
        "title": title,
        "time": time_str,
        "days": days,
        "zoom_url": zoom_url or None,
    }
    entries = _load_schedule_config()
    entries.append(entry)
    _save_schedule_config(entries)
    return jsonify(entry), 201


@app.route("/api/schedule-config/<entry_id>", methods=["DELETE"])
def api_schedule_config_remove(entry_id):
    entries = _load_schedule_config()
    _save_schedule_config([e for e in entries if e.get("id") != entry_id])
    return jsonify({"ok": True})


@app.route("/api/ignore", methods=["POST"])
def api_ignore_add():
    body = request.get_json() or {}
    type_ = body.get("type")
    id_ = str(body.get("id", ""))
    if type_ not in ("assignment", "course") or not id_:
        return jsonify({"error": "invalid"}), 400
    data = _load_ignore()
    key = "assignment_ids" if type_ == "assignment" else "course_ids"
    if id_ not in data[key]:
        data[key].append(id_)
        _save_ignore(data)
    return jsonify({"ok": True})


@app.route("/api/ignore", methods=["DELETE"])
def api_ignore_remove():
    body = request.get_json() or {}
    type_ = body.get("type")
    id_ = str(body.get("id", ""))
    if type_ not in ("assignment", "course") or not id_:
        return jsonify({"error": "invalid"}), 400
    data = _load_ignore()
    key = "assignment_ids" if type_ == "assignment" else "course_ids"
    data[key] = [x for x in data[key] if x != id_]
    _save_ignore(data)
    return jsonify({"ok": True})


@app.route("/api/debug/schedule")
def api_debug_schedule():
    date_str = request.args.get("date", date_module.today().isoformat())
    tzoffset = int(request.args.get("tzoffset", 0))
    try:
        from datetime import timezone as _tz, timedelta as _td
        tz = _tz(_td(minutes=-tzoffset))
        d = date_module.fromisoformat(date_str)
        start_utc = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        courses = [c for c in canvas_client.get_active_courses() if c.get("name")]
        course_ids = [str(c["id"]) for c in courses]
        student_id = canvas_client._resolve_student_id()

        course_map = {str(c["id"]): c["name"] for c in courses}

        date_params = [("start_date", start_utc), ("end_date", end_utc), ("per_page", "50")]
        ctx_courses = [("context_codes[]", f"course_{cid}") for cid in course_ids]
        ctx_user = [("context_codes[]", f"user_{student_id}")]

        typed_events   = canvas_client._get("/api/v1/calendar_events", [("type", "event")]   + date_params + ctx_courses)
        untyped_events = canvas_client._get("/api/v1/calendar_events", date_params + ctx_courses)
        user_events    = canvas_client._get("/api/v1/calendar_events", [("type", "event")]   + date_params + ctx_user)

        def summarize(e):
            return {
                "title": e.get("title"),
                "start_at": e.get("start_at"),
                "context_code": e.get("context_code"),
                "type": e.get("type"),
                "description_snippet": (e.get("description") or "")[:300],
            }

        return jsonify({
            "query": {"start_date": start_utc, "end_date": end_utc, "student_id": student_id},
            "courses": [{"id": cid, "name": course_map.get(cid, "?")} for cid in course_ids],
            "typed_event_count": len(typed_events),
            "untyped_event_count": len(untyped_events),
            "user_event_count": len(user_events),
            "typed_events": [summarize(e) for e in typed_events],
            "untyped_events": [summarize(e) for e in untyped_events],
            "user_events": [summarize(e) for e in user_events],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/debug/assignments")
def api_debug_assignments():
    date_str = request.args.get("date", date_module.today().isoformat())
    try:
        from datetime import date as _d, timedelta
        next_day = (_d.fromisoformat(date_str) + timedelta(days=1)).isoformat()
        courses = [c for c in canvas_client.get_active_courses() if c.get("name")]
        course_ids = [str(c["id"]) for c in courses]
        params = [
            ("type", "assignment"),
            ("start_date", date_str),
            ("end_date", next_day),
            ("per_page", "50"),
        ] + [("context_codes[]", f"course_{cid}") for cid in course_ids]
        events = canvas_client._get("/api/v1/calendar_events", params)
        return jsonify({"date": date_str, "count": len(events), "events": events})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/debug/submission")
def api_debug_submission():
    course_id = request.args.get("course_id", "")
    assignment_id = request.args.get("assignment_id", "")
    try:
        details = canvas_client.get_submission_details(course_id, assignment_id)
        return jsonify(details)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/debug")
def api_debug():
    date_str = request.args.get("date", date_module.today().isoformat())
    try:
        courses = canvas_client.get_active_courses()
        planner_raw = canvas_client._get(
            "/api/v1/planner/items",
            {"start_date": date_str, "end_date": date_str, "per_page": 50},
        )
        return jsonify({
            "date": date_str,
            "course_count": len(courses),
            "courses": [{"id": c["id"], "name": c.get("name", ""), "enrollment_state": c.get("enrollment_state")} for c in courses[:10]],
            "planner_item_count": len(planner_raw),
            "planner_items": planner_raw[:5],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(debug=True, port=port)
