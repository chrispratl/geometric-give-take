FROM python:3.14.7

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

WORKDIR /app
COPY *.py /app/

COPY positions /app/positions

ENV PYTHONUNBUFFERED=1
