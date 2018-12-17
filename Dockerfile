FROM python:alpine

RUN mkdir /usr/src/feedback_bot
COPY feedback_bot.py requirements.txt /usr/src/feedback_bot/
RUN apk add --no-cache --virtual build-deps build-base && \
    pip install --no-cache-dir -r /usr/src/feedback_bot/requirements.txt && \
    apk del build-deps
RUN apk add --no-cache tzdata

VOLUME /var/feedback_bot
CMD ["python3", "/usr/src/feedback_bot/feedback_bot.py", "/var/feedback_bot/feedback_bot.sqlite"]
