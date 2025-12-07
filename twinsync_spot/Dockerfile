ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    ffmpeg \
    curl

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy app
COPY app/ ./app/

# Copy run script
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Create data directory
RUN mkdir -p /data

LABEL \
    io.hass.name="TwinSync Spot" \
    io.hass.description="Does this match YOUR definition?" \
    io.hass.type="addon" \
    io.hass.version="1.1.0"

CMD ["/run.sh"]
