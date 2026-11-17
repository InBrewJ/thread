FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --recursive --shallow-submodules \
    https://github.com/leejet/stable-diffusion.cpp.git . \
    && cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DSD_BUILD_EXAMPLES=ON \
        -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --config Release -j"$(nproc)" --target sd-cli \
    && cp build/bin/sd-cli /usr/local/bin/sd-cli

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/timeline.db \
    IMAGES_DIR=/app/data/mood_images \
    MODEL_PATH=/app/data/models/sd-turbo-q4_0.gguf \
    SD_CLI_BIN=/usr/local/bin/sd-cli

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/sd-cli /usr/local/bin/sd-cli

COPY mood-gen/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server/db.py /app/db.py
COPY mood-gen /app/mood-gen

WORKDIR /app/mood-gen

CMD ["python", "worker.py"]
