FROM node:24-bookworm-slim AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG VITE_REPOSITORY_URL
ARG VITE_OPERATOR_NAME
ARG VITE_SUPPORT_EMAIL
ARG VITE_HOSTING_NOTICE
ARG VITE_MAIL_NOTICE
ARG VITE_RETENTION_NOTICE
ARG VITE_PRIVACY_OFFICER
ARG VITE_OVERSEAS_NOTICE
ENV VITE_PUBLIC_DEPLOYMENT=true VITE_API_BASE_URL=""
RUN npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /build/dist /srv
