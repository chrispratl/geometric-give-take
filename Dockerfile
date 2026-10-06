FROM python:3.14.8

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

WORKDIR /app
COPY *.py /app/

COPY variants /app/variants

ENV PYTHONUNBUFFERED=1
