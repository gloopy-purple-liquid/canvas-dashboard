import re
import requests
from datetime import datetime


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
