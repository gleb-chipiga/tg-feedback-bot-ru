FROM python:3.11-slim

COPY feedback_bot /tg-feedback-bot-ru/feedback_bot
COPY requirements.txt /tg-feedback-bot-ru/
WORKDIR /tg-feedback-bot-ru
RUN pip install -r requirements.txt
VOLUME /var/tg-feedback-bot-ru
CMD ["python", "-m", "feedback_bot", "/var/tg-feedback-bot-rustorage.sqlite"]
