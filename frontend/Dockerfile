FROM node:24.18.0-bookworm-slim AS build

ENV CI=true
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG FRONTEND_BACKEND_TARGET=http://backend:8000
ENV FRONTEND_BACKEND_TARGET=$FRONTEND_BACKEND_TARGET
RUN npm run build

FROM nginx:1.30.4-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
