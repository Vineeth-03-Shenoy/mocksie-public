# Regenerates package-lock.json within the existing semver ranges, with no host mount.
#   docker build -f lock.Dockerfile --output type=local,dest=. .   (exports only package-lock.json)
FROM node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS lock
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* \
 && git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"
WORKDIR /w
COPY package.json package-lock.json ./
RUN npm update --omit=dev --ignore-scripts && npm audit --omit=dev --audit-level=high

FROM scratch
COPY --from=lock /w/package-lock.json /package-lock.json
