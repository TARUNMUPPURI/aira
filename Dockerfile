# ─────────────────────────────────────────────────────────────────────────────
#  ARIA — Dockerfile
#  Base: python:3.11-slim
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Prevents Python from writing .pyc files and buffers stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps needed by some Python packages (chromadb, grpcio)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (separate layer for cache efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Generate gRPC stubs from proto (grpc_tools must already be installed above)
COPY proto/ proto/
RUN python -m grpc_tools.protoc \
        -I proto \
        --python_out=proto \
        --grpc_python_out=proto \
        proto/approval.proto

# Copy the rest of the source
COPY . .

# Expose all three service ports
EXPOSE 8000 50051 8501

# Default command: FastAPI / uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
