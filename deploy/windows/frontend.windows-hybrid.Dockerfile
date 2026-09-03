# Identical to deploy/docker/frontend.Dockerfile, except it proxies to the
# Windows host's natively-running backend (see nginx.windows-hybrid.conf)
# instead of a "backend" container -- used only in the hybrid deployment
# (deploy/windows/docker-compose.windows-hybrid.yml), where the backend
# runs natively for FileNet content-write support and only postgres/nginx
# are containerized.
#
# Build from the REPO ROOT:
#   docker build -f deploy/windows/frontend.windows-hybrid.Dockerfile -t cecm-frontend .
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE=/api
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/windows/nginx.windows-hybrid.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
