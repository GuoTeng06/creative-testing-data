FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn && \
    pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY frontend/ ./frontend/
WORKDIR /app/backend
ENV MYSQL_HOST=192.168.16.38
ENV MYSQL_PORT=3306
ENV MYSQL_USER=root
ENV MYSQL_PASSWORD=root
ENV MYSQL_DATABASE="creative testing data"
EXPOSE 8766
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
