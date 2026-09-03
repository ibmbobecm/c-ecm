# C-ECM frontend — builds the static SPA and serves it via nginx, which
# also reverse-proxies /api/ to the backend container (see nginx.conf).
# One image, no separate frontend runtime needed.
#
# Build from the REPO ROOT so `frontend/` is in context:
#   docker build -f deploy/docker/frontend.Dockerfile -t cecm-frontend .
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
# Same-origin, relative API base -- nginx.conf's /api/ location proxies
# this to the backend container and strips the prefix, so the app never
# needs CORS in production and every backend route works with zero nginx
# maintenance as new routers are added.
ENV VITE_API_BASE=/api
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
