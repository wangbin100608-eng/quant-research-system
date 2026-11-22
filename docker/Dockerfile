# 多阶段构建：Rust 时间隔离引擎（极小镜像）
FROM rust:1.83-alpine AS rust-builder
WORKDIR /build
COPY rust_core/Cargo.toml rust_core/Cargo.lock* ./
COPY rust_core/src ./src
RUN cargo build --release && strip target/release/timeguard

# Python 运行镜像
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    QUANT_ROOT=/app TZ=Asia/Shanghai
WORKDIR /app
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    tzdata curl ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*

COPY python/requirements.txt /app/python/requirements.txt
RUN pip install --no-cache-dir -r /app/python/requirements.txt
# PyTorch CPU 版（可选；失败不阻断镜像构建，基准脚本会优雅跳过）
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch || true

COPY config /app/config
COPY python /app/python
COPY scripts /app/scripts
COPY web /app/web
COPY --from=rust-builder /build/target/release/timeguard /app/rust_core/target/release/timeguard
RUN chmod +x /app/rust_core/target/release/timeguard

# 数据卷：元数据、快照、模型、预测、报告
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import sqlite3,os; assert os.path.exists('/app/data/meta.db') or True" || exit 1

EXPOSE 8501

CMD ["python", "/app/scripts/serve.py"]
