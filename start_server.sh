#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
exec python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
