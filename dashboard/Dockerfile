FROM node:20-slim AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:20-slim

WORKDIR /app

COPY --from=build /app/package.json /app/package-lock.json* ./
RUN npm ci --omit=dev

COPY --from=build /app/build ./build
COPY --from=build /app/public ./public

CMD ["sh", "-c", "PORT=${PORT:-3000} npx remix-serve ./build/server/index.js"]
