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


def classify_task_prefix(text):
    low = text.lower()
    optional = "🌀" in text or "optional:" in low
    if "due today" in low or "🗓" in text:
        label = "due"
    elif low.startswith("start"):
        label = "start"
    elif low.startswith("continue"):
        label = "continue"
    elif "reminder" in low or "⚠" in text:
        label = "reminder"
    elif "independent reading" in low or "📖" in text:
        label = "reading"
    else:
        label = "other"
    return label, optional


def extract_class_time(text):
    # Accepts "@ 10am", "at 9:00 AM", "10:00 am", "@ 11 A.M." — the "@"/"at"
    # prefix is optional; the am/pm suffix is required so plain numbers
    # (e.g. "20 minutes") don't match.
    m = re.search(r'(?:@|\bat\b)?\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?', text, re.I)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = m.group(2) or "00"
    ampm = m.group(3).upper() + "M"
    return f"{hour}:{minute} {ampm}"


def _is_live_class(text):
    # Teachers phrase the synchronous class differently across courses:
    #   "Attend: Live Class @ 10am"        (live + attend)
    #   "Attend POD Squad at 9:00 AM..."   (attend + time, no "live class")
    #   "Come to Live Class at 10:00 am"   (live + time, no "attend")
    # Treat a line as the live class when it names it outright (live + attend),
    # or when it carries a class time alongside an attend/live/join-Zoom signal.
    # Requiring a time in the looser cases avoids matching untimed checklist
    # lines like "Complete the Daily Big 3: Attend Classes".
    low = text.lower()
    has_time = extract_class_time(text) != ""
    live = "live class" in low
    attend = "attend" in low
    zoom = "zoom" in low
    return (live and attend) or (
        has_time and (live or attend or (zoom and "join" in low))
    )


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
        if _is_live_class(text):
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


def parse_week_range(title, ref_date):
    m = re.search(r'(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})', title or "")
    if not m:
        return None
    y = ref_date.year
    start = _date(y, int(m.group(1)), int(m.group(2)))
    end = _date(y, int(m.group(3)), int(m.group(4)))
    if end < start:
        end = _date(end.year + 1, end.month, end.day)
    return (start, end)


class CanvasClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self._student_id = None
        self._courses_cache = None
        self._courses_cache_ts = 0
        self._front_page_cache = {}
        self._module_map_cache = {}
        self._zoom_cache = {}

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
