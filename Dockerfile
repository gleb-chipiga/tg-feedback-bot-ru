# syntax=docker/dockerfile:1.7

ARG UV_VERSION=latest
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.13-slim

WORKDIR /app

ENV UV_LINK_MODE=copy

COPY --from=uv /uv /usr/local/bin/uv

ARG BOT_VERSION
ENV BOT_VERSION=${BOT_VERSION}

RUN --mount=type=cache,target=/root/.cache/uv \
    test -n "$BOT_VERSION" || (echo "BOT_VERSION build arg is required" >&2 && exit 1); \
    uv pip install --system --python /usr/local/bin/python "tg-feedback-bot-ru==${BOT_VERSION}"

CMD ["tg-feedback-bot-ru"]
