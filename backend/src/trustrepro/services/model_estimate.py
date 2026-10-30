"""Reservation estimates only; observable provider usage remains the billing source."""

import base64
import binascii
import io
import json
import math
import re
from decimal import Decimal

from PIL import Image


def image_tokens(part, model):
    url = part.get("image_url", "")
    detail = part.get("detail", "auto")
    if isinstance(url, dict):
        detail, url = url.get("detail", detail), url.get("url", "")
    size = None
    # Inspect local image headers only. Never fetch URLs or resolve provider file IDs.
    if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
        encoded = url.split(";base64,", 1)[1]
        if len(encoded) <= 28 * 1024 * 1024:
            try:
                with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
                    # Image.open is lazy: dimensions do not require pixel decoding.
                    size = image.size
            except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
                pass
    # Documented tile families, including dated snapshots (not arbitrary aliases).
    match = re.fullmatch(r"(gpt-4o-mini|gpt-4o|gpt-4\.1|gpt-5\.1)(?:-\d{4}-\d{2}-\d{2})?", model)
    if match:
        base, tile = {"gpt-4o-mini": (2833, 5667), "gpt-5.1": (70, 140)}.get(match[1], (85, 170))
        if detail == "low":
            return base
        if size:
            width, height = size
            scale = min(1, 2048 / max(size), 768 / min(size))
            return base + tile * math.ceil(width * scale / 512) * math.ceil(height * scale / 512)
        return base + 8 * tile
    # Compatible providers and new model aliases need an estimate, not an invented
    # billing rule. Reserve two tokens per original 32px patch, with a 1024 floor.
    # Missing dimensions reserve 60000 tokens (30000 patches x 2), never zero.
    # Do not assume their "low"/"auto" modes resize like a different model family.
    if size:
        return max(1024, 2 * math.ceil(size[0] / 32) * math.ceil(size[1] / 32))
    return 60000


def reservation(body, config, minimum):
    images = 0

    def without_image_payload(value):
        nonlocal images
        if isinstance(value, list):
            return [without_image_payload(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") in {"input_image", "image_url"}:
                images += image_tokens(value, config["model"])
                return {"type": value["type"]}
            return {key: without_image_payload(item) for key, item in value.items()}
        return value

    # Only typed input parts are images. Text/tool arguments containing a data URL
    # are still text, and schemas in tools are not interpreted as image content.
    estimate_body = {**body, "input": without_image_payload(body.get("input"))}
    text = math.ceil(len(json.dumps(estimate_body, ensure_ascii=False).encode()) / 3)
    output = body.get("max_output_tokens") or 8192
    amount = (
        Decimal(text + images) * Decimal(str(config["inputRate"]))
        + Decimal(output) * Decimal(str(config["outputRate"]))
    ) / 1000000
    return max(minimum, amount), {"textTokens": text, "imageTokens": images, "outputTokens": output}
