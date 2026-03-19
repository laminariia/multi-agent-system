# ──────────────────────────────────────────────────────────────────
# Stage 1: Build — install all deps + compile Remix
# ──────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (layer cache optimisation)
COPY package.json package-lock.json* ./
RUN npm ci

# Copy source and build
COPY . .
RUN mkdir -p public && npm run build

# ──────────────────────────────────────────────────────────────────
# Stage 2: Production — minimal runtime image
# ──────────────────────────────────────────────────────────────────
FROM node:20-alpine AS runner

WORKDIR /app

# Install curl for healthcheck (wget is available in alpine by default,
# but curl gives consistent behaviour with the API Dockerfile)
RUN apk add --no-cache curl

# Non-root user for security
RUN addgroup -g 1001 -S nodejs && adduser -S remix -u 1001 -G nodejs

# Production dependencies only
COPY --from=builder /app/package.json /app/package-lock.json* ./
RUN npm ci --omit=dev && npm cache clean --force

# Copy built application
COPY --from=builder --chown=remix:nodejs /app/build ./build
COPY --from=builder --chown=remix:nodejs /app/public ./public

USER remix

ENV NODE_ENV=production
ENV PORT=3000
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "curl -sf http://localhost:${PORT:-3000}/health || exit 1"]

CMD ["sh", "-c", "PORT=${PORT:-3000} npx remix-serve ./build/server/index.js"]
