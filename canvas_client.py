import html
import re
import time
import requests
from datetime import datetime, date as _date, timedelta, timezone


def _format_time(iso_str, tzoffset=0):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        tz = timezone(timedelta(minutes=-tzoffset))
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return iso_str


class CanvasClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self._student_id = None
        self._courses_cache = None
        self._courses_cache_ts = 0

    def _resolve_student_id(self):
        if self._student_id is None:
            try:
                observees = self._get("/api/v1/users/self/observees")
                self._student_id = str(observees[0]["id"]) if observees else "self"
            except Exception:
                self._student_id = "self"
        return self._student_id

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        response = requests.get(url, headers=self.headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def _get_all(self, path, params=None):
        url = f"{self.base_url}{path}"
        results = []
        current_params = params
        while url:
            response = requests.get(url, headers=self.headers, params=current_params, timeout=10)
            response.raise_for_status()
            results.extend(response.json())
            current_params = None
            url = None
            for part in response.headers.get("Link", "").split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
        return results

    def get_active_courses(self):
        if self._courses_cache is not None and time.time() - self._courses_cache_ts < 300:
            return self._courses_cache
        result = self._get("/api/v1/courses", {"enrollment_state": "active", "per_page": 50})
        self._courses_cache = result
        self._courses_cache_ts = time.time()
        return result


    def get_schedule(self, date_str, course_ids, tzoffset=0):
        tz = timezone(timedelta(minutes=-tzoffset))
        d = _date.fromisoformat(date_str)
        start_utc = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = [
            ("type", "event"),
            ("start_date", start_utc),
            ("end_date", end_utc),
            ("per_page", "50"),
        ] + [("context_codes[]", f"course_{cid}") for cid in course_ids]
        events = self._get("/api/v1/calendar_events", params)
        return [
            {
                "time": _format_time(event.get("start_at", ""), tzoffset),
                "title": event.get("title", ""),
                "zoom_url": self._extract_zoom_url(event),
            }
            for event in events
        ]

    def _extract_zoom_url(self, event):
        location = event.get("location_name") or ""
        if "zoom.us" in location:
            url = location.strip()
            return url if url.startswith("https://") else f"https://{url}"
        description = event.get("description") or ""
        match = re.search(r'https://[^\s"\'<>]*zoom\.us[^\s"\'<>]*', description)
        if match:
            return html.unescape(match.group(0))
        lti_match = re.search(r'href="(https?://[^"]+/external_tools/[^"]+)"', description)
        if lti_match:
            return html.unescape(lti_match.group(1))
        return None

    def get_assignments_due(self, date_str, course_ids, tzoffset=0):
        # tzoffset: minutes west of UTC (browser getTimezoneOffset() convention)
        tz = timezone(timedelta(minutes=-tzoffset))
        d = _date.fromisoformat(date_str)
        start_dt = datetime(d.year, d.month, d.day, tzinfo=tz) - timedelta(days=14)
        end_dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz) + timedelta(days=1)
        start_utc = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = [
            ("type", "assignment"),
            ("start_date", start_utc),
            ("end_date", end_utc),
            ("per_page", "100"),
        ] + [("context_codes[]", f"course_{cid}") for cid in course_ids]
        events = self._get_all("/api/v1/calendar_events", params)
        seen = set()
        result = []
        for event in events:
            a = event.get("assignment") or {}
            context_code = event.get("context_code", "")
            cid = str(a.get("course_id", "")) or (context_code[len("course_"):] if context_code.startswith("course_") else "")
            aid = str(a.get("id", ""))
            if not aid or aid in seen:
                continue
            seen.add(aid)
            result.append({
                "id": aid,
                "course_id": cid,
                "title": event.get("title", ""),
                "due_at": a.get("due_at") or event.get("end_at"),
                "points_possible": a.get("points_possible"),
            })
        return result

    def get_submission_details(self, course_id, assignment_id):
        return self._get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{self._resolve_student_id()}",
            {"include[]": "submission_comments"},
        )

    def get_missing_assignments(self):
        return self._get(
            f"/api/v1/users/{self._resolve_student_id()}/missing_submissions",
            {"per_page": 50},
        )
