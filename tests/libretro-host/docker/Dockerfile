# A real libretro frontend (RetroArch) that needs no window, no display and no foreground application, plus the tools that
# build the core for Linux. Used by tests/libretro-host/test_real_frontend.py; nothing of the repository is copied into the image.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends retroarch clang cmake ninja-build python3 make ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# The build tools (tools/materialize_upstream.py, the code generators) call git.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN retroarch --version | head -1
