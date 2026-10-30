ARG BACKEND_IMAGE=trustrepro-backend:desktop-build
FROM ${BACKEND_IMAGE}
COPY frontend/dist /app/frontend
ENV TRUSTREPRO_FRONTEND_ROOT=/app/frontend \
    TRUSTREPRO_BOOTSTRAP_MODEL=false \
    TRUSTREPRO_DESKTOP_WINDOWS_LOOPBACK=true \
    TRUSTREPRO_DESKTOP_TOKEN_FILE=/run/trustrepro-desktop-token
