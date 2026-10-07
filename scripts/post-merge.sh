#!/bin/bash
set -e
pnpm install --frozen-lockfile
UV_PROJECT_ENVIRONMENT=.pythonlibs uv sync --frozen
# The Python API initializes only its explicitly owned schema during history build.
# Never run a broad schema push against the existing VPS/source database.
pnpm --filter @workspace/api-server run test
