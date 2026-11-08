# Deep Dive Agent —— Docker 部署镜像
# 开发模式仍用 `python run.py`（见 README 快速开始）；本镜像用于 Docker 一键部署。
FROM python:3.11-slim

WORKDIR /app

# 容器时区：保证使用统计等按本地（东八区）日期归档
ENV TZ=Asia/Shanghai

# 先装依赖再拷代码：依赖不变时命中层缓存，改代码不触发重装。
COPY requirements.txt .
# 装包源默认官方 PyPI；国内网络构建时可传加速源：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

# 拷贝运行所需内容（.dockerignore 已排除 data/.venv/.env 等，不会打进镜像）
COPY app ./app
COPY frontend ./frontend
COPY run.py .

# 容器内必须绑 0.0.0.0 对外（run.py 绑 127.0.0.1 仅本机可访问，端口映射打不进来）；
# 启动不走 run.py 入口（其 docstring 亦注明：生产/部署用 uvicorn 直启）
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
