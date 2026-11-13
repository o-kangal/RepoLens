# Web image: the built app behind nginx, which also forwards /api to the backend so the browser
# talks to one origin. Built from the repository root: docker build -f frontend/Dockerfile .
FROM node:24.22.3-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS build

# The version in packageManager of package.json.
RUN npm install --global pnpm@12.16.1
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json ./
COPY frontend/package.json frontend/
RUN pnpm install --frozen-lockfile
COPY frontend frontend
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
EXPOSE 8080
