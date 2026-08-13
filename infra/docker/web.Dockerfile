# =============================================================================
# Web image (§100, P23-T01)
# =============================================================================
# Build from the repository root:
#   docker build -f infra/docker/web.Dockerfile -t aipvs-web .
#
# Next.js standalone output: the build traces which files the server actually
# needs and copies only those, so the runtime image carries neither the build
# toolchain nor `node_modules` in full. For this app that is ~180 MB rather
# than ~1.2 GB.
#
# **`NEXT_PUBLIC_API_URL` is baked in at build time**, because that is what
# `NEXT_PUBLIC_` means — the value is inlined into the client bundle. So the
# image is environment-specific, and the same image cannot be promoted from
# staging to production. That is a real constraint and it is stated here
# rather than discovered when staging's API URL appears in production.
#
# Nothing secret may be passed this way. Only `NEXT_PUBLIC_*` is accepted, and
# by definition those reach the browser (§7).
# =============================================================================

FROM node:22-bookworm-slim AS builder

RUN corepack enable

WORKDIR /build

COPY pnpm-lock.yaml pnpm-workspace.yaml package.json turbo.json ./
COPY apps/web/package.json apps/web/
COPY packages/ui/package.json packages/ui/
COPY packages/config/package.json packages/config/
COPY packages/shared-types/package.json packages/shared-types/

RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store \
    && pnpm install --frozen-lockfile

COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN pnpm --filter @aipvs/web build


FROM node:22-bookworm-slim AS runtime

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone bundle carries its own minimal node_modules; `static` and
# `public` are not traced into it and have to be copied alongside.
COPY --from=builder --chown=app:app /build/apps/web/.next/standalone ./
COPY --from=builder --chown=app:app /build/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=app:app /build/apps/web/public ./apps/web/public

USER app
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "apps/web/server.js"]
