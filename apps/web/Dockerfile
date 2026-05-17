ARG NODE_BASE=docker.io/library/node:22-alpine
ARG NGINX_BASE=docker.io/library/nginx:1.27-alpine

FROM ${NODE_BASE} AS deps
WORKDIR /app
COPY package*.json ./
RUN npm install

FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM deps AS builder
COPY . .
ARG VITE_API_BASE_PATH=
ARG VITE_HEALTH_PATH=/healthz
ENV VITE_API_BASE_PATH=${VITE_API_BASE_PATH}
ENV VITE_HEALTH_PATH=${VITE_HEALTH_PATH}
RUN npm run build

FROM ${NGINX_BASE} AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
