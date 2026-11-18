FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY radar ./radar
COPY config ./config
ENV RADAR_STATE=/app/state/radar.db
# Un passage toutes les 5 minutes ; le résumé quotidien se lance avec : docker run … radar python -m radar digest
CMD ["sh", "-c", "while true; do python -m radar run; sleep 300; done"]
