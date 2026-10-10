# Build and run environment for the Linux rows of the portability matrix (T25, tests/portability/run_matrix.py).
# One image per ISA (--platform linux/arm64 and linux/amd64), the same recipe: Clang and GCC from the distribution, no
# retroarch, no ROMs, nothing of the repository (it is mounted read-only when a row runs).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends clang libclang-rt-18-dev gcc g++ cmake ninja-build python3 make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN clang --version | head -1 && gcc --version | head -1 && cmake --version | head -1
