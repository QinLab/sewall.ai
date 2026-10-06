# Sewall.ai command-line image. Live commands reach NCBI and, with --config, a model provider.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY sewall ./sewall
COPY skills/live ./skills/live
RUN pip install --no-cache-dir ".[vertex,validation]" && rm -rf /src
RUN useradd --create-home sewall
USER sewall
WORKDIR /work
ENTRYPOINT ["sewall"]
CMD ["--help"]
