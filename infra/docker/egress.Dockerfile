FROM public.ecr.aws/docker/library/debian:bookworm-slim
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends squid ca-certificates && rm -rf /var/lib/apt/lists/*
COPY infra/egress.conf /etc/squid/squid.conf
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
