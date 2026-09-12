# 记账本 · 服务端版（自托管）
# 构建：docker build -t moneybook .
# 运行：docker run -d --name moneybook -p 8787:8787 -v moneybook-data:/app/data -e MONEYBOOK_TOKEN=你的口令 moneybook
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONPATH=/app/src
WORKDIR /app

# 零第三方依赖，只需要标准库
COPY src/ ./src/
COPY config.example.json run.sh ./
COPY docs/ ./docs/
COPY tools/ ./tools/
COPY samples/ ./samples/

# 数据目录（务必挂载出来，否则容器重建会丢数据）
VOLUME ["/app/data"]
EXPOSE 8787

# 用非 root 用户跑
RUN useradd -m -u 10001 moneybook && mkdir -p /app/data && chown -R moneybook:moneybook /app
USER moneybook

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/ping',timeout=4)"

CMD ["python", "-m", "moneybook"]
