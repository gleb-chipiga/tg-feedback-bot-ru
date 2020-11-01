FROM python:3.8-slim

ENV PYTHONOPTIMIZE=2
RUN pip install --compile --no-cache-dir tg-feedback-bot-ru
VOLUME /var/tg-feedback-bot-ru
CMD ["feedback-bot", "/var/tg-feedback-bot-ru/feedback_bot.sqlite"]
