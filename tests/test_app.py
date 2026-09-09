import json
from unittest.mock import patch, MagicMock
import pytest
from app import app as flask_app


@patch("app.canvas_client")
def test_api_day_returns_schedule_and_assignments(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Mathematics 6 Q1-Q1"}]
    mock_client.get_assignments_due.return_value = [
        {"id": "456", "course_id": "1", "title": "Math HW #12",
         "due_at": "2026-04-22T23:59:00Z", "points_possible": 100},
    ]
    mock_client.get_front_page.return_value = {"title": "W01 04/20 - 04/24 Home", "body": "<html/>"}
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = "https://z/1"
    # module-level parse returns a live class + no tasks for this body
    with patch("app.parse_homepage_day", return_value={
        "live_class": {"title": "Live Class", "time": "10:00 AM"}, "tasks": []
    }):
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            resp = c.get("/api/day?date=2026-04-22")  # a Wednesday in that range

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["homepage_available"] is True
    assert len(data["schedule"]) == 1
    assert data["schedule"][0]["title"] == "Mathematics 6 — Live Class"
    assert data["schedule"][0]["zoom_url"] == "https://z/1"
    assert len(data["assignments"]) == 1
    assert data["assignments"][0]["title"] == "Math HW #12"


@patch("app.canvas_client")
def test_api_day_deduplicates_assignments(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Math"}]
    mock_client.get_assignments_due.return_value = [
        {"id": "456", "course_id": "1", "title": "HW #12", "due_at": "2026-04-24T23:59:00Z", "points_possible": 10},
        {"id": "456", "course_id": "1", "title": "HW #12", "due_at": "2026-04-24T23:59:00Z", "points_possible": 10},
    ]
    mock_client.get_front_page.return_value = None
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = None

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day?date=2026-04-24")

    data = resp.get_json()
    assert len(data["assignments"]) == 1


@patch("app.canvas_client")
def test_api_submissions_returns_details(mock_client):
    mock_client._resolve_student_id.return_value = "99"
    mock_client.get_submission_details.return_value = {
        "workflow_state": "graded",
        "grade": "94%",
        "score": 94.0,
        "submitted_at": "2026-04-24T20:00:00Z",
        "submission_comments": [{"comment": "Great work!", "author_name": "Mrs. Anderson"}],
    }

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.post(
            "/api/submissions",
            data=json.dumps([{"id": "456", "course_id": "1"}]),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["id"] == "456"
    assert data[0]["grade"] == "94%"
    assert data[0]["submitted"] is True
    assert data[0]["graded"] is True
    assert data[0]["comments"] == ["Great work!"]
    mock_client.get_submission_details.assert_called_once_with("1", "456")


@patch("app.canvas_client")
def test_api_day_defaults_to_today(mock_client):
    mock_client.get_active_courses.return_value = []
    mock_client.get_assignments_due.return_value = []
    mock_client.get_front_page.return_value = None
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = None

    from datetime import date
    today = date.today().isoformat()

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day")

    data = resp.get_json()
    assert data["date"] == today


@patch("app.canvas_client")
def test_api_day_builds_tasks_with_status(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 11902, "name": "Humanities 6 Q1"}]
    mock_client.get_assignments_due.return_value = []
    mock_client.get_front_page.return_value = {"title": "W01 09/07 - 09/11 Home", "body": "<html/>"}
    mock_client.get_module_items_map.return_value = {
        "2011877": {"type": "Assignment", "content_id": 771602, "title": "i-Ready", "due_at": None, "points": 15},
        "999": {"type": "Page", "content_id": None, "title": "Info", "due_at": None, "points": None},
    }
    mock_client.get_zoom_url.return_value = None
    mock_client._resolve_student_id.return_value = "30796"
    mock_client.get_submission_details.return_value = {"workflow_state": "graded", "grade": "A", "score": 15}
    parsed = {"live_class": None, "tasks": [
        {"raw_title": "i-Ready", "url": "u1", "item_id": "2011877", "type_label": "start", "optional": False},
        {"raw_title": "Info", "url": "u2", "item_id": "999", "type_label": "other", "optional": True},
    ]}
    with patch("app.parse_homepage_day", return_value=parsed):
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            resp = c.get("/api/day?date=2026-09-08")

    data = resp.get_json()
    assert data["homepage_available"] is True
    assert len(data["tasks"]) == 1
    block = data["tasks"][0]
    assert block["course_id"] == "11902"
    items = {i["title"]: i for i in block["items"]}
    assert items["i-Ready"]["submittable"] is True
    assert items["i-Ready"]["graded"] is True
    assert items["i-Ready"]["grade"] == "A"
    assert items["Info"]["optional"] is True
    assert items["Info"]["submittable"] is False


@patch("app.canvas_client")
def test_api_day_homepage_unavailable_for_other_week(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Math 6 Q1"}]
    mock_client.get_assignments_due.return_value = []
    mock_client.get_front_page.return_value = {"title": "W01 09/07 - 09/11 Home", "body": "<html/>"}
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = None
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day?date=2026-09-22")  # outside 09/07-09/11

    data = resp.get_json()
    assert data["homepage_available"] is False
    assert data["schedule"] == []
    assert data["tasks"] == []


@patch("app.canvas_client")
def test_api_day_survives_unparseable_homepage_week_range(mock_client):
    # A front-page title with an invalid date (month 13) makes the real
    # parse_week_range raise ValueError inside _course_homepage. That must
    # be isolated to this one course, not blow up the whole /api/day request.
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Broken Course"}]
    mock_client.get_assignments_due.return_value = [
        {"id": "456", "course_id": "1", "title": "Still Due HW",
         "due_at": "2026-04-22T23:59:00Z", "points_possible": 100},
    ]
    mock_client.get_front_page.return_value = {"title": "W01 13/45 - 13/46 Home", "body": "<html/>"}
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = None

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        resp = c.get("/api/day?date=2026-04-22")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schedule"] == []
    assert data["tasks"] == []
    assert len(data["assignments"]) == 1
    assert data["assignments"][0]["title"] == "Still Due HW"


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


def test_time_sort_key_sorts_unparseable_last():
    from app import _time_sort_key
    assert _time_sort_key("") > _time_sort_key("11:00 AM")
    assert _time_sort_key("not a time") > _time_sort_key("12:00 AM")
