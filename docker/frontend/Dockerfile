FROM node:22-alpine AS base
WORKDIR /app/frontend

FROM base AS deps
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install

FROM base AS development
COPY --from=deps /app/frontend/node_modules ./node_modules
COPY frontend ./
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM base AS builder
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ARG API_PROXY_BASE_URL=http://backend:8000/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV API_PROXY_BASE_URL=${API_PROXY_BASE_URL}
COPY --from=deps /app/frontend/node_modules ./node_modules
COPY frontend ./
RUN npm run build

FROM node:22-alpine AS production
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
WORKDIR /app/frontend
COPY --from=builder /app/frontend/.next/standalone ./
COPY --from=builder /app/frontend/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
