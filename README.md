# Canvas Dashboard

A faster, cleaner view of your Canvas LMS assignments and schedule. Designed for parent/observer accounts — shows today's class schedule with Zoom links, assignments due with submission status and grades, and overdue work that still needs to be submitted.

## Quick start (Docker)

```bash
docker run -d \
  -p 5001:5001 \
  -e CANVAS_BASE_URL=https://yourschool.instructure.com \
  -e CANVAS_TOKEN=your_token_here \
  -v canvas-dashboard-data:/data \
  --name canvas-dashboard \
  ghcr.io/yourusername/canvas-dashboard:latest
```

Then open [http://localhost:5001](http://localhost:5001).

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `CANVAS_BASE_URL` | Yes | Your school's Canvas URL, e.g. `https://school.instructure.com` |
| `CANVAS_TOKEN` | Yes | Canvas API access token (see below) |
| `PORT` | No | Port to listen on (default: `5001`) |

## Getting a Canvas API token

1. Log in to Canvas
2. Go to **Account → Settings**
3. Scroll to **Approved Integrations** and click **+ New Access Token**
4. Give it a name and click **Generate Token**
5. Copy the token — you won't be able to see it again

Works with both student and parent/observer accounts. Observer accounts automatically resolve to the observed student's submissions and missing assignments.

## Persisting the ignore list

When you hide an assignment or course, that preference is saved to `/data/ignore.json` inside the container. Mount a volume to keep it across container restarts:

```bash
# Named volume (recommended)
-v canvas-dashboard-data:/data

# Or bind mount to a local directory
-v /path/to/local/dir:/data
```

The ignore list resets to empty if no volume is mounted, which is fine if you don't use the hide feature.

## Building locally

```bash
docker build -t canvas-dashboard .
docker run -d \
  -p 5001:5001 \
  -e CANVAS_BASE_URL=https://yourschool.instructure.com \
  -e CANVAS_TOKEN=your_token_here \
  canvas-dashboard
```

## Running without Docker

Requires Python 3.10+.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create a .env file
echo "CANVAS_BASE_URL=https://yourschool.instructure.com" > .env
echo "CANVAS_TOKEN=your_token_here" >> .env

python app.py
```

## Running tests

```bash
source venv/bin/activate
pytest tests/
```
