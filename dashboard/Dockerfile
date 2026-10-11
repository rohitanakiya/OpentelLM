# Multi-stage build for the Next.js dashboard.
# Final image runs:
#   1. next start (the App Router server) on :3000
#   2. ws-server.mjs on :3001 (standalone WebSocket server)
# Both processes are started by the CMD at the bottom.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# npm ci installs exactly what package-lock.json pins (reproducible builds)
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV WS_PORT=3001

# Standalone Next.js output bundles only what's needed
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static     ./.next/static
COPY --from=builder /app/public           ./public
# The WS server isn't part of the Next build — copy it explicitly,
# and copy node_modules so concurrently/jose/ioredis/ws are available.
COPY --from=builder /app/ws-server.mjs    ./ws-server.mjs
COPY --from=deps    /app/node_modules     ./node_modules

EXPOSE 3000 3001

# wget is in busybox on alpine; healthcheck verifies both processes
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O - http://localhost:3000/login >/dev/null \
   && wget -q -O - http://localhost:3001/health >/dev/null \
   || exit 1

CMD ["sh", "-c", "node ws-server.mjs & exec node server.js"]
