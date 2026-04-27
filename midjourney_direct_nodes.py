"""ComfyUI Midjourney Direct — Midjourney access via the LegNext.ai pool-based proxy.

Midjourney has no official public API. LegNext is a pool-based proxy service
(it supplies the Midjourney accounts, you pay per image) — the cheapest viable
provider as of April 2026 at $0.08/image v8.1 fast, true pay-as-you-go with
no monthly minimum.

Auth: x-api-key header. Endpoints documented at https://docs.legnext.ai/.
"""

import os
import io
import json
import time
import hashlib

import numpy as np
import torch
from PIL import Image
import requests

try:
    import folder_paths
except ImportError:
    folder_paths = None


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

CATEGORY = "Midjourney Direct"

LEGNEXT_BASE_URL = "https://api.legnext.ai/api/v1"
DIFFUSION_ENDPOINT = f"{LEGNEXT_BASE_URL}/diffusion"
JOB_ENDPOINT = f"{LEGNEXT_BASE_URL}/job"

# Versions LegNext exposes. Display label -> Midjourney --v flag value.
# niji-6 is special-cased: emits --niji 6, no --v.
# Note: v8 and v8.1 require a LegNext Pro subscription. v7 and below work
# on the standard pay-as-you-go tier, which is what most users are on.
VERSIONS = {
    "v7 (default)": "7",
    "v6.1": "6.1",
    "v6": "6",
    "niji-6": "niji-6",
    "v8.1 (LegNext Pro only)": "8.1",
    "v8 (LegNext Pro only)": "8",
    "v8 hd (LegNext Pro, 4x cost)": "8-hd",
}

ASPECT_RATIOS = [
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
]

QUALITY_LEVELS = ["0.25", "0.5", "1", "2", "4"]

# Approximate USD/point — LegNext's published rate is $0.08 per ~80 points
# for v8.1 fast (1 point ≈ $0.001). Used only for the human-readable cost
# display; the actual billed amount comes back from the API meta.usage.consume.
POINT_TO_USD = 0.001

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_POLL_INTERVAL_S = 3.0


# ---------------------------------------------------------------------------
# CREDENTIALS
# ---------------------------------------------------------------------------

def _resolve_api_key(api_key_input=""):
    """Resolution order: node input → LEGNEXT_API_KEY env var → legnext_api_key.txt at ComfyUI root."""
    key = (api_key_input or "").strip()
    if key:
        return key

    env_key = os.environ.get("LEGNEXT_API_KEY", "").strip()
    if env_key:
        return env_key

    if folder_paths is not None:
        root = os.path.dirname(folder_paths.get_output_directory())
    else:
        root = os.path.dirname(__file__)
    key_file = os.path.join(root, "legnext_api_key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            file_key = f.read().strip()
        if file_key:
            return file_key

    raise ValueError(
        "[Midjourney Direct] No LegNext API key found. Provide via:\n"
        "  1. The api_key input on the node\n"
        "  2. LEGNEXT_API_KEY environment variable\n"
        "  3. legnext_api_key.txt file in your ComfyUI root\n\n"
        "Get a key at: https://legnext.ai/"
    )


# ---------------------------------------------------------------------------
# IMAGE HELPERS
# ---------------------------------------------------------------------------

def _pil_to_tensor(pil_image):
    """PIL → ComfyUI image tensor [1, H, W, 3] float32 in [0,1]."""
    img_np = np.array(pil_image.convert("RGB")).astype(np.float32) / 255.0
    return torch.from_numpy(img_np).unsqueeze(0)


def _download_image(url, timeout=60.0):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content))


# ---------------------------------------------------------------------------
# PROMPT BUILDING — append Midjourney-style flags to the text prompt.
# LegNext's pool runs real Midjourney, so native flag syntax is the most
# reliable path (avoids guessing whether they accept structured JSON fields).
# ---------------------------------------------------------------------------

def _build_prompt(prompt, version, aspect_ratio, seed,
                  style_raw, weird, chaos, stylize, quality,
                  turbo, draft, iw, negative, tile):
    parts = [prompt.strip()]
    version_value = VERSIONS[version]

    # Model/version: niji is its own flag, hd is suffix on --v 8
    if version_value == "niji-6":
        parts.append("--niji 6")
    elif version_value == "8-hd":
        parts.append("--v 8")
        parts.append("--hd")
    else:
        parts.append(f"--v {version_value}")

    parts.append(f"--ar {aspect_ratio}")

    if style_raw:
        parts.append("--style raw")
    if weird and weird > 0:
        parts.append(f"--weird {int(weird)}")
    if chaos and chaos > 0:
        parts.append(f"--chaos {int(chaos)}")
    if stylize is not None and int(stylize) != 100:
        parts.append(f"--s {int(stylize)}")
    if quality and quality != "1":
        parts.append(f"--q {quality}")
    if turbo:
        parts.append("--turbo")
    if draft:
        parts.append("--draft")
    if iw is not None and abs(float(iw) - 1.0) > 1e-6:
        parts.append(f"--iw {float(iw):g}")
    if negative and negative.strip():
        parts.append(f"--no {negative.strip()}")
    if tile:
        parts.append("--tile")
    if seed and int(seed) > 0:
        parts.append(f"--seed {int(seed)}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# CACHE KEY
# ---------------------------------------------------------------------------

def _make_cache_key(text_with_flags):
    """Deterministic cache key for Hash Vault. The fully-built flag string
    captures every parameter that affects output, so hashing it alone is
    sufficient — no need to hash the structured fields separately."""
    return hashlib.sha256(text_with_flags.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# POLLING
# ---------------------------------------------------------------------------

def _poll_for_completion(job_id, api_key, poll_interval, timeout):
    """Poll GET /job/{id} until status in {completed, failed} or timeout."""
    headers = {"x-api-key": api_key}
    url = f"{JOB_ENDPOINT}/{job_id}"
    t0 = time.time()
    last_status = None
    while True:
        elapsed = time.time() - t0
        if elapsed > timeout:
            raise TimeoutError(
                f"[Midjourney Direct] Job {job_id} did not complete within {timeout:.0f}s "
                f"(last status: {last_status})"
            )

        try:
            r = requests.get(url, headers=headers, timeout=30.0)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[Midjourney Direct] Poll error (will retry): {e}")
            time.sleep(poll_interval)
            continue

        data = r.json()
        status = data.get("status")
        if status != last_status:
            print(f"[Midjourney Direct] Job {job_id[:8]}… status: {status} (elapsed {elapsed:.0f}s)")
            last_status = status

        if status == "completed":
            return data
        if status == "failed":
            err = data.get("error") or {}
            msg = err.get("message") or err.get("raw_message") or "unknown"
            raise RuntimeError(f"[Midjourney Direct] Job failed: {msg}")

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# NODE: Midjourney Generate
# ---------------------------------------------------------------------------

class MidjourneyGenerate:
    """Generate a Midjourney image via LegNext.ai (pool-based proxy).

    Returns the 4-grid as a single IMAGE. Use ComfyUI's Image Crop / Image
    Tile Split helpers downstream if you want the four upscaled tiles
    separately — Midjourney returns one composite tile by default."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "An elderly Parisian bookseller in his shop, golden hour side light…",
                    "tooltip": "Midjourney prompt. Don't include flags here — wire the structured inputs below.",
                }),
                "version": (list(VERSIONS.keys()), {
                    "default": "v7 (default)",
                    "tooltip": (
                        "v7 is the default for the standard LegNext pay-as-you-go tier. "
                        "v8 / v8.1 / v8 hd require a LegNext Pro subscription. "
                        "niji-6 is the anime / East-Asian aesthetic model."
                    ),
                }),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "2:3"}),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFF,
                    "tooltip": "0 = random. Same seed + same prompt + same params = consistent output.",
                }),
            },
            "optional": {
                "style_raw": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "--style raw. Reduces MJ's default heavy stylization. ON for AKURATE-style realism.",
                }),
                "stylize": ("INT", {
                    "default": 100, "min": 0, "max": 1000,
                    "tooltip": "--s. Lower = more prompt-faithful. Higher = more artistic interpretation.",
                }),
                "weird": ("INT", {
                    "default": 0, "min": 0, "max": 3000,
                    "tooltip": "--weird. Unconventional aesthetic exploration. 0 = off.",
                }),
                "chaos": ("INT", {
                    "default": 0, "min": 0, "max": 100,
                    "tooltip": "--chaos. Variety across the four grid tiles.",
                }),
                "quality": (QUALITY_LEVELS, {
                    "default": "1",
                    "tooltip": "--q. Higher = more compute = more cost. 1 is standard.",
                }),
                "turbo": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "--turbo. 4x faster, 2x cost.",
                }),
                "draft": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "--draft. Half-power preview (cheap + fast). Use for ideation passes.",
                }),
                "image_weight": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05,
                    "tooltip": "--iw. Influence of any image prompt. Only meaningful when prompt includes a URL.",
                }),
                "negative": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "--no. Comma-separated tokens to exclude (e.g. 'people, text, frame').",
                }),
                "tile": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "--tile. Generate seamless tileable patterns.",
                }),
                "poll_interval_seconds": ("FLOAT", {
                    "default": DEFAULT_POLL_INTERVAL_S, "min": 1.0, "max": 30.0, "step": 0.5,
                    "tooltip": "How often to check job status. 3s is a reasonable default.",
                }),
                "timeout_seconds": ("FLOAT", {
                    "default": DEFAULT_TIMEOUT_S, "min": 30.0, "max": 1800.0, "step": 10.0,
                    "tooltip": "Hard cap on total wait time before raising an error.",
                }),
                "api_key": ("STRING", {
                    "default": "",
                    "placeholder": "LegNext API key (or use env/file)",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "cost_info", "cache_key")
    FUNCTION = "generate"
    CATEGORY = CATEGORY

    def generate(self, prompt, version, aspect_ratio, seed,
                 style_raw=True, stylize=100, weird=0, chaos=0,
                 quality="1", turbo=False, draft=False, image_weight=1.0,
                 negative="", tile=False,
                 poll_interval_seconds=DEFAULT_POLL_INTERVAL_S,
                 timeout_seconds=DEFAULT_TIMEOUT_S,
                 api_key=""):
        if not prompt or not prompt.strip():
            raise ValueError("[Midjourney Direct] Prompt is empty.")

        api_key = _resolve_api_key(api_key)

        text_with_flags = _build_prompt(
            prompt=prompt,
            version=version,
            aspect_ratio=aspect_ratio,
            seed=seed,
            style_raw=style_raw,
            weird=weird,
            chaos=chaos,
            stylize=stylize,
            quality=quality,
            turbo=turbo,
            draft=draft,
            iw=image_weight,
            negative=negative,
            tile=tile,
        )
        cache_key = _make_cache_key(text_with_flags)

        print(f"[Midjourney Direct] Submitting: {text_with_flags[:160]}{'…' if len(text_with_flags) > 160 else ''}")

        # Submit job
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        body = {"text": text_with_flags}

        try:
            r = requests.post(DIFFUSION_ENDPOINT, headers=headers, json=body, timeout=30.0)
            r.raise_for_status()
        except requests.HTTPError as e:
            try:
                err_body = r.json()
            except Exception:
                err_body = r.text[:500] if r is not None else "(no body)"
            raise RuntimeError(
                f"[Midjourney Direct] Submission failed: HTTP {r.status_code} — {err_body}"
            ) from e
        except requests.RequestException as e:
            raise RuntimeError(f"[Midjourney Direct] Network error on submission: {e}") from e

        submit_data = r.json()
        job_id = submit_data.get("job_id")
        if not job_id:
            raise RuntimeError(
                f"[Midjourney Direct] Submission response missing job_id: {json.dumps(submit_data)[:500]}"
            )

        print(f"[Midjourney Direct] Job submitted: {job_id}")

        # Poll
        result = _poll_for_completion(job_id, api_key, poll_interval_seconds, timeout_seconds)

        # Extract image URL
        output = result.get("output") or {}
        image_url = output.get("image_url")
        if not image_url:
            urls = output.get("image_urls") or []
            if urls:
                image_url = urls[0]
        if not image_url:
            raise RuntimeError(
                f"[Midjourney Direct] No image URL in completed job: "
                f"{json.dumps(result)[:500]}"
            )

        # Download + tensorize
        pil_image = _download_image(image_url)
        tensor = _pil_to_tensor(pil_image)

        # Cost info
        meta = result.get("meta") or {}
        usage = meta.get("usage") or {}
        consumed = usage.get("consume", 0) or 0
        try:
            consumed_int = int(consumed)
        except (TypeError, ValueError):
            consumed_int = 0
        usd = consumed_int * POINT_TO_USD

        # Elapsed
        try:
            t_start = meta.get("started_at") or meta.get("created_at")
            t_end = meta.get("ended_at")
            if t_start and t_end:
                from datetime import datetime
                t0 = datetime.fromisoformat(t_start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(t_end.replace("Z", "+00:00"))
                elapsed_s = (t1 - t0).total_seconds()
            else:
                elapsed_s = None
        except Exception:
            elapsed_s = None

        version_display = VERSIONS[version]
        cost_info = (
            f"Midjourney {version_display} · {consumed_int} pts (~${usd:.3f})"
        )
        if elapsed_s is not None:
            cost_info += f" · {elapsed_s:.0f}s"
        cost_info += f" · seed {output.get('seed', 'auto')}"

        print(f"[Midjourney Direct] {cost_info}")

        return (tensor, cost_info, cache_key)


# ---------------------------------------------------------------------------
# MAPPINGS
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "MidjourneyGenerate": MidjourneyGenerate,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MidjourneyGenerate": "Midjourney Generate (Direct via LegNext)",
}
