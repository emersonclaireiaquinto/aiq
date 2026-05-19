# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI dependency for extracting the current user ID from the request."""

from __future__ import annotations

import json
import logging
from base64 import b64decode

from fastapi import Request

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default-user"


def get_current_user_id(request: Request) -> str:
    """Extract user ID from the Authorization header (Bearer JWT) or fall back to default.

    When REQUIRE_AUTH=true, the frontend sends an idToken as a Bearer token.
    We decode the JWT payload (without verification — the auth provider already
    validated it) to extract the `sub` claim as the user ID.

    When REQUIRE_AUTH=false, no token is sent and we return DEFAULT_USER_ID.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return DEFAULT_USER_ID

    token = auth_header[7:]
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return DEFAULT_USER_ID
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = json.loads(b64decode(payload))
        user_id = decoded.get("sub") or decoded.get("user_id") or decoded.get("email")
        if user_id:
            return str(user_id)
    except Exception:
        logger.debug("Failed to decode JWT for user ID extraction, using default", exc_info=True)

    return DEFAULT_USER_ID
