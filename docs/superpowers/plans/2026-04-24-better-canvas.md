# Better Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python/Flask web app that shows a student's Canvas LMS schedule (with Zoom links), assignments due, grades, teacher comments, and missing assignments in a single daily view.

**Architecture:** Flask serves a single-page HTML dashboard and proxies all Canvas API calls using a personal access token stored in `.env`. The frontend uses vanilla JS to fetch from two local endpoints (`/api/day` and `/api/missing`) and re-render on day navigation. Canvas API interaction is isolated in `canvas_client.py`.

**Tech Stack:** Python 3.9+, Flask 3.x, Requests 2.x, python-dotenv, pytest

---

## File Map

| File | Responsibility |
|---|---|
| `canvas_client.py` | All Canvas API HTTP calls; formats raw API data into clean dicts |
| `app.py` | Flask app; routes that call `canvas_client` and return JSON; serves `index.html` |
| `templates/index.html` | Single-page frontend: HTML structure, CSS, vanilla JS |
| `requirements.txt` | Python dependencies |
| `.env.example` | Token config template |
| `tests/test_canvas_client.py` | Unit tests for canvas_client (mocked HTTP) |
| `tests/test_app.py` | Integration tests for Flask routes (mocked canvas_client) |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `tests/__init__.py`
- Create: `templates/.gitkeep`

- [ ] **Step 1: Create requirements.txt**

```
flask==3.1.0
requests==2.32.3
python-dotenv==1.0.1
pytest==8.3.5
```

- [ ] **Step 2: Create .env.example**

```
CANVAS_TOKEN=your_personal_access_token_here
CANVAS_BASE_URL=https://school.instructure.com
```

- [ ] **Step 3: Create directory structure**

```bash
mkdir -p templates tests
touch tests/__init__.py
```

- [ ] **Step 4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: packages install without errors.

- [ ] **Step 5: Create .env from example (first-time setup only)**

```bash
cp .env.example .env
```

Then edit `.env` and fill in your Canvas token. Get one from: Canvas → Account → Settings → New Access Token.

- [ ] **Step 6: Create .gitignore**

```
.env
__pycache__/
*.pyc
.pytest_cache/
.superpowers/
```

- [ ] **Step 7: Initialize git and commit**

```bash
git init
git add requirements.txt .env.example tests/__init__.py .gitignore
git commit -m "feat: project scaffolding"
```

---

## Task 2: CanvasClient — Core + Courses

**Files:**
- Create: `canvas_client.py`
- Create: `tests/test_canvas_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_canvas_client.py`:

```python
from unittest.mock import patch, MagicMock
from canvas_client import CanvasClient


def make_client():
    return CanvasClient("https://canvas.test", "test-token")


@patch("canvas_client.requests.get")
def test_get_active_courses_returns_list(mock_get):
    mock_get.return_value.json.return_value = [
        {"id": 1, "name": "Mathematics"},
        {"id": 2, "name": "Science"},
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    courses = client.get_active_courses()

    assert courses == [{"id": 1, "name": "Mathematics"}, {"id": 2, "name": "Science"}]
    mock_get.assert_called_once_with(
        "https://canvas.test/api/v1/courses",
        headers={"Authorization": "Bearer test-token"},
        params={"enrollment_state": "active", "per_page": 50},
        timeout=10,
    )


@patch("canvas_client.requests.get")
def test_get_active_courses_raises_on_http_error(mock_get):
    import requests as req
    mock_get.return_value.raise_for_status.side_effect = req.HTTPError("401")

    client = make_client()
    try:
        client.get_active_courses()
        assert False, "should have raised"
    except req.HTTPError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_client'`

- [ ] **Step 3: Create canvas_client.py**

```python
import re
import requests
from datetime import datetime


class CanvasClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        response = requests.get(url, headers=self.headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_active_courses(self):
        return self._get("/api/v1/courses", {"enrollment_state": "active", "per_page": 50})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: CanvasClient with courses endpoint"
```

---

## Task 3: CanvasClient — Schedule

**Files:**
- Modify: `canvas_client.py`
- Modify: `tests/test_canvas_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
@patch("canvas_client.requests.get")
def test_get_schedule_returns_formatted_events(mock_get):
    mock_get.return_value.json.return_value = [
        {
            "id": "10",
            "title": "Mathematics — Period 1",
            "start_at": "2026-04-24T14:00:00Z",
            "location_name": "https://zoom.us/j/99999",
            "description": "",
        }
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    schedule = client.get_schedule("2026-04-24", ["1", "2"])

    assert len(schedule) == 1
    assert schedule[0]["title"] == "Mathematics — Period 1"
    assert schedule[0]["zoom_url"] == "https://zoom.us/j/99999"
    assert "AM" in schedule[0]["time"] or "PM" in schedule[0]["time"]

    call_params = mock_get.call_args[1]["params"]
    assert ("type", "event") in call_params
    assert ("context_codes[]", "course_1") in call_params
    assert ("context_codes[]", "course_2") in call_params


@patch("canvas_client.requests.get")
def test_get_schedule_extracts_zoom_from_description(mock_get):
    mock_get.return_value.json.return_value = [
        {
            "id": "11",
            "title": "Science",
            "start_at": "2026-04-24T16:00:00Z",
            "location_name": "Room 101",
            "description": '<a href="https://zoom.us/j/12345?pwd=abc">Join Zoom</a>',
        }
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    schedule = client.get_schedule("2026-04-24", ["1"])

    assert schedule[0]["zoom_url"] == "https://zoom.us/j/12345?pwd=abc"


@patch("canvas_client.requests.get")
def test_get_schedule_zoom_url_none_when_missing(mock_get):
    mock_get.return_value.json.return_value = [
        {
            "id": "12",
            "title": "English",
            "start_at": "2026-04-24T19:00:00Z",
            "location_name": "Classroom",
            "description": "",
        }
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    schedule = client.get_schedule("2026-04-24", ["1"])

    assert schedule[0]["zoom_url"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: 3 new tests FAIL with `AttributeError: 'CanvasClient' object has no attribute 'get_schedule'`

- [ ] **Step 3: Add get_schedule to canvas_client.py**

Add these methods to `CanvasClient` in `canvas_client.py`:

```python
    def get_schedule(self, date_str, course_ids):
        params = [
            ("type", "event"),
            ("start_date", date_str),
            ("end_date", date_str),
            ("per_page", "50"),
        ] + [("context_codes[]", f"course_{cid}") for cid in course_ids]
        events = self._get("/api/v1/calendar_events", params)
        return [
            {
                "time": _format_time(event.get("start_at", "")),
                "title": event.get("title", ""),
                "zoom_url": self._extract_zoom_url(event),
            }
            for event in events
        ]

    def _extract_zoom_url(self, event):
        location = event.get("location_name") or ""
        if "zoom.us" in location:
            return location.strip()
        description = event.get("description") or ""
        match = re.search(r'https://[^\s"\'<>]*zoom\.us[^\s"\'<>]*', description)
        return match.group(0) if match else None
```

Also add this module-level helper function (outside the class, at the top of the file after imports):

```python
def _format_time(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return iso_str
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: CanvasClient schedule with Zoom URL extraction"
```

---

## Task 4: CanvasClient — Assignments Due + Submission Details

**Files:**
- Modify: `canvas_client.py`
- Modify: `tests/test_canvas_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
@patch("canvas_client.requests.get")
def test_get_assignments_due_filters_to_assignments(mock_get):
    mock_get.return_value.json.return_value = [
        {
            "plannable_type": "assignment",
            "plannable": {
                "id": 456,
                "course_id": 789,
                "title": "Math HW #12",
                "due_at": "2026-04-24T23:59:00Z",
                "points_possible": 100,
            },
            "submissions": {"submitted": True, "graded": True, "missing": False},
        },
        {
            "plannable_type": "calendar_event",
            "plannable": {"id": 99, "title": "Study Hall"},
            "submissions": False,
        },
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    items = client.get_assignments_due("2026-04-24")

    assert len(items) == 1
    assert items[0]["plannable"]["title"] == "Math HW #12"


@patch("canvas_client.requests.get")
def test_get_submission_details_returns_grade_and_comments(mock_get):
    mock_get.return_value.json.return_value = {
        "grade": "94%",
        "score": 94.0,
        "submission_comments": [
            {"comment": "Great work!", "author_name": "Mrs. Anderson"}
        ],
    }
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    details = client.get_submission_details("789", "456")

    assert details["grade"] == "94%"
    assert details["score"] == 94.0
    assert details["submission_comments"][0]["comment"] == "Great work!"
    mock_get.assert_called_once_with(
        "https://canvas.test/api/v1/courses/789/assignments/456/submissions/self",
        headers={"Authorization": "Bearer test-token"},
        params={"include[]": "submission_comments"},
        timeout=10,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: 2 new tests FAIL.

- [ ] **Step 3: Add methods to canvas_client.py**

Add to `CanvasClient`:

```python
    def get_assignments_due(self, date_str):
        items = self._get(
            "/api/v1/planner/items",
            {"start_date": date_str, "end_date": date_str, "per_page": 50},
        )
        return [i for i in items if i.get("plannable_type") == "assignment"]

    def get_submission_details(self, course_id, assignment_id):
        return self._get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self",
            {"include[]": "submission_comments"},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: CanvasClient assignments due and submission details"
```

---

## Task 5: CanvasClient — Missing Assignments

**Files:**
- Modify: `canvas_client.py`
- Modify: `tests/test_canvas_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_canvas_client.py`:

```python
@patch("canvas_client.requests.get")
def test_get_missing_assignments_aggregates_across_courses(mock_get):
    def side_effect(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if "courses/1/assignments" in url:
            mock_resp.json.return_value = [
                {"id": 10, "name": "History Quiz", "course_id": 1, "due_at": "2026-04-21T23:59:00Z"}
            ]
        elif "courses/2/assignments" in url:
            mock_resp.json.return_value = []
        return mock_resp

    mock_get.side_effect = side_effect

    client = make_client()
    missing = client.get_missing_assignments(["1", "2"])

    assert len(missing) == 1
    assert missing[0]["name"] == "History Quiz"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: 1 new test FAILS.

- [ ] **Step 3: Add method to canvas_client.py**

Add to `CanvasClient`:

```python
    def get_missing_assignments(self, course_ids):
        missing = []
        for cid in course_ids:
            items = self._get(
                f"/api/v1/courses/{cid}/assignments",
                {"bucket": "missing", "per_page": 50},
            )
            missing.extend(items)
        return missing
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_canvas_client.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: CanvasClient missing assignments"
```

---

## Task 6: Flask App + /api/day Route

**Files:**
- Create: `app.py`
- Create: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_app.py`:

```python
from unittest.mock import patch, MagicMock
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@patch("app.canvas_client")
def test_api_day_returns_schedule_and_assignments(mock_client):
    mock_client.get_active_courses.return_value = [
        {"id": 1, "name": "Mathematics"}
    ]
    mock_client.get_schedule.return_value = [
        {"time": "9:00 AM", "title": "Mathematics — Period 1", "zoom_url": "https://zoom.us/j/1"}
    ]
    mock_client.get_assignments_due.return_value = [
        {
            "plannable": {
                "id": 456,
                "course_id": 1,
                "title": "Math HW #12",
                "due_at": "2026-04-24T23:59:00Z",
                "points_possible": 100,
            },
            "submissions": {"submitted": False, "graded": False, "missing": False},
        }
    ]

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day?date=2026-04-24")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["date"] == "2026-04-24"
    assert len(data["schedule"]) == 1
    assert data["schedule"][0]["title"] == "Mathematics — Period 1"
    assert len(data["assignments"]) == 1
    assert data["assignments"][0]["title"] == "Math HW #12"
    assert data["assignments"][0]["submitted"] is False
    assert data["assignments"][0]["comments"] == []


@patch("app.canvas_client")
def test_api_day_fetches_submission_details_when_submitted(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Mathematics"}]
    mock_client.get_schedule.return_value = []
    mock_client.get_assignments_due.return_value = [
        {
            "plannable": {
                "id": 456,
                "course_id": 1,
                "title": "Math HW #12",
                "due_at": "2026-04-24T23:59:00Z",
                "points_possible": 100,
            },
            "submissions": {"submitted": True, "graded": True, "missing": False},
        }
    ]
    mock_client.get_submission_details.return_value = {
        "grade": "94%",
        "score": 94.0,
        "submission_comments": [{"comment": "Great work!", "author_name": "Mrs. Anderson"}],
    }

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day?date=2026-04-24")

    data = resp.get_json()
    assert data["assignments"][0]["grade"] == "94%"
    assert data["assignments"][0]["comments"] == ["Great work!"]
    mock_client.get_submission_details.assert_called_once_with("1", "456")


@patch("app.canvas_client")
def test_api_day_defaults_to_today(mock_client):
    mock_client.get_active_courses.return_value = []
    mock_client.get_schedule.return_value = []
    mock_client.get_assignments_due.return_value = []

    from datetime import date
    today = date.today().isoformat()

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day")

    data = resp.get_json()
    assert data["date"] == today
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_app.py -v
```

Expected: `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Create app.py with /api/day route**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_app.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: Flask app with /api/day route"
```

---

## Task 7: Flask /api/missing Route

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
@patch("app.canvas_client")
def test_api_missing_returns_missing_assignments(mock_client):
    mock_client.get_active_courses.return_value = [
        {"id": 1, "name": "US History"}
    ]
    mock_client.get_missing_assignments.return_value = [
        {"id": 10, "name": "History Quiz", "course_id": 1, "due_at": "2026-04-21T23:59:00Z"}
    ]

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/missing")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["missing"]) == 1
    assert data["missing"][0]["title"] == "History Quiz"
    assert data["missing"][0]["course_name"] == "US History"
    assert data["missing"][0]["due_at"] == "2026-04-21T23:59:00Z"


@patch("app.canvas_client")
def test_api_missing_returns_empty_list_when_none(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Math"}]
    mock_client.get_missing_assignments.return_value = []

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/missing")

    data = resp.get_json()
    assert data["missing"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_app.py -v
```

Expected: 2 new tests FAIL with 404.

- [ ] **Step 3: Add /api/missing route to app.py**

Add this route to `app.py` (before `if __name__ == "__main__":`):

```python
@app.route("/api/missing")
def api_missing():
    try:
        courses = canvas_client.get_active_courses()
        course_ids = [str(c["id"]) for c in courses]
        course_map = {str(c["id"]): c["name"] for c in courses}
        raw = canvas_client.get_missing_assignments(course_ids)
        missing = [
            {
                "title": a["name"],
                "course_name": course_map.get(str(a.get("course_id", "")), ""),
                "due_at": a.get("due_at"),
            }
            for a in raw
        ]
        return jsonify({"missing": missing})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
pytest tests/ -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: /api/missing route"
```

---

## Task 8: Frontend HTML + CSS

**Files:**
- Create: `templates/index.html`

- [ ] **Step 1: Create templates/index.html with structure and CSS**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Better Canvas</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f0f2f5;
      color: #2c3e50;
      min-height: 100vh;
    }

    header {
      background: #2c3e50;
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 10;
    }

    .logo { color: #fff; font-size: 18px; font-weight: 700; }

    .day-nav { display: flex; align-items: center; gap: 10px; }

    .day-nav button {
      background: #34495e;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 6px 14px;
      font-size: 14px;
      cursor: pointer;
    }

    .day-nav button:hover { background: #4a6278; }

    #today-btn { background: #27ae60; }
    #today-btn:hover { background: #2ecc71; }

    #current-date { color: #fff; font-size: 15px; font-weight: 600; min-width: 200px; text-align: center; }

    main { max-width: 760px; margin: 0 auto; padding: 24px 16px; display: flex; flex-direction: column; gap: 28px; }

    .section-label {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 1px;
      text-transform: uppercase;
      color: #7f8c8d;
      margin-bottom: 10px;
    }

    .section-label.missing-label { color: #e67e22; }

    .card {
      background: #fff;
      border-radius: 8px;
      border: 1px solid #e0e0e0;
      display: flex;
      align-items: center;
      padding: 13px 16px;
      gap: 14px;
      margin-bottom: 8px;
    }

    .card.missing { border-color: #f39c12; }
    .card.not-submitted { border-color: #e74c3c; }

    .card-time { min-width: 72px; font-size: 13px; font-weight: 600; color: #7f8c8d; }

    .card-body { flex: 1; }

    .card-title { font-size: 14px; font-weight: 700; }

    .card-sub { font-size: 12px; color: #7f8c8d; margin-top: 2px; }

    .card-comment { font-size: 12px; color: #27ae60; font-style: italic; margin-top: 4px; }

    .card-right { text-align: right; white-space: nowrap; }

    .grade { font-size: 18px; font-weight: 700; color: #27ae60; }

    .status { font-size: 12px; font-weight: 600; }
    .status.submitted { color: #27ae60; }
    .status.pending { color: #f39c12; }
    .status.not-submitted { color: #e74c3c; }
    .status.missing { color: #e67e22; }

    .status-icon {
      width: 22px; height: 22px; border-radius: 50%;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 700; color: #fff; flex-shrink: 0;
    }

    .status-icon.ok { background: #2ecc71; }
    .status-icon.warn { background: #e74c3c; }
    .status-icon.missing { background: #f39c12; }

    .zoom-btn {
      background: #3498db;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 7px 14px;
      font-size: 12px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      white-space: nowrap;
    }

    .zoom-btn:hover { background: #2980b9; }

    .empty { color: #bdc3c7; font-size: 14px; padding: 12px 0; }

    #error-banner {
      background: #e74c3c;
      color: #fff;
      padding: 12px 16px;
      border-radius: 8px;
      display: none;
    }

    #loading { color: #7f8c8d; font-size: 14px; padding: 20px 0; display: none; }

    #missing-section { display: none; }
  </style>
</head>
<body>

<header>
  <div class="logo">📚 Better Canvas</div>
  <div class="day-nav">
    <button id="prev-day">◀</button>
    <span id="current-date"></span>
    <button id="next-day">▶</button>
    <button id="today-btn">Today</button>
  </div>
</header>

<main>
  <div id="error-banner"></div>
  <div id="loading">Loading...</div>

  <section id="schedule-section">
    <div class="section-label">📅 Today's Schedule</div>
    <div id="schedule-list"></div>
  </section>

  <section id="assignments-section">
    <div class="section-label">📝 Assignments Due Today</div>
    <div id="assignments-list"></div>
  </section>

  <section id="missing-section">
    <div class="section-label missing-label">⚠ Missing Assignments</div>
    <div id="missing-list"></div>
  </section>
</main>

<script>
  // JS added in Task 9
</script>

</body>
</html>
```

- [ ] **Step 2: Verify the page loads**

```bash
python app.py
```

Open `http://localhost:5000` — you should see the header with nav buttons and empty sections. No JS errors in the browser console.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: frontend HTML and CSS"
```

---

## Task 9: Frontend JavaScript

**Files:**
- Modify: `templates/index.html` (replace the empty `<script>` block)

- [ ] **Step 1: Replace the empty script block with the full JS**

Find `<script>` and `</script>` in `templates/index.html` and replace everything between them with:

```javascript
  const DAY_MS = 86400000;

  function toDateStr(date) {
    return date.toISOString().slice(0, 10);
  }

  function formatDisplayDate(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  }

  function shiftDate(dateStr, days) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d + days);
    return toDateStr(dt);
  }

  function formatDueDate(isoStr) {
    if (!isoStr) return "";
    const dt = new Date(isoStr);
    return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function showError(msg) {
    const banner = document.getElementById("error-banner");
    banner.textContent = msg;
    banner.style.display = "block";
  }

  function hideError() {
    document.getElementById("error-banner").style.display = "none";
  }

  function renderSchedule(schedule) {
    const list = document.getElementById("schedule-list");
    if (!schedule.length) {
      list.innerHTML = '<div class="empty">No classes scheduled.</div>';
      return;
    }
    list.innerHTML = schedule.map(ev => `
      <div class="card">
        <div class="card-time">${ev.time}</div>
        <div class="card-body">
          <div class="card-title">${ev.title}</div>
        </div>
        ${ev.zoom_url
          ? `<a class="zoom-btn" href="${ev.zoom_url}" target="_blank" rel="noopener">Join Zoom</a>`
          : ""}
      </div>
    `).join("");
  }

  function renderAssignments(assignments) {
    const list = document.getElementById("assignments-list");
    if (!assignments.length) {
      list.innerHTML = '<div class="empty">No assignments due today.</div>';
      return;
    }
    list.innerHTML = assignments.map(a => {
      let iconClass, statusHtml, cardClass = "";

      if (a.submitted && a.graded) {
        iconClass = "ok";
        statusHtml = `<div class="grade">${a.grade || ""}</div><div class="status submitted">Graded</div>`;
      } else if (a.submitted) {
        iconClass = "ok";
        statusHtml = `<div class="status pending">Pending grade</div>`;
      } else {
        iconClass = "warn";
        cardClass = "not-submitted";
        const due = a.due_at ? `Due ${formatDueDate(a.due_at)}` : "";
        statusHtml = `<div class="status not-submitted">Not submitted</div><div class="card-sub">${due}</div>`;
      }

      const comments = (a.comments || []).map(c =>
        `<div class="card-comment">"${c}"</div>`
      ).join("");

      return `
        <div class="card ${cardClass}">
          <span class="status-icon ${iconClass}">${a.submitted ? "✓" : "!"}</span>
          <div class="card-body">
            <div class="card-title">${a.title}</div>
            <div class="card-sub">${a.course_name}</div>
            ${comments}
          </div>
          <div class="card-right">${statusHtml}</div>
        </div>
      `;
    }).join("");
  }

  function renderMissing(missing) {
    const section = document.getElementById("missing-section");
    const list = document.getElementById("missing-list");
    if (!missing.length) {
      section.style.display = "none";
      return;
    }
    section.style.display = "block";
    list.innerHTML = missing.map(a => `
      <div class="card missing">
        <span class="status-icon missing">✗</span>
        <div class="card-body">
          <div class="card-title">${a.title}</div>
          <div class="card-sub">${a.course_name}</div>
        </div>
        <div class="card-right">
          <div class="status missing">Was due ${formatDueDate(a.due_at)}</div>
        </div>
      </div>
    `).join("");
  }

  async function loadDay(dateStr) {
    hideError();
    document.getElementById("loading").style.display = "block";
    document.getElementById("current-date").textContent = formatDisplayDate(dateStr);

    try {
      const [dayRes, missingRes] = await Promise.all([
        fetch(`/api/day?date=${dateStr}`),
        fetch("/api/missing")
      ]);

      const day = await dayRes.json();
      const missingData = await missingRes.json();

      if (day.error) { showError(`Canvas error: ${day.error}`); return; }

      renderSchedule(day.schedule || []);
      renderAssignments(day.assignments || []);
      renderMissing(missingData.missing || []);
    } catch (err) {
      showError("Could not connect to the local server. Is app.py running?");
    } finally {
      document.getElementById("loading").style.display = "none";
    }
  }

  let currentDate = toDateStr(new Date());

  document.getElementById("prev-day").addEventListener("click", () => {
    currentDate = shiftDate(currentDate, -1);
    loadDay(currentDate);
  });

  document.getElementById("next-day").addEventListener("click", () => {
    currentDate = shiftDate(currentDate, 1);
    loadDay(currentDate);
  });

  document.getElementById("today-btn").addEventListener("click", () => {
    currentDate = toDateStr(new Date());
    loadDay(currentDate);
  });

  loadDay(currentDate);
```

- [ ] **Step 2: Verify the app works end-to-end**

```bash
python app.py
```

Open `http://localhost:5000`. With a valid `.env`:
- Schedule section shows today's Canvas calendar events with Join Zoom buttons
- Assignments section shows today's due assignments with correct status colors
- Missing section appears only if there are missing assignments
- Prev/next arrows navigate days; "Today" resets to current date

With an invalid token, the error banner should appear with a clear message.

- [ ] **Step 3: Run the full test suite one final time**

```bash
pytest tests/ -v
```

Expected: all 13 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: frontend JS — day navigation, rendering, error states"
```
