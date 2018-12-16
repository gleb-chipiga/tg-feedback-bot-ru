FROM python:alpine

RUN mkdir /feedback_bot
COPY feedback_bot.py requirements.txt /feedback_bot/
RUN apk --update add --virtual build-dependencies build-base && \
    pip install --no-cache-dir -r /feedback_bot/requirements.txt && \
    apk del build-dependencies build-base

VOLUME /storage
CMD ["python3", "/feedback_bot/feedback_bot.py", "/storage/feedback_bot.sqlite"]
