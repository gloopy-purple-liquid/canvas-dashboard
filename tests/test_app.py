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
