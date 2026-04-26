import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_module, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from canvas_client import CanvasClient

load_dotenv()

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
IGNORE_FILE = os.path.join(_DATA_DIR, "ignore.json")


def _load_ignore():
    try:
        with open(IGNORE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"assignment_ids": [], "course_ids": []}


def _save_ignore(data):
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
            schedule_fut = pool.submit(canvas_client.get_schedule, date_str, course_ids)
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
