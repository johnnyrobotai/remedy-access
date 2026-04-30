FROM python:3.13-slim
WORKDIR /agent
COPY agent/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip && pip install -e .
COPY agent/worker.py ./worker.py
CMD ["python", "worker.py", "start"]
