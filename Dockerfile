FROM python:3.8-slim

ENV PYTHONOPTIMIZE=2
ENV TZ=Europe/Moscow
RUN pip install --compile --no-cache-dir tg-feedback-bot-ru
VOLUME /var/tg-feedback-bot-ru
WORKDIR /var/tg-feedback-bot-ru
CMD ["feedback-bot", "config.yaml", "storage.sqlite"]
