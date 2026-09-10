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
    # tzoffset=420 → UTC-7 (Pacific), so 14:00Z = 7:00 AM Pacific
    schedule = client.get_schedule("2026-04-24", ["1", "2"], tzoffset=420)

    assert len(schedule) == 1
    assert schedule[0]["title"] == "Mathematics — Period 1"
    assert schedule[0]["zoom_url"] == "https://zoom.us/j/99999"
    assert schedule[0]["time"] == "7:00 AM"

    call_params = mock_get.call_args[1]["params"]
    assert ("type", "event") in call_params
    # UTC-7: April 24 local = April 24 07:00Z to April 25 06:59:59Z
    assert ("start_date", "2026-04-24T07:00:00Z") in call_params
    assert ("end_date", "2026-04-25T06:59:59Z") in call_params
    assert ("context_codes[]", "course_1") in call_params
    assert ("context_codes[]", "course_2") in call_params


@patch("canvas_client.requests.get")
def test_get_schedule_uses_user_timezone_for_time(mock_get):
    """Times display in the user's timezone, not the server's."""
    mock_get.return_value.json.return_value = [
        {"id": "20", "title": "English", "start_at": "2026-04-24T16:00:00Z",
         "location_name": "", "description": ""},
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    # UTC-7: 16:00Z = 9:00 AM local
    schedule = client.get_schedule("2026-04-24", ["1"], tzoffset=420)
    assert schedule[0]["time"] == "9:00 AM"


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
def test_get_schedule_unescapes_html_entities_in_zoom_url(mock_get):
    """Canvas HTML descriptions encode & as &amp; — we must unescape before returning."""
    mock_get.return_value.json.return_value = [
        {
            "id": "13",
            "title": "Math",
            "start_at": "2026-04-24T16:00:00Z",
            "location_name": "Room 101",
            "description": '<a href="https://zoom.us/j/12345?pwd=abc&amp;uname=xyz">Join</a>',
        }
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    schedule = client.get_schedule("2026-04-24", ["1"])

    assert schedule[0]["zoom_url"] == "https://zoom.us/j/12345?pwd=abc&uname=xyz"


@patch("canvas_client.requests.get")
def test_get_schedule_adds_https_to_bare_location_name(mock_get):
    """location_name may omit the protocol — we prepend https:// so the Join button appears."""
    mock_get.return_value.json.return_value = [
        {
            "id": "14",
            "title": "Science",
            "start_at": "2026-04-24T16:00:00Z",
            "location_name": "zoom.us/j/99999",
            "description": "",
        }
    ]
    mock_get.return_value.raise_for_status = MagicMock()

    client = make_client()
    schedule = client.get_schedule("2026-04-24", ["1"])

    assert schedule[0]["zoom_url"] == "https://zoom.us/j/99999"


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


from datetime import date
from canvas_client import classify_task_prefix, extract_class_time, parse_week_range


def test_classify_task_prefix_due():
    assert classify_task_prefix("🗓️ Due Today: W01 - It's In The Syllabus") == ("due", False)

def test_classify_task_prefix_start_and_continue():
    assert classify_task_prefix("Start:  6W01 - Fall i-Ready Reading Diagnostic") == ("start", False)
    assert classify_task_prefix("Continue: 6W01 - Fall i-Ready Reading Diagnostic") == ("continue", False)

def test_classify_task_prefix_optional():
    assert classify_task_prefix("🌀 Optional: 6 - Class Name Suggestions") == ("other", True)

def test_classify_task_prefix_reading_and_reminder():
    assert classify_task_prefix("📖 Independent Reading: Read for at least 20 minutes.") == ("reading", False)
    assert classify_task_prefix("⚠️ Reminder: Select a novel for daily independent reading.") == ("reminder", False)

def test_classify_task_prefix_other():
    assert classify_task_prefix("Live Class Recordings & Weekly Assignments") == ("other", False)

def test_extract_class_time_variants():
    assert extract_class_time("Attend: Live Class @ 10am.") == "10:00 AM"
    assert extract_class_time("Attend Live Class @ 10 am.") == "10:00 AM"
    assert extract_class_time("Live Class @ 9:30am") == "9:30 AM"
    assert extract_class_time("Attend Live Class @ 1 pm") == "1:00 PM"

def test_extract_class_time_missing():
    assert extract_class_time("Attend: Live Class") == ""

def test_parse_week_range_ok():
    assert parse_week_range("6W01 --> 09/07 - 09/11 - Humanities Homepage", date(2026, 9, 8)) == (date(2026, 9, 7), date(2026, 9, 11))

def test_parse_week_range_none():
    assert parse_week_range("Pod Squad Homepage", date(2026, 9, 8)) is None

def test_parse_week_range_year_boundary():
    assert parse_week_range("W18 12/29 - 01/02 Home", date(2026, 12, 30)) == (date(2026, 12, 29), date(2027, 1, 2))


import os
from canvas_client import parse_homepage_day

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "homepage_sample.html")

def _body():
    with open(FIXTURE) as f:
        return f.read()

def test_parse_homepage_day_tuesday_live_class_and_tasks():
    r = parse_homepage_day(_body(), 1)  # Tuesday -> #tab2
    assert r["live_class"] == {"title": "Live Class", "time": "10:00 AM"}
    titles = [(t["raw_title"], t["type_label"], t["optional"], t["item_id"]) for t in r["tasks"]]
    assert ("6W01 - Fall i-Ready Reading Diagnostic", "start", False, "2011877") in titles
    assert ("6 - Class Name Suggestions", "other", True, "2011879") in titles
    # the "Need more help?" prose line is not a task
    assert all("Need more help" not in t["raw_title"] for t in r["tasks"])

def test_parse_homepage_day_wednesday_due_and_reminder():
    r = parse_homepage_day(_body(), 2)  # Wednesday -> #tab3
    assert r["live_class"] is None
    labels = {t["type_label"] for t in r["tasks"]}
    assert labels == {"due", "reminder"}
    due = next(t for t in r["tasks"] if t["type_label"] == "due")
    assert due["item_id"] == "2011878"
    reminder = next(t for t in r["tasks"] if t["type_label"] == "reminder")
    assert reminder["item_id"] is None  # reminder has no module link

def test_parse_homepage_day_monday_no_tasks():
    r = parse_homepage_day(_body(), 0)
    assert r == {"live_class": None, "tasks": []}

def test_parse_homepage_day_weekend_empty():
    assert parse_homepage_day(_body(), 5) == {"live_class": None, "tasks": []}

def test_parse_homepage_day_none_body():
    assert parse_homepage_day(None, 1) == {"live_class": None, "tasks": []}


from canvas_client import enrich_tasks

def test_enrich_tasks_marks_assignment_submittable():
    tasks = [{"raw_title": "i-Ready", "url": "u1", "item_id": "2011877", "type_label": "start", "optional": False}]
    module_map = {"2011877": {"type": "Assignment", "content_id": 771602, "title": "i-Ready", "due_at": None, "points": 15}}
    out = enrich_tasks(tasks, module_map)
    assert out == [{
        "title": "i-Ready", "url": "u1", "type_label": "start", "optional": False,
        "submittable": True, "content_id": "771602",
        "submitted": False, "graded": False, "grade": None,
    }]

def test_enrich_tasks_page_not_submittable():
    tasks = [{"raw_title": "Resources", "url": "u2", "item_id": "999", "type_label": "other", "optional": False}]
    module_map = {"999": {"type": "Page", "content_id": None, "title": "Resources", "due_at": None, "points": None}}
    out = enrich_tasks(tasks, module_map)
    assert out[0]["submittable"] is False
    assert out[0]["content_id"] is None

def test_enrich_tasks_unknown_item_not_submittable():
    tasks = [{"raw_title": "Reminder", "url": None, "item_id": None, "type_label": "reminder", "optional": False}]
    out = enrich_tasks(tasks, {})
    assert out[0]["submittable"] is False
    assert out[0]["title"] == "Reminder"


@patch("canvas_client.requests.get")
def test_get_front_page_returns_title_and_body(mock_get):
    mock_get.return_value.json.return_value = {"title": "W01 09/07 - 09/11 Home", "body": "<div id='tab1'></div>"}
    mock_get.return_value.raise_for_status = MagicMock()
    client = make_client()
    fp = client.get_front_page("11902")
    assert fp == {"title": "W01 09/07 - 09/11 Home", "body": "<div id='tab1'></div>"}
    # cached: second call makes no new request
    client.get_front_page("11902")
    assert mock_get.call_count == 1

@patch("canvas_client.requests.get")
def test_get_front_page_none_on_error(mock_get):
    import requests as req
    mock_get.return_value.raise_for_status.side_effect = req.HTTPError("404")
    client = make_client()
    assert client.get_front_page("11902") is None

@patch("canvas_client.requests.get")
def test_get_module_items_map_builds_index(mock_get):
    mock_get.return_value.json.return_value = [
        {"id": 1, "name": "M1", "items": [
            {"id": 2011877, "type": "Assignment", "content_id": 771602, "title": "i-Ready",
             "content_details": {"due_at": "2026-09-12T06:59:59Z", "points_possible": 15.0}},
            {"id": 2011909, "type": "Page", "content_id": None, "title": "Resources", "content_details": {}},
        ]},
    ]
    mock_get.return_value.raise_for_status = MagicMock()
    mock_get.return_value.headers = {"Link": ""}
    client = make_client()
    m = client.get_module_items_map("11902")
    assert m["2011877"] == {"type": "Assignment", "content_id": 771602, "title": "i-Ready",
                            "due_at": "2026-09-12T06:59:59Z", "points": 15.0}
    assert m["2011909"]["type"] == "Page"

@patch("canvas_client.requests.get")
def test_get_zoom_url_finds_zoom_tab(mock_get):
    mock_get.return_value.json.return_value = [
        {"label": "Home", "full_url": "https://school.instructure.com/courses/11902"},
        {"label": "Zoom", "full_url": "https://school.instructure.com/courses/11902/external_tools/1039"},
    ]
    mock_get.return_value.raise_for_status = MagicMock()
    client = make_client()
    assert client.get_zoom_url("11902") == "https://school.instructure.com/courses/11902/external_tools/1039"

@patch("canvas_client.requests.get")
def test_get_zoom_url_none_when_absent(mock_get):
    mock_get.return_value.json.return_value = [{"label": "Home", "full_url": "x"}]
    mock_get.return_value.raise_for_status = MagicMock()
    client = make_client()
    assert client.get_zoom_url("11902") is None


# --- live-class detection across varied teacher phrasings (bugfix) ---

def test_extract_class_time_accepts_at_and_bare_times():
    assert extract_class_time("Attend POD Squad at 9:00 AM. Join via Zoom") == "9:00 AM"
    assert extract_class_time("Come to Live Class at 10:00 am.") == "10:00 AM"
    assert extract_class_time("ATTEND LIVE CLASS @ 11 A.M.") == "11:00 AM"

def test_parse_homepage_day_detects_attend_without_live_class_keyword():
    # Pod Squad style: "attend" + a time + Zoom, but no literal "live class"
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li>Attend POD Squad at 9:00 AM. Join via the Zoom Link</li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] == {"title": "Live Class", "time": "9:00 AM"}
    assert r["tasks"] == []

def test_parse_homepage_day_detects_live_class_without_attend_keyword():
    # Science style: "live class" + a time, but no "attend"
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li>Task 1: Come to Live Class at 10:00 am. Intros and skills.</li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] == {"title": "Live Class", "time": "10:00 AM"}

def test_parse_homepage_day_attend_classes_checklist_is_not_live_class():
    # "Attend Classes" checklist item with NO time is not the live class;
    # it links to a module item, so it stays a task.
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li>Complete the Daily Big 3: '
            '<a href="https://school.instructure.com/courses/11386/modules/items/500">Attend Classes</a></li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] is None
    assert len(r["tasks"]) == 1
    assert r["tasks"][0]["item_id"] == "500"

def test_parse_homepage_day_timed_task_without_attend_is_not_live_class():
    # A task that merely mentions a time (no attend/live/zoom-join) stays a task.
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li>\U0001f5d3️ Due Today: '
            '<a href="https://school.instructure.com/courses/1/modules/items/77">Quiz opens at 3:00 pm</a></li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] is None
    assert len(r["tasks"]) == 1
    assert r["tasks"][0]["type_label"] == "due"

def test_parse_homepage_day_detects_live_lesson_phrasing():
    # LAUNCH style: "Attend ... live lesson ... zoom"; time is a range with no am/pm
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li>Attend 9-10 live lesson (Recap of the last 2 lessons) '
            '<a href="https://x/rec">click here for recording</a> zoom</li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] == {"title": "Live Class", "time": ""}
    assert r["tasks"] == []

def test_parse_homepage_day_live_class_recordings_is_not_live_class():
    # "Live Class Recordings" contains "live class" but is a resource link, not the class
    body = ('<div id="tab1" class="tab-content"><ul>'
            '<li><a href="https://school.instructure.com/courses/1/modules/items/9">'
            'Live Class Recordings &amp; Weekly Assignments</a></li>'
            '</ul></div>')
    r = parse_homepage_day(body, 0)
    assert r["live_class"] is None
    assert len(r["tasks"]) == 1
    assert r["tasks"][0]["item_id"] == "9"
