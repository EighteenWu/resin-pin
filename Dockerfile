FROM python:3.12-slim

WORKDIR /app
COPY resin_pin /app/resin_pin

ENV PYTHONUNBUFFERED=1
ENV PIN_STATE_PATH=/data/state.json
ENV PIN_LISTEN=0.0.0.0:2270

VOLUME ["/data"]
EXPOSE 2270

CMD ["python", "-m", "resin_pin"]
