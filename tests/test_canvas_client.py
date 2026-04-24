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
