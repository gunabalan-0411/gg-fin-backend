FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg lsb-release \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    ffmpeg \
    postgresql-client-18 \
    libglib2.0-0 \
    libgomp1 \
    libgl1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry==1.8.3

COPY pyproject.toml poetry.lock* ./

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

RUN pip install jellyfish indic-transliteration google-auth-oauthlib google-api-python-client xlrd google-genai PyMuPDF opencv-python-headless numpy weasyprint

# Fonts for server-side PDF generation (WeasyPrint via Pango/Cairo — proper Tamil shaping)
RUN mkdir -p /app/fonts /usr/local/share/fonts && \
    curl -fsSL "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansTamil/NotoSansTamil-Regular.ttf" \
    -o /app/fonts/NotoSansTamil-Regular.ttf && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/ibmplexmono/IBMPlexMono-Regular.ttf" \
    -o /app/fonts/IBMPlexMono-Regular.ttf && \
    cp /app/fonts/*.ttf /usr/local/share/fonts/ && \
    fc-cache -fv /usr/local/share/fonts/ 2>/dev/null || true

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "python create_db.py || true && alembic upgrade head && exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 1 --timeout 300"]
