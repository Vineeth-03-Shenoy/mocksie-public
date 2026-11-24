# Piston code-execution API rebuilt on Debian 12 (bookworm) + Node 20.
# Upstream ships Debian 10 (EOL) + Node 15; this keeps the same API and entrypoint.
# Every external input is pinned: base images by digest, source repos by commit, npm deps by package-lock.
ARG NODE_IMAGE=node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
ARG BUILD_IMAGE=buildpack-deps@sha256:f53cd4c58cdb96a467b98696cd510a949ef4ccd7d23e07b75107de754773007a

FROM ${BUILD_IMAGE} AS isolate
RUN apt-get update && apt-get install -y --no-install-recommends git libcap-dev && rm -rf /var/lib/apt/lists/* \
 && git clone https://github.com/envicutor/isolate.git /tmp/isolate \
 && cd /tmp/isolate && git checkout af6db68042c3aa0ded80787fbb78bc0846ea2114 \
 && make -j"$(nproc)" install

FROM ${NODE_IMAGE} AS api
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 make g++  && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/engineer-man/piston.git /src  && cd /src && git checkout de2b365ac759670a3a0d13ea208a0869a92c7e64
WORKDIR /app
# Our own refreshed lock (see lock.Dockerfile): upstream's pinned old versions carry known CVEs.
# The two git-hosted deps stay pinned by commit; ssh URLs are rewritten so the clone works unauthenticated.
COPY package.json package-lock.json ./
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"  && npm ci --omit=dev

FROM ${NODE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends \
    libxml2 gnupg tar coreutils util-linux libc6-dev binutils build-essential locales \
    libncurses6 libseccomp-dev rename procps python3 libcap-dev bash ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -M piston
COPY --from=isolate /usr/local/bin/isolate /usr/local/bin/
COPY --from=isolate /usr/local/etc/isolate /usr/local/etc/isolate
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen
# Piston only needs `node`: remove npm, corepack and yarn (their bundled deps were the other HIGH findings)
RUN rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /piston_api
COPY --from=api /app/node_modules ./node_modules
COPY --from=api /app/package.json /app/package-lock.json ./
COPY --from=api /src/api/src ./src
CMD ["/piston_api/src/docker-entrypoint.sh"]
EXPOSE 2000/tcp
