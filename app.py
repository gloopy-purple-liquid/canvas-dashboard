import os
from datetime import date as date_module

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from canvas_client import CanvasClient

load_dotenv()

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
    try:
        courses = canvas_client.get_active_courses()
        course_ids = [str(c["id"]) for c in courses]
        course_map = {str(c["id"]): c["name"] for c in courses}

        schedule = canvas_client.get_schedule(date_str, course_ids)
        planner_items = canvas_client.get_assignments_due(date_str)

        assignments = []
        for item in planner_items:
            plannable = item["plannable"]
            subs = item.get("submissions") or {}
            submitted = subs.get("submitted", False) if isinstance(subs, dict) else False
            graded = subs.get("graded", False) if isinstance(subs, dict) else False
            cid = str(plannable.get("course_id", ""))
            aid = str(plannable["id"])

            assignment = {
                "id": aid,
                "course_id": cid,
                "title": plannable["title"],
                "course_name": course_map.get(cid, ""),
                "due_at": plannable.get("due_at"),
                "submitted": submitted,
                "graded": graded,
                "grade": None,
                "score": None,
                "points_possible": plannable.get("points_possible"),
                "comments": [],
            }

            if submitted:
                try:
                    details = canvas_client.get_submission_details(cid, aid)
                    assignment["grade"] = details.get("grade")
                    assignment["score"] = details.get("score")
                    assignment["comments"] = [
                        c["comment"] for c in details.get("submission_comments", [])
                    ]
                except Exception:
                    pass

            assignments.append(assignment)

        return jsonify({"date": date_str, "schedule": schedule, "assignments": assignments})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True)
