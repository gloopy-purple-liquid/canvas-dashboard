import re
import requests
from datetime import datetime


def _format_time(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return iso_str


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

    def get_missing_assignments(self, course_ids):
        missing = []
        for cid in course_ids:
            items = self._get(
                f"/api/v1/courses/{cid}/assignments",
                {"bucket": "missing", "per_page": 50},
            )
            missing.extend(items)
        return missing
