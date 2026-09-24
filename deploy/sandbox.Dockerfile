# 沙箱镜像：预装 pytest/pytest-cov，构建上下文 = 仓库根
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest pytest-cov
WORKDIR /ws
CMD ["sleep", "infinity"]
