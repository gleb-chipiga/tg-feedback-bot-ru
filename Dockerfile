FROM python:slim

RUN mkdir /feedback_bot
COPY feedback_bot.py requirements.txt /feedback_bot/
RUN pip install --no-cache-dir -r /feedback_bot/requirements.txt

VOLUME /storage
CMD exec python3 /feedback_bot/feedback_bot.py "$ADMIN_USERNAME" /storage/feedback_bot.sqlite