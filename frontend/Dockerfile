# ============================================================
# Frontend - Vite + React + Tailwind CSS
# Dockerfile
#
# Strategy:
#   Stage 1 (builder) — Node 20 compiles the Vite app into
#                       static files under /app/dist
#   Stage 2 (runtime) — nginx:alpine serves those static files
#
# The final image has NO Node runtime, making it very small
# (typically ~25 MB vs ~1 GB with Node).
# ============================================================


# ------------------------------------------------------------
# Stage 1 - Builder
# ------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy dependency manifests first so Docker layer cache is
# only invalidated when packages actually change.
COPY package.json package-lock.json ./

# Install all dependencies (including devDependencies needed
# by Vite / Tailwind at build time).
RUN npm ci

# Copy the rest of the source code.
COPY . .

# Build the production bundle.
# Output goes to /app/dist by default in Vite.
RUN npm run build


# ------------------------------------------------------------
# Stage 2 - Runtime
# A minimal nginx image that just serves the compiled assets.
# ------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Remove the default nginx demo page.
RUN rm -rf /usr/share/nginx/html/*

# Copy the compiled Vite output from the builder stage.
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy the custom nginx config that handles React client-side
# routing (rewrites all 404s to index.html so React Router works).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy entrypoint script to inject runtime environment variables into /env-config.js
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# nginx listens on port 80 inside the container.
# Azure Static Web Apps / Container Apps maps this to 443 externally.
EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
