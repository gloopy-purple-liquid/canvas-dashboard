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
