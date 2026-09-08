ARG PYTHON_IMAGE=python:3.14-slim-trixie
FROM ${PYTHON_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends make git tmux openssh-client openssh-server locales ncurses-term \
    && localedef -i en_US -f UTF-8 en_US.UTF-8 \
    && mkdir -p /run/sshd \
    && rm -f /etc/ssh/ssh_host_* \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir ruff==0.16.6
RUN useradd --create-home --shell /bin/sh fixture

ENV LANG=en_US.UTF-8
ENV RUFF_NO_CACHE=true
USER fixture
ENV HOME=/home/fixture
WORKDIR /workspace
CMD ["make", "check", "smoke", "RUFF=python -m ruff"]
