# Studio Decision Ledger — API and console on one origin.
#
# The console is built in a Node stage and copied across as static files, so
# no build tooling reaches the runtime image. The API serves it from the same
# origin the /api routes live on, which is why the client needs no proxy and
# no CORS in production.

FROM node:24-slim AS console
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build


FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies come from pyproject so there is one declaration of what the
# service needs. This layer rebuilds only when that file changes.
COPY api/pyproject.toml /app/api/pyproject.toml
COPY api/sdl /app/api/sdl
RUN pip install /app/api

# The built console, and the explicit pointer to it. Resolving this by walking
# up from the installed package would land in site-packages.
COPY --from=console /build/dist /app/dist
ENV SDL_CONSOLE_DIR=/app/dist

# Cloud Run sets PORT; the default keeps `docker run` working unchanged.
ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} is expanded; exec so uvicorn is PID 1 and receives
# Cloud Run's SIGTERM directly rather than through a shell that ignores it.
CMD exec uvicorn sdl.app:app --host 0.0.0.0 --port ${PORT}
