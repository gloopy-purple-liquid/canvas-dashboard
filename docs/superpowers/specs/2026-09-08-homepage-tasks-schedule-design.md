# Homepage-Driven Schedule & Tasks — Design Spec

**Date:** 2026-09-08
**Status:** Approved
**Builds on:** `2026-04-24-better-canvas-design.md`

## Problem

The dashboard pulls the daily schedule from Canvas's `calendar_events` API and
assignments from `calendar_events` + `missing_submissions`. But the courses at
school.instructure.com don't publish live classes or a daily task list through
those APIs. Instead, each course's **weekly homepage** (a `wiki` front page)
embeds:

- the day's **live Zoom class** ("Attend: Live Class @ 10am"), and
- a **task list** for each weekday, including optional tasks.

None of this surfaces today, so the schedule shows "No classes scheduled" and
the day's real to-do list is invisible.

## Discovery (verified against live data, 2026-09-08)

Confirmed across 8 courses (11902, 11904, 11933, 11968, 11386, 11563, 11905):

- Every course uses `default_view = wiki`. The front page title encodes the week
  range, e.g. `"6W01 --> 09/07 - 09/11 - Humanities Homepage"`.
- The front page body contains five day-tabs: `<div id="tab1">` (Monday)
  through `<div id="tab5">` (Friday). A nav `<a href="#tab2">Tuesday</a>` links
  each. `#tab2` = Tuesday, matching the reference screenshot.
- Each day tab is a list of `<li>` items. Task items carry a text prefix that
  classifies them, and link to a Canvas resource:
  - `🗓️ Due Today:` — due assignment
  - `Start:` / `Continue:` — assignment work in progress
  - `🌀 Optional:` — optional task
  - `📖 Independent Reading:` — reading task (info)
  - `⚠️ Reminder:` — reminder (info)
  - `Attend: Live Class @ <time>` — the live Zoom class (→ schedule, not tasks)
- Task links point to `/courses/{cid}/modules/items/{item_id}`.
- The single module-item API (`/api/v1/courses/{cid}/modules/items/{id}`) returns
  404 for the observer token. **The working resolver** is the modules listing:
  `/api/v1/courses/{cid}/modules?include[]=items&include[]=content_details`,
  which yields per item: `id`, `type` (Assignment/Quiz/Page/File/ExternalUrl/
  Discussion), `content_id`, `title`, and `content_details.due_at` /
  `points_possible`. Verified: item `2011877` → `content_id 771602`, Assignment,
  due `2026-09-12`, 15 pts.
- The token is an **observer** token: `self` = parent (id 30836), observee =
  student (id 30796). Submission lookups use the observee id (existing
  `_resolve_student_id()` already does this).
- The **Zoom link** is a course nav item (`Zoom -> /courses/{cid}/external_tools/{tool_id}`,
  tool id varies per course), retrievable from `/api/v1/courses/{cid}/tabs`.

## Scope decisions (from brainstorming)

1. **Replace schedule + add tasks.** The homepage is the primary source: the
   live Zoom class fills Today's Schedule, and a new Tasks section is added.
   The existing Assignments and Missing sections stay for grade/submission
   tracking.
2. **Tasks grouped by course, optional dimmed.** One block per course; optional
   (🌀) tasks shown inline but visually de-emphasized.
3. **Show completion status.** For submittable tasks (Assignment/Quiz), resolve
   and display done / not-submitted / graded. Non-submittable tasks render as
   plain links.

## Architecture

Unchanged shape: `[Browser] ←→ [Flask localhost] ←→ [Canvas API]`. This feature
adds a new data source (front-page HTML) and a parsing layer. All parsing is
isolated in pure functions that operate on already-fetched HTML, so they are
unit-testable without network access.

### `canvas_client.py`

New/changed methods:

- `get_front_page(course_id)` → returns the front-page body HTML (or `None` if
  the course has no front page). Cached ~5 min per course (same pattern as
  `get_active_courses`).
- `get_module_items_map(course_id)` → fetches
  `/modules?include[]=items&include[]=content_details` (paginated) and returns
  `{ str(item_id): {"type", "content_id", "title", "due_at", "points"} }`.
  Cached ~5 min per course.
- `get_zoom_url(course_id)` → from `/api/v1/courses/{cid}/tabs`, return the
  `full_url` of the tab whose `label` is `"Zoom"` (or `None`). Cached.
- `get_homepage_day(course_id, weekday, module_map)` → orchestrates: get front
  page, call `parse_homepage_day`, and enrich task items via `module_map`.
  Returns `{"live_class": {...}|None, "tasks": [...]}`.

New pure functions (no network — the testable core):

- `parse_homepage_day(body, weekday)`:
  - `weekday`: 0=Mon … 4=Fri; returns empty for 5/6 (weekend).
  - Selects `<div id="tab{weekday+1}">` … up to the next `tab` div.
  - Returns `{"live_class": {...}|None, "tasks": [...]}` where:
    - `live_class`: `{"title": "Live Class", "time": "10:00 AM"|""}` parsed from
      an `Attend … Live Class @ <time>` item (time via regex; `""` if unparsable).
    - each task: `{"raw_title", "url", "item_id"|None, "type_label", "optional"}`
      where `type_label ∈ {due, start, continue, reading, reminder, other}`,
      `optional` is `True` for `🌀 Optional:` items, `item_id` is parsed from a
      `modules/items/{id}` href (else `None`), and `raw_title` is the link text.
  - Items with no actionable link and no recognized prefix (pure prose) are
    skipped.
- `classify_task_prefix(text)` → `(type_label, optional)`.
- `extract_class_time(text)` → `"10:00 AM"` | `""` (handles `10am`, `10 am`,
  `10:00am`).
- `parse_week_range(title, ref_date)` → `(start_date, end_date)` | `None`,
  extracting the `MM/DD - MM/DD` range from the front-page title (year inferred
  from `ref_date`).

### `app.py` — `/api/day`

Extend the existing handler. After loading active courses, for each course
(parallelized with the existing `ThreadPoolExecutor` pattern):

1. Fetch `module_map = get_module_items_map(cid)` and
   `homepage = get_homepage_day(cid, weekday, module_map)`.
2. **Schedule**: for each `live_class`, append
   `{"time", "title": "<course short name> — Live Class", "zoom_url": get_zoom_url(cid)}`.
   Manual schedule-config entries are still appended (unchanged).
3. **Tasks**: build a per-course block. For each task with a submittable
   `content_id` (type Assignment/Quiz), fetch submission status (reuse
   `get_submission_details`, mapping workflow_state exactly as
   `/api/submissions` does). Non-submittable items get `submittable: false`.

Respect the existing ignore list for course-level hiding.

**Current-week check:** the front-page title encodes a date range (e.g.
`"… 09/07 - 09/11 …"`). `parse_week_range(title)` extracts the two `MM/DD`
dates (year inferred from the requested date). If the requested `date` falls
within that range, `homepage_available` is `true` and `weekday` selects the tab;
otherwise homepage-sourced schedule/tasks are empty and the response carries
`homepage_available: false` so the frontend can explain. If the title has no
parseable range, treat the page as the current week (`homepage_available: true`).

### `/api/day` response (additions)

```json
{
  "date": "2026-09-08",
  "homepage_available": true,
  "schedule": [
    { "time": "10:00 AM", "title": "Humanities 6 — Live Class",
      "zoom_url": "https://school.instructure.com/courses/11902/external_tools/1039" }
  ],
  "tasks": [
    {
      "course_id": "11902",
      "course_name": "Humanities 6 …",
      "items": [
        { "title": "6W01 - Fall i-Ready Reading Diagnostic",
          "url": "https://school.instructure.com/courses/11902/modules/items/2011877",
          "type_label": "start", "optional": false,
          "submittable": true, "submitted": false, "graded": false, "grade": null },
        { "title": "6 - Class Name Suggestions",
          "url": "https://school.instructure.com/courses/11902/modules/items/2011879",
          "type_label": "other", "optional": true,
          "submittable": true, "submitted": false, "graded": false, "grade": null }
      ]
    }
  ],
  "assignments": [ /* unchanged */ ],
  "today_start_utc": "…"
}
```

`assignments` and `/api/missing` are unchanged.

### Frontend (`templates/index.html`)

- **Today's Schedule**: now populated by homepage live-class entries; each shows
  time, course label, and a "Join Zoom" button when `zoom_url` is present.
  Existing empty-state ("No classes scheduled") retained for genuinely empty days.
- **📋 Today's Tasks** (new section, below Assignments): one block per course
  (course name heading), each task a row with:
  - a status icon for submittable tasks: ✓ (submitted/graded) / ! (not
    submitted); plain bullet for non-submittable info tasks,
  - the task title as a link (opens the Canvas item in a new tab),
  - a small type tag (Due Today / Start / Continue / Reading / Reminder),
  - optional (🌀) tasks rendered dimmed.
- When `homepage_available` is false, the schedule + tasks sections show a note:
  "Weekly homepage is only available for the current week."
- All rendered text escaped (existing XSS-safe rendering pattern).

## Limitations (accepted)

- **Current week only.** The front page is replaced by the school each week, so
  historical/future weekly homepages aren't retrievable. Day navigation within
  the current week works; other weeks show the note above.
- **Weekends.** No Mon–Fri tab → empty schedule/tasks (not an error).
- **Overlap.** A "🗓️ Due Today" task may also appear in the Assignments section.
  Intentional: Tasks = the teacher's daily plan; Assignments = grade/submission
  tracker.
- **Parsing fragility.** Parsing depends on the school's homepage template
  (tab divs + text prefixes). If a course deviates, `parse_homepage_day` returns
  what it can and skips the rest rather than erroring; a course that fails to
  parse contributes no tasks but does not break the day view.

## Error handling

- A front-page fetch, modules fetch, or submission fetch that fails for one
  course is logged and that course is skipped; the rest of the day view still
  renders (same resilience as the current `/api/missing` handling).
- Unparseable class time → live-class entry still shown with empty time, sorted
  last among timed entries.

## Testing

- **Fixtures**: save real front-page HTML (course 11902 at minimum, plus one
  more with a different template variation) under `tests/fixtures/`.
- **`parse_homepage_day`** unit tests: correct tab selected per weekday; each
  task type classified; optional flag; live-class time extraction
  (`10am`/`10 am`/`10:00am`); weekend → empty; prose-only `<li>` skipped;
  missing tab → empty.
- **`classify_task_prefix`** / **`extract_class_time`** / **`parse_week_range`**
  table-driven tests (including a title with no parseable range → `None`).
- **Status resolution**: with a mocked module map + mocked submissions, assert
  submittable vs non-submittable items and correct status flags.
- **`/api/day`**: mock `CanvasClient` methods; assert `schedule`, `tasks`, and
  `homepage_available` shapes; assert course-level ignore is respected and a
  failing course is skipped without failing the request.

## Out of scope

- Retrieving past/future weeks' homepages.
- Marking tasks complete from the dashboard.
- Parsing homepages that don't follow the school's tab-based template beyond
  best-effort/skip.
