#!/usr/bin/env bash
set -ex

uvicorn app:app --host 0.0.0.0 --port ${HTTP_PORT:-8000} --no-access-log