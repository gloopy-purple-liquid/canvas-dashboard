# Better Canvas — Design Spec

**Date:** 2026-04-24  
**Status:** Approved

## Overview

A local web app that gives a student a single-page daily view of everything they need from Canvas LMS (yourschool.instructure.com): today's class schedule with Zoom links, assignments due today with submission status/grades/teacher comments, and any missing assignments. The parent/student runs it locally — no hosting, no accounts, no config beyond a Canvas API token.

---

## Architecture

```
[Browser] ←→ [Flask app on localhost:5000] ←→ [Canvas API at yourschool.instructure.com]
```

- **Backend:** Python + Flask. Runs locally with `python app.py`.
- **Frontend:** Single `index.html` served by Flask, with vanilla JS and CSS. No build step.
- **Auth:** Canvas personal access token stored in a `.env` file. The Flask server holds the token and proxies all Canvas API calls — the browser never sees it.
- **Persistence:** None. All data is fetched live from Canvas on each page load / day navigation.

**File structure:**
```
better_canvas/
├── app.py              # Flask server + Canvas API proxy
├── .env                # CANVAS_TOKEN and CANVAS_BASE_URL (not committed)
├── .env.example        # Template for setup
├── requirements.txt    # flask, requests, python-dotenv
└── templates/
    └── index.html      # Single-page frontend
```

---

## Canvas API Endpoints Used

| Purpose | Canvas endpoint |
|---|---|
| Class schedule + Zoom links | `GET /api/v1/calendar_events?type=event&start_date=DATE&end_date=DATE&context_codes[]=course_ID` |
| Assignments due on a day | `GET /api/v1/planner/items?start_date=DATE&end_date=DATE` |
| Active courses list | `GET /api/v1/courses?enrollment_state=active&per_page=50` |
| Missing assignments | `GET /api/v1/courses/:id/assignments?bucket=missing&per_page=50` |
| Submission details (grade + comments) | `GET /api/v1/courses/:id/assignments/:id/submissions/self?include[]=submission_comments` |

The planner items endpoint returns submission state (submitted/unsubmitted/graded) inline, which covers the common case. Submission details (grade + comments) are fetched per-assignment only when the planner item shows a submission exists.

**Schedule fetch flow:** To get calendar events, the backend first fetches active courses to collect all course IDs, then passes them as `context_codes[]=course_COURSEID` to the calendar events endpoint. This is required — the endpoint does not return events across all courses without explicit context codes.

**Zoom URL extraction:** Canvas stores the Zoom meeting URL in the calendar event's `location_name` field (preferred) or embedded as a link in the `description` HTML. The backend checks `location_name` first; if it doesn't look like a URL, it parses `description` with a regex to find any `https://...zoom.us/...` link. If neither yields a URL, `zoom_url` is `null` and the frontend omits the "Join Zoom" button.

---

## Flask Routes

### `GET /`
Serves `index.html`. No parameters.

### `GET /api/day?date=YYYY-MM-DD`
Returns all data for a given day. Date defaults to today if omitted.

Response:
```json
{
  "date": "2026-04-24",
  "schedule": [
    {
      "time": "9:00 AM",
      "title": "Mathematics — Period 1",
      "teacher": "Mrs. Anderson",
      "zoom_url": "https://zoom.us/j/..."
    }
  ],
  "assignments": [
    {
      "id": "12345",
      "course_id": "678",
      "title": "Math Homework #12",
      "course_name": "Mathematics",
      "due_at": "2026-04-24T23:59:00Z",
      "submitted": true,
      "grade": "94%",
      "score": 94,
      "points_possible": 100,
      "graded": true,
      "comments": ["Great work on the word problems, Sophia!"]
    }
  ]
}
```

### `GET /api/missing`
Returns missing assignments across all active courses.

Response:
```json
{
  "missing": [
    {
      "title": "History Chapter 4 Quiz",
      "course_name": "US History",
      "due_at": "2026-04-21T23:59:00Z"
    }
  ]
}
```

---

## Frontend

A single `index.html` template rendered by Flask (Jinja2 for injecting the base URL if needed, otherwise pure static).

**Layout (single column, top to bottom):**
1. **Header/nav bar** — app name, prev-day arrow, current date, next-day arrow, "Today" button
2. **Today's Schedule** — one card per event, showing time, class name, teacher, "Join Zoom" button
3. **Assignments Due Today** — one card per assignment showing: status icon (✓ submitted / ! not submitted), title, course, grade if graded, teacher comment if present
4. **Missing Assignments** — shown below due assignments, orange border, "was due [date]" label. Hidden/collapsed if none.

**State:**
- Current date tracked in JS. Initializes to today.
- Prev/next arrows call `GET /api/day?date=...` and re-render sections.
- "Today" button resets to current date.
- Loading state shown while fetching.
- Error state shown if Canvas API call fails (e.g., bad token).

**Styling:**
- Clean, light theme. No external CSS frameworks — hand-written CSS.
- Color coding: green = submitted/graded, orange = pending grade, red = not submitted, orange border = missing.
- Responsive for laptop screens (no mobile requirement).

---

## Setup Flow

1. Clone/download the project
2. Copy `.env.example` to `.env` and fill in:
   ```
   CANVAS_TOKEN=your_token_here
   CANVAS_BASE_URL=https://yourschool.instructure.com
   ```
3. Get a token: Canvas → Account → Settings → New Access Token
4. `pip install -r requirements.txt`
5. `python app.py`
6. Open `http://localhost:5000`

---

## Error Handling

- **Invalid token / 401 from Canvas:** Return a clear error to the frontend; display "Could not connect to Canvas — check your API token in .env"
- **No events for a day:** Show "No classes scheduled" and "No assignments due" placeholders
- **Canvas API timeout:** Return 504 with a user-friendly error message
- **Missing assignments fetch fails:** Log the error but still return the day data — missing section just shows empty

---

## Out of Scope

- Mobile layout
- Push notifications or reminders
- Multi-student / multi-account support
- Submitting assignments through the app
- Showing course grades / gradebook overview
