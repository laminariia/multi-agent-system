FROM node:20-slim AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN mkdir -p public && npm run build

FROM node:20-slim

WORKDIR /app

COPY --from=build /app/package.json /app/package-lock.json* ./
RUN npm ci --omit=dev

COPY --from=build /app/build ./build
COPY --from=build /app/public ./public

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:${PORT:-3000}/ || exit 1

CMD ["sh", "-c", "PORT=${PORT:-3000} npx remix-serve ./build/server/index.js"]
