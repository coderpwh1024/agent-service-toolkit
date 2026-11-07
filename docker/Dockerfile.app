FROM python:3.13.14-slim

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml .
COPY uv.lock .
RUN pip install --no-cache-dir uv==0.11.32

# Install only the dependencies needed for the client application
# --frozen: Use exact versions from the lock file
# --only-group client: Only install dependencies marked as part of the "client" group in pyproject.toml
RUN uv sync --frozen --only-group client

COPY client/ ./client/
COPY app/__init__.py ./app/
COPY app/schemas/ ./app/schemas/
COPY app/services/__init__.py ./app/services/
COPY app/services/voice/__init__.py app/services/voice/stt.py app/services/voice/tts.py ./app/services/voice/
COPY app/services/voice/providers/__init__.py app/services/voice/providers/alibaba_stt.py app/services/voice/providers/alibaba_tts.py ./app/services/voice/providers/
COPY streamlit_app.py .

CMD ["streamlit", "run", "streamlit_app.py"]
