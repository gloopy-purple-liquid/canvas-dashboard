# Homepage-Driven Schedule & Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse each Canvas course's weekly wiki homepage to surface the day's live Zoom class and task list (with optional tasks and per-task submission status), replacing the empty calendar-based schedule.

**Architecture:** Add pure parsing functions to `canvas_client.py` that turn already-fetched homepage HTML into structured `live_class` + `tasks`, plus three cached networked getters (`get_front_page`, `get_module_items_map`, `get_zoom_url`). `app.py`'s `/api/day` orchestrates per-course fetching (parallelized), enriches tasks with module metadata, resolves submission status, and returns `schedule` + `tasks` + `homepage_available`. The frontend adds a "Today's Tasks" section.

**Tech Stack:** Python 3, Flask, `requests`, `pytest` (backend). Vanilla JS + CSS in a single `templates/index.html` (no build step).

## Global Constraints

- No new third-party dependencies — use `re`, `html`, stdlib `datetime`, and existing `requests`/`Flask`.
- All caches use a **300-second TTL**, matching `get_active_courses`.
- Frontend is a single `templates/index.html`, no build step; **all interpolated strings must pass through `esc()`** (XSS safety).
- Token is a parent/**observer** token; submission lookups go through the existing `_resolve_student_id()` (returns the observee id).
- Weekday convention: `date.weekday()` → 0=Monday … 4=Friday; homepage tabs are `#tab1`(Mon)…`#tab5`(Fri).
- `type_label` domain: `{"due", "start", "continue", "reading", "reminder", "other"}`.
- Run tests with `python -m pytest` from the repo root (the repo's `venv` is present; `pytest` is installed).

---

### Task 1: Pure text-classification helpers

**Files:**
- Modify: `canvas_client.py` (add module-level functions near the top, after the existing `_format_time`)
- Test: `tests/test_canvas_client.py` (append)

**Interfaces:**
- Consumes: nothing (pure, stdlib only).
- Produces:
  - `classify_task_prefix(text: str) -> tuple[str, bool]` → `(type_label, optional)`.
  - `extract_class_time(text: str) -> str` → e.g. `"10:00 AM"` or `""`.
  - `parse_week_range(title: str, ref_date: date) -> tuple[date, date] | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_canvas_client.py -k "classify_task_prefix or extract_class_time or parse_week_range" -v`
Expected: FAIL with `ImportError: cannot import name 'classify_task_prefix'`

- [ ] **Step 3: Write minimal implementation**

In `canvas_client.py`, after the `_format_time` function add (`re`, `html`, and `date as _date` are already imported):

```python
def classify_task_prefix(text):
    low = text.lower()
    optional = "🌀" in text or "optional:" in low
    if "due today" in low or "🗓" in text:
        label = "due"
    elif low.startswith("start"):
        label = "start"
    elif low.startswith("continue"):
        label = "continue"
    elif "independent reading" in low or "📖" in text:
        label = "reading"
    elif "reminder" in low or "⚠" in text:
        label = "reminder"
    else:
        label = "other"
    return label, optional


def extract_class_time(text):
    m = re.search(r'@\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?', text, re.I)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = m.group(2) or "00"
    ampm = m.group(3).upper() + "M"
    return f"{hour}:{minute} {ampm}"


def parse_week_range(title, ref_date):
    m = re.search(r'(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})', title or "")
    if not m:
        return None
    y = ref_date.year
    start = _date(y, int(m.group(1)), int(m.group(2)))
    end = _date(y, int(m.group(3)), int(m.group(4)))
    return (start, end)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_canvas_client.py -k "classify_task_prefix or extract_class_time or parse_week_range" -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: add homepage text-classification helpers"
```

---

### Task 2: `parse_homepage_day` — HTML → live_class + tasks

**Files:**
- Modify: `canvas_client.py` (add after the Task 1 helpers)
- Create: `tests/fixtures/homepage_sample.html`
- Test: `tests/test_canvas_client.py` (append)

**Interfaces:**
- Consumes: `classify_task_prefix`, `extract_class_time` (Task 1).
- Produces:
  - `parse_homepage_day(body: str | None, weekday: int) -> dict` returning
    `{"live_class": {"title": str, "time": str} | None, "tasks": [ {"raw_title": str, "url": str | None, "item_id": str | None, "type_label": str, "optional": bool} ]}`.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/homepage_sample.html` (mirrors the real school template: `#tab1`…`#tab5` day tabs, `<li>` task items with text prefixes and `modules/items/{id}` links):

```html
<div id="cc-homepage-announcements">intro prose</div>
<ul id="fca-tab-menu">
  <li><a href="#tab1">Monday</a></li>
  <li><a href="#tab2">Tuesday</a></li>
  <li><a href="#tab3">Wednesday</a></li>
  <li><a href="#tab4">Thursday</a></li>
  <li><a href="#tab5">Friday</a></li>
</ul>
<div id="tab1" class="tab-content">
  <ul><li><span>Labor Day - No School, no tasks today! :-)</span></li></ul>
</div>
<div id="tab2" class="tab-content">
  <ul>
    <li><strong><a title="Link" href="$CANVAS_COURSE_REFERENCE$/modules/items/gabc?wrap=1"></a></strong>Attend: Live Class @ 10am. To join, click the "Zoom" link.</li>
    <li><span>Need more help?&nbsp; Read <a href="https://help.example.com/zoom">"How do I join?"</a></span></li>
    <li><strong>Start:&nbsp; <a href="https://school.instructure.com/courses/11902/modules/items/2011877" target="_blank">6W01 - Fall i-Ready Reading Diagnostic</a></strong></li>
    <li><strong><span>🌀</span>Optional: <a href="https://school.instructure.com/courses/11902/modules/items/2011879" target="_blank">6 - Class Name Suggestions</a></strong></li>
  </ul>
</div>
<div id="tab3" class="tab-content">
  <ul>
    <li><strong>🗓️ Due Today: <a href="https://school.instructure.com/courses/11902/modules/items/2011878">W01 - It's In The Syllabus</a></strong></li>
    <li><strong>⚠️ Reminder: Select a novel for daily independent reading.</strong></li>
  </ul>
</div>
<div id="tab4" class="tab-content"><ul></ul></div>
<div id="tab5" class="tab-content"><ul></ul></div>
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_canvas_client.py -k parse_homepage_day -v`
Expected: FAIL with `ImportError: cannot import name 'parse_homepage_day'`

- [ ] **Step 4: Write minimal implementation**

In `canvas_client.py`, after the Task 1 helpers:

```python
def _clean_text(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_homepage_day(body, weekday):
    result = {"live_class": None, "tasks": []}
    if not body or weekday < 0 or weekday > 4:
        return result
    tab_re = re.compile(
        r'<div id="tab%d".*?>(.*?)(?=<div id="tab\d+"|$)' % (weekday + 1), re.S
    )
    m = tab_re.search(body)
    if not m:
        return result
    segment = m.group(1)
    for li in re.findall(r"<li\b.*?</li>", segment, re.S):
        text = _clean_text(li)
        if not text:
            continue
        if "live class" in text.lower() and "attend" in text.lower():
            if result["live_class"] is None:
                result["live_class"] = {"title": "Live Class", "time": extract_class_time(text)}
            continue
        item_m = re.search(r'href="[^"]*?/courses/\d+/modules/items/(\w+)"', li)
        item_id = item_m.group(1) if item_m else None
        href_m = re.search(r'<a\b[^>]*href="([^"]+)"', li)
        url = html.unescape(href_m.group(1)) if href_m else None
        link_m = re.search(r"<a\b[^>]*>(.*?)</a>", li, re.S)
        link_text = _clean_text(link_m.group(1)) if link_m else ""
        type_label, optional = classify_task_prefix(text)
        if item_id is None and not optional and type_label == "other":
            continue
        result["tasks"].append({
            "raw_title": link_text or text,
            "url": url,
            "item_id": item_id,
            "type_label": type_label,
            "optional": optional,
        })
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_canvas_client.py -k parse_homepage_day -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py tests/fixtures/homepage_sample.html
git commit -m "feat: parse homepage day tab into live class and tasks"
```

---

### Task 3: `enrich_tasks` — attach module metadata + submittability

**Files:**
- Modify: `canvas_client.py` (add after `parse_homepage_day`)
- Test: `tests/test_canvas_client.py` (append)

**Interfaces:**
- Consumes: task dicts from `parse_homepage_day` (Task 2); a `module_map` shaped like `get_module_items_map`'s output (Task 4): `{ item_id_str: {"type": str, "content_id": int|None, "title": str, "due_at": str|None, "points": num|None} }`.
- Produces:
  - `enrich_tasks(tasks: list[dict], module_map: dict) -> list[dict]` where each item is
    `{"title": str, "url": str|None, "type_label": str, "optional": bool, "submittable": bool, "content_id": str|None, "submitted": False, "graded": False, "grade": None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_canvas_client.py -k enrich_tasks -v`
Expected: FAIL with `ImportError: cannot import name 'enrich_tasks'`

- [ ] **Step 3: Write minimal implementation**

In `canvas_client.py`, after `parse_homepage_day`:

```python
def enrich_tasks(tasks, module_map):
    enriched = []
    for t in tasks:
        info = module_map.get(t["item_id"]) if t["item_id"] else None
        submittable = bool(info and info.get("type") in ("Assignment", "Quiz"))
        content_id = (
            str(info["content_id"])
            if submittable and info.get("content_id") is not None
            else None
        )
        enriched.append({
            "title": t["raw_title"],
            "url": t["url"],
            "type_label": t["type_label"],
            "optional": t["optional"],
            "submittable": submittable,
            "content_id": content_id,
            "submitted": False,
            "graded": False,
            "grade": None,
        })
    return enriched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_canvas_client.py -k enrich_tasks -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: enrich homepage tasks with module metadata"
```

---

### Task 4: Cached networked getters (`get_front_page`, `get_module_items_map`, `get_zoom_url`)

**Files:**
- Modify: `canvas_client.py` (`CanvasClient.__init__` + three new methods)
- Test: `tests/test_canvas_client.py` (append)

**Interfaces:**
- Consumes: existing `self._get`, `self._get_all`.
- Produces (all on `CanvasClient`, all cached 300s):
  - `get_front_page(course_id) -> {"title": str, "body": str} | None` (`None` on any fetch error / no front page).
  - `get_module_items_map(course_id) -> dict` (shape per Task 3's `module_map`).
  - `get_zoom_url(course_id) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_canvas_client.py -k "front_page or module_items_map or zoom_url" -v`
Expected: FAIL with `AttributeError: 'CanvasClient' object has no attribute 'get_front_page'`

- [ ] **Step 3: Write minimal implementation**

In `CanvasClient.__init__`, after `self._courses_cache_ts = 0`, add:

```python
        self._front_page_cache = {}
        self._module_map_cache = {}
        self._zoom_cache = {}
```

Add these methods to `CanvasClient`:

```python
    def _cache_get(self, cache, key):
        entry = cache.get(key)
        if entry is not None and time.time() - entry[1] < 300:
            return True, entry[0]
        return False, None

    def get_front_page(self, course_id):
        cid = str(course_id)
        hit, value = self._cache_get(self._front_page_cache, cid)
        if hit:
            return value
        try:
            fp = self._get(f"/api/v1/courses/{cid}/front_page")
            value = {"title": fp.get("title", ""), "body": fp.get("body") or ""}
        except Exception:
            value = None
        self._front_page_cache[cid] = (value, time.time())
        return value

    def get_module_items_map(self, course_id):
        cid = str(course_id)
        hit, value = self._cache_get(self._module_map_cache, cid)
        if hit:
            return value
        params = [("include[]", "items"), ("include[]", "content_details"), ("per_page", "50")]
        result = {}
        try:
            for module in self._get_all(f"/api/v1/courses/{cid}/modules", params):
                for item in module.get("items", []):
                    details = item.get("content_details") or {}
                    result[str(item.get("id"))] = {
                        "type": item.get("type"),
                        "content_id": item.get("content_id"),
                        "title": item.get("title", ""),
                        "due_at": details.get("due_at"),
                        "points": details.get("points_possible"),
                    }
        except Exception:
            result = {}
        self._module_map_cache[cid] = (result, time.time())
        return result

    def get_zoom_url(self, course_id):
        cid = str(course_id)
        hit, value = self._cache_get(self._zoom_cache, cid)
        if hit:
            return value
        value = None
        try:
            for tab in self._get(f"/api/v1/courses/{cid}/tabs"):
                if (tab.get("label") or "").strip().lower() == "zoom":
                    value = tab.get("full_url")
                    break
        except Exception:
            value = None
        self._zoom_cache[cid] = (value, time.time())
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_canvas_client.py -k "front_page or module_items_map or zoom_url" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add canvas_client.py tests/test_canvas_client.py
git commit -m "feat: add cached front-page, module-map, and zoom-url getters"
```

---

### Task 5: `/api/day` integration — schedule + tasks + homepage_available

**Files:**
- Modify: `app.py` (imports, new helpers `_short_course_name` / `_course_homepage` / `_resolve_task_status`, and the `api_day` handler)
- Test: `tests/test_app.py` (update existing `/api/day` tests + add new ones)

**Interfaces:**
- Consumes: `parse_week_range`, `parse_homepage_day`, `enrich_tasks`, `get_front_page`, `get_module_items_map`, `get_zoom_url`, `get_submission_details`, `_resolve_student_id` (Tasks 1–4 + existing).
- Produces: `/api/day` JSON gains `homepage_available: bool` and `tasks: [{"course_id","course_name","items":[…]}]`; `schedule` entries now come from homepage live classes (+ manual config). Each task item carries `submitted/graded/grade`.

- [ ] **Step 1: Update existing `/api/day` tests and add new ones**

The current `/api/day` no longer calls `get_schedule`. Edit `tests/test_app.py`:

Replace `test_api_day_returns_schedule_and_assignments` with:

```python
@patch("app.canvas_client")
def test_api_day_returns_schedule_and_assignments(mock_client):
    mock_client.get_active_courses.return_value = [{"id": 1, "name": "Mathematics 6 Q1-Q1"}]
    mock_client.get_assignments_due.return_value = [
        {"id": "456", "course_id": "1", "title": "Math HW #12",
         "due_at": "2026-04-24T23:59:00Z", "points_possible": 100},
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
```

In `test_api_day_deduplicates_assignments` and `test_api_day_defaults_to_today`, remove the `mock_client.get_schedule.return_value = []` lines and add:

```python
    mock_client.get_front_page.return_value = None
    mock_client.get_module_items_map.return_value = {}
    mock_client.get_zoom_url.return_value = None
```

Add new tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py -k api_day -v`
Expected: FAIL (e.g. `KeyError: 'homepage_available'` / assertions on `schedule` / `tasks`).

- [ ] **Step 3: Write the implementation**

In `app.py`, update the import line:

```python
from canvas_client import CanvasClient, parse_week_range, parse_homepage_day, enrich_tasks
```

Add module-level helpers (after `_time_sort_key`):

```python
def _short_course_name(name):
    return (name or "").split(" Q1")[0].strip() or (name or "")


def _course_homepage(cid, weekday, ref_date):
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
```

Now rewrite the body of `api_day`. Replace the schedule/assignments fetch block and the schedule-assembly block. The full handler becomes:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS (all, including the updated `/api/day` tests and the two new ones)

- [ ] **Step 5: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions across `test_app.py` + `test_canvas_client.py`)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: serve homepage-derived schedule and tasks from /api/day"
```

---

### Task 6: Frontend — Today's Tasks section + homepage note

**Files:**
- Modify: `templates/index.html` (new section markup, `renderTasks`, `loadDay`/`clearDay` wiring, homepage note, CSS)

**Interfaces:**
- Consumes: `/api/day` response `tasks` + `homepage_available` (Task 5); existing `esc()`, `renderSchedule`, `loadDay`.
- Produces: rendered "📋 Today's Tasks" section; no new backend contract.

- [ ] **Step 1: Add the section markup**

In `templates/index.html`, after the `assignments-section` block and before `missing-section`, add:

```html
  <section id="tasks-section">
    <div class="section-label">📋 Today's Tasks</div>
    <div id="homepage-note" class="empty" style="display:none">Weekly homepage is only available for the current week.</div>
    <div id="tasks-list"></div>
  </section>
```

- [ ] **Step 2: Add CSS**

In the `<style>` block, add:

```css
    .task-course { margin: 14px 0 6px; font-weight: 600; color: #2c3e50; }
    .task-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
                border: 1px solid #e5e8ec; border-radius: 8px; margin-bottom: 6px; background: #fff; }
    .task-row.optional { opacity: 0.55; }
    .task-row .t-title { flex: 1; }
    .task-row .t-title a { color: inherit; text-decoration: none; }
    .task-row .t-title a:hover { text-decoration: underline; }
    .task-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
                color: #7f8c8d; border: 1px solid #dfe4e8; border-radius: 6px; padding: 1px 6px; }
    .task-status { width: 18px; text-align: center; font-weight: 700; }
    .task-status.done { color: #27ae60; }
    .task-status.todo { color: #e74c3c; }
    .task-status.info { color: #bdc3c7; }
```

- [ ] **Step 3: Add `renderTasks` and wire it in**

Add this function near `renderAssignments`:

```javascript
  const TASK_TAGS = { due: "Due Today", start: "Start", continue: "Continue", reading: "Reading", reminder: "Reminder", other: "" };

  function renderTasks(taskBlocks, homepageAvailable) {
    const note = document.getElementById("homepage-note");
    const list = document.getElementById("tasks-list");
    if (!homepageAvailable) {
      note.style.display = "block";
      list.innerHTML = "";
      return;
    }
    note.style.display = "none";
    if (!taskBlocks.length) {
      list.innerHTML = '<div class="empty">No tasks today.</div>';
      return;
    }
    list.innerHTML = taskBlocks.map(block => {
      const rows = block.items.map(it => {
        let statusClass = "info", statusChar = "•";
        if (it.submittable) {
          if (it.submitted || it.graded) { statusClass = "done"; statusChar = "✓"; }
          else { statusClass = "todo"; statusChar = "!"; }
        }
        const tag = TASK_TAGS[it.type_label] || "";
        const tagHtml = tag ? `<span class="task-tag">${esc(tag)}</span>` : "";
        const titleHtml = it.url
          ? `<a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
          : esc(it.title);
        return `
          <div class="task-row${it.optional ? ' optional' : ''}">
            <span class="task-status ${statusClass}">${statusChar}</span>
            <div class="t-title">${titleHtml}</div>
            ${tagHtml}
          </div>`;
      }).join("");
      return `<div class="task-course">${esc(block.course_name)}</div>${rows}`;
    }).join("");
  }
```

In `loadDay`, after `renderSchedule(day.schedule || []);` add:

```javascript
      renderTasks(day.tasks || [], day.homepage_available !== false);
```

In `clearDay`, add:

```javascript
    document.getElementById("tasks-list").innerHTML = "";
    document.getElementById("homepage-note").style.display = "none";
```

- [ ] **Step 4: Verify end-to-end against live Canvas**

The `.env` token is live. Start the app and check the real output:

```bash
cd ~/Code/better_canvas && python app.py &
sleep 2
curl -s "http://localhost:5001/api/day?date=$(date +%F)&tzoffset=420" | python3 -m json.tool | head -60
```

Expected: JSON with `homepage_available: true`, a non-empty `tasks` array whose items include titles like "Fall i-Ready Reading Diagnostic" with `submittable`/`submitted` fields, and (on a weekday) `schedule` entries titled "… — Live Class". Then open `http://localhost:5001` in a browser and confirm the "📋 Today's Tasks" section renders course blocks with status icons, dimmed optional tasks, and type tags; the schedule shows the live class with a Join Zoom button. Stop the server: `kill %1`.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "feat: render Today's Tasks section and homepage-driven schedule"
```

---

## Self-Review Notes

- **Spec coverage:** parsing (Tasks 1–2), status enrichment (Task 3 + Task 5), cached getters incl. Zoom (Task 4), `/api/day` schedule-replace + tasks + `homepage_available` (Task 5), frontend section + note + optional-dimming + status icons (Task 6), current-week check via `parse_week_range` (Tasks 1 & 5), weekend/empty handling (Task 2), per-course error resilience (`get_front_page`/`get_module_items_map` swallow errors → course skipped; `_resolve_task_status` logs and leaves defaults). Testing strategy from the spec is realized as fixture-based unit tests + mocked `/api/day` tests + a live end-to-end check.
- **Overlap** between Tasks and Assignments is intentional (per spec) — both sections render independently.
- **Type consistency:** `module_map` shape is identical in Tasks 3, 4, 5; task-item keys (`title/url/type_label/optional/submittable/content_id/submitted/graded/grade`) are produced by `enrich_tasks` (Task 3) and consumed unchanged in Tasks 5 and 6.
```
