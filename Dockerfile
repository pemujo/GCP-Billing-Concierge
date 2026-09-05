# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

ENV UV_INDEX_URL=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=https://pypi.org/simple

COPY ./pyproject.toml ./README.md ./

COPY ./GCP_billing_concierge ./GCP_billing_concierge

RUN uv sync --no-dev

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn GCP_billing_concierge.fast_api_app:app --host 0.0.0.0 --port ${PORT:-8080}"]
