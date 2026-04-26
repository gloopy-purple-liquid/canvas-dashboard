from unittest.mock import patch, MagicMock
from canvas_client import CanvasClient


def make_client():
    return CanvasClient("https://canvas.test", "test-token")


@patch("canvas_client.requests.get")
def test_get_all_follows_pagination(mock_get):
    page1 = MagicMock()
    page1.json.return_value = [{"id": 1}, {"id": 2}]
    page1.raise_for_status = MagicMock()
    page1.headers = {"Link": '<https://canvas.test/api/v1/items?page=2>; rel="next"'}

    page2 = MagicMock()
    page2.json.return_value = [{"id": 3}]
    page2.raise_for_status = MagicMock()
    page2.headers = {"Link": ""}

    mock_get.side_effect = [page1, page2]

    client = make_client()
    results = client._get_all("/api/v1/items", {"per_page": "100"})

    assert len(results) == 3
    assert results[2]["id"] == 3
    assert mock_get.call_count == 2
    # Second call uses the next-page URL, no params
    assert mock_get.call_args_list[1][1]["params"] is None


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


@patch("canvas_client.requests.get")
def test_get_assignments_due_returns_clean_dicts(mock_get):
    mock_get.return_value.json.return_value = [
        {
            "id": "99",
            "title": "Math HW #12",
            "context_code": "course_789",
            "end_at": "2026-04-24T23:59:00Z",
            "assignment": {
                "id": 456,
                "course_id": 789,
                "due_at": "2026-04-24T23:59:00Z",
                "points_possible": 100,
            },
        },
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    items = client.get_assignments_due("2026-04-24", ["789"])

    assert len(items) == 1
    assert items[0]["title"] == "Math HW #12"
    assert items[0]["id"] == "456"
    assert items[0]["course_id"] == "789"
    assert items[0]["due_at"] == "2026-04-24T23:59:00Z"
    assert items[0]["points_possible"] == 100

    call_params = mock_get.call_args[1]["params"]
    assert ("type", "assignment") in call_params
    assert ("start_date", "2026-04-10T00:00:00Z") in call_params  # 14 days back (UTC tzoffset=0)
    assert ("end_date", "2026-04-25T23:59:59Z") in call_params    # end of day UTC + 1 day buffer
    assert ("context_codes[]", "course_789") in call_params


@patch("canvas_client.requests.get")
def test_resolve_student_id_uses_observee(mock_get):
    mock_get.return_value.json.return_value = [{"id": 42, "name": "Student"}]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    assert client._resolve_student_id() == "42"
    assert client._resolve_student_id() == "42"  # cached — no second API call
    assert mock_get.call_count == 1


@patch("canvas_client.requests.get")
def test_resolve_student_id_falls_back_to_self(mock_get):
    mock_get.return_value.json.return_value = []
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    assert client._resolve_student_id() == "self"


@patch("canvas_client.requests.get")
def test_get_submission_details_uses_student_id(mock_get):
    mock_get.return_value.json.return_value = {
        "grade": "94%",
        "score": 94.0,
        "workflow_state": "graded",
        "submission_comments": [
            {"comment": "Great work!", "author_name": "Mrs. Anderson"}
        ],
    }
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    client._student_id = "99"  # pre-set to avoid observees API call
    details = client.get_submission_details("789", "456")

    assert details["grade"] == "94%"
    assert details["score"] == 94.0
    assert details["submission_comments"][0]["comment"] == "Great work!"
    mock_get.assert_called_once_with(
        "https://canvas.test/api/v1/courses/789/assignments/456/submissions/99",
        headers={"Authorization": "Bearer test-token"},
        params={"include[]": "submission_comments"},
        timeout=10,
    )


@patch("canvas_client.requests.get")
def test_get_active_courses_caches_result(mock_get):
    mock_get.return_value.json.return_value = [{"id": 1, "name": "Mathematics"}]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    courses1 = client.get_active_courses()
    courses2 = client.get_active_courses()

    assert courses1 == courses2
    assert mock_get.call_count == 1  # second call served from cache



@patch("canvas_client.requests.get")
def test_get_missing_assignments_uses_student_id(mock_get):
    mock_get.return_value.json.return_value = [
        {"id": 10, "name": "History Quiz", "course_id": 1, "due_at": "2026-04-21T23:59:00Z"}
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    client._student_id = "99"  # pre-set to avoid observees API call
    missing = client.get_missing_assignments()

    assert len(missing) == 1
    assert missing[0]["name"] == "History Quiz"
    mock_get.assert_called_once_with(
        "https://canvas.test/api/v1/users/99/missing_submissions",
        headers={"Authorization": "Bearer test-token"},
        params={"per_page": 50},
        timeout=10,
    )
