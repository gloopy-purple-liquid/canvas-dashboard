import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_module, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from canvas_client import CanvasClient, parse_week_range, parse_homepage_day, enrich_tasks

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
        return float("inf")


def _short_course_name(name):
    # Course names carry a section/term code before a "(A-E)" grade-band marker,
    # e.g. "Humanities 6 Q1-Q1-1(A-E) 5-6(B,D)-Smith". Strip everything from the
    # code onward. The code is sometimes space-separated ("6 Q1-Q1-1"), sometimes
    # glued to the name with a hyphen ("Pod Squad-26-27-1", "LAUNCH-Q1-1").
    name = (name or "").strip()
    marker = re.search(r"\([A-Za-z]-[A-Za-z]\)", name)
    if not marker:
        return name
    head = name[: marker.start()].rstrip()
    sp = head.rfind(" ")
    token = head[sp + 1:]
    if re.match(r"(Q\d|S\d|A-|A\d|\d)", token):
        cut = sp + 1  # the whole trailing token is the code
    else:
        hyphen = re.search(r"-(?=\d|Q\d|S\d)", token)  # code glued via hyphen
        cut = (sp + 1 + hyphen.start()) if hyphen else len(head)
    return head[:cut].strip() or name


def _course_homepage(cid, weekday, ref_date):
    try:
        fp = canvas_client.get_front_page(cid)
        if not fp:
            return None
        week = parse_week_range(fp["title"], ref_date)
        range_found = week is not None
        in_range = (not range_found) or (week[0] <= ref_date <= week[1])
        if not in_range:
            return {"range_found": True, "in_range": False, "live_class": None, "tasks": [], "zoom_url": None}
        parsed = parse_homepage_day(fp["body"], weekday)
        module_map = canvas_client.get_module_items_map(cid)
        tasks = enrich_tasks(parsed["tasks"], module_map)
        zoom = canvas_client.get_zoom_url(cid) if parsed["live_class"] else None
        return {
            "range_found": range_found,
            "in_range": True,
            "live_class": parsed["live_class"],
            "tasks": tasks,
            "zoom_url": zoom,
        }
    except Exception as exc:
        app.logger.warning("homepage failed for course %s: %s", cid, exc)
        return None


def _resolve_task_status(item, course_id):
    try:
        s = canvas_client.get_submission_details(course_id, item["content_id"])
        state = s.get("workflow_state", "unsubmitted")
        item["submitted"] = (
            state in ("submitted", "graded", "pending_review", "excused")
            or bool(s.get("submitted_at"))
        )
        item["graded"] = state == "graded" or bool(s.get("grade"))
        item["grade"] = s.get("grade")
    except Exception as exc:
        app.logger.warning("task status failed for %s/%s: %s", course_id, item.get("content_id"), exc)


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
        weekday = d.weekday()
        today_start_utc = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today_end_utc = (datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        ignore = _load_ignore()
        ignored_aids = set(ignore.get("assignment_ids", []))
        ignored_cids = set(ignore.get("course_ids", []))

        with ThreadPoolExecutor(max_workers=8) as pool:
            assignments_fut = pool.submit(canvas_client.get_assignments_due, date_str, course_ids, tzoffset=tzoffset)
            homepage_futs = {cid: pool.submit(_course_homepage, cid, weekday, d) for cid in course_ids}
            raw_assignments = assignments_fut.result()
            homepages = {cid: fut.result() for cid, fut in homepage_futs.items()}

        # assignments (unchanged logic)
        seen_ids = set()
        assignments = []
        for a in raw_assignments:
            if a["id"] in ignored_aids or a["course_id"] in ignored_cids:
                continue
            if a["id"] in seen_ids:
                continue
            seen_ids.add(a["id"])
            due = a["due_at"] or ""
            if due <= today_end_utc:
                assignments.append({
                    "id": a["id"],
                    "course_id": a["course_id"],
                    "title": a["title"],
                    "course_name": course_map.get(a["course_id"], ""),
                    "due_at": a["due_at"],
                    "points_possible": a["points_possible"],
                })
        assignments.sort(key=lambda a: a["due_at"] or "")

        # homepage availability (all courses share the same real-world week)
        range_results = [r for r in homepages.values() if r and r["range_found"]]
        homepage_available = (not range_results) or any(r["in_range"] for r in range_results)

        # schedule + tasks from homepages
        schedule = []
        tasks = []
        for cid in course_ids:
            if cid in ignored_cids:
                continue
            r = homepages.get(cid)
            if not r or not r["in_range"]:
                continue
            if r["live_class"]:
                schedule.append({
                    "time": r["live_class"]["time"],
                    "title": f'{_short_course_name(course_map.get(cid, ""))} — Live Class',
                    "zoom_url": r["zoom_url"],
                })
            if r["tasks"]:
                tasks.append({
                    "course_id": cid,
                    "course_name": course_map.get(cid, ""),
                    "items": r["tasks"],
                })

        # resolve submission status for submittable tasks
        pairs = [(it, block["course_id"]) for block in tasks for it in block["items"]
                 if it["submittable"] and it["content_id"]]
        if pairs:
            canvas_client._resolve_student_id()
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda p: _resolve_task_status(p[0], p[1]), pairs))

        # manual schedule config entries
        day_abbr = d.strftime("%a")
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
            "homepage_available": homepage_available,
            "schedule": schedule,
            "tasks": tasks,
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
