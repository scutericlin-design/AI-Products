FROM docker-a-stock-ai-v1-4:stock-integrity-20260906
WORKDIR /app
COPY stock_alpha /app/stock_alpha
COPY tests /app/tests
ENV TZ=Asia/Shanghai PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "stock_alpha.live", "serve"]
