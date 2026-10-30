FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends bash git curl ripgrep build-essential gfortran ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*
COPY infra/docker/scientific-requirements.txt /opt/trustrepro/scientific-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /opt/trustrepro/scientific-requirements.txt
COPY backend/src/trustrepro/sandbox/command.py /opt/trustrepro/command.py
COPY backend/src/trustrepro/sandbox/parse_paper.py /opt/trustrepro/parse_paper.py
RUN useradd --uid 10001 --create-home researcher
USER 10001:10001
WORKDIR /workspace
CMD ["sleep", "infinity"]
