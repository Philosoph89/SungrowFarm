ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY run.sh /
RUN chmod a+x /run.sh

EXPOSE 8099

CMD [ "/run.sh" ]
