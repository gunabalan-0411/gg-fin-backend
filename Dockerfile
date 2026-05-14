FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    ffmpeg \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry==1.8.3

COPY pyproject.toml poetry.lock* ./

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

RUN pip install jellyfish indic-transliteration google-auth-oauthlib google-api-python-client xlrd

# CUDA runtime libraries required by ctranslate2 (faster-whisper GPU backend).
# These pip packages bundle the actual .so files — no CUDA base image needed.
RUN pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12

# Expose the bundled NVIDIA .so files so ctranslate2 can dlopen them at runtime.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:\
/usr/local/lib/python3.11/site-packages/nvidia/cuda_runtime/lib:\
/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib

COPY . .

# Bake whisper-small into the image so it's ready without a runtime download
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('Systran/faster-whisper-small', local_dir='/app/models/whisper-small')"

EXPOSE 8000

CMD ["sh", "-c", "python create_db.py || true && alembic upgrade head && exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 1 --timeout 300"]
