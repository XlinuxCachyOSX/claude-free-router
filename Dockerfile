FROM python:3.12-slim

WORKDIR /app

COPY . .

ENV CFR_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8787

CMD ["python3", "-u", "server.py"]
