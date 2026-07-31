FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/web

RUN useradd --create-home --uid 10001 scraper-control
COPY web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY web/app ./app
USER scraper-control
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os, urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/healthz', headers={'Authorization':'Bearer '+os.environ['JAWNIX_SCRAPER_CONTROL_TOKEN']}); urllib.request.urlopen(request, timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
