# ComfyUI Midjourney Direct

Use Midjourney inside ComfyUI workflows. No Discord required.

Midjourney has no official public API. This pack wraps **LegNext.ai** — a pool-based Midjourney proxy that supplies the upstream MJ accounts so you don't need a Discord-side subscription. Pay-as-you-go at roughly $0.08 per v8.1 fast image, no monthly minimum.

## Why LegNext

When the predecessors (PiAPI, GoAPI) discontinued their Midjourney services in early 2026, LegNext absorbed both pools and is now the consolidation point of the third-party MJ market. Pure pay-as-you-go pricing, webhook + polling support, and full v6 / v7 / v8.1 / niji-6 coverage with all standard flags.

## Node

### Midjourney Generate (Direct via LegNext)

| Input | Type | Description |
|-------|------|-------------|
| `prompt` | STRING | Your imagine prompt. Don't include flags here — wire the structured inputs. |
| `version` | COMBO | v7 (default), v6.1, v6, niji-6, plus v8 / v8.1 / v8 hd which require a LegNext **Pro** subscription on top of the free pay-as-you-go tier |
| `aspect_ratio` | COMBO | 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 |
| `seed` | INT | 0 = random. Same seed + prompt + params = consistent output. |
| `style_raw` | BOOL (optional) | Default ON. Reduces MJ's heavy default stylization. |
| `stylize` | INT (optional) | --s flag. 0–1000. Lower = more prompt-faithful. |
| `weird` | INT (optional) | --weird. 0–3000. Unconventional aesthetic exploration. |
| `chaos` | INT (optional) | --chaos. 0–100. Variety across the four grid tiles. |
| `quality` | COMBO (optional) | --q. 0.25, 0.5, 1, 2, 4. Higher = more compute = more cost. |
| `turbo` | BOOL (optional) | --turbo. 4x faster, 2x cost. |
| `draft` | BOOL (optional) | --draft. Cheap, fast preview. |
| `image_weight` | FLOAT (optional) | --iw. 0–3. Only meaningful when prompt includes an image URL. |
| `negative` | STRING (optional) | --no. Comma-separated tokens to exclude. |
| `tile` | BOOL (optional) | --tile. Seamless tileable patterns. |
| `poll_interval_seconds` | FLOAT (optional) | How often to check job status. Default 3s. |
| `timeout_seconds` | FLOAT (optional) | Hard cap on total wait. Default 300s. |
| `api_key` | STRING (optional) | LegNext API key (or use env/file). |

**Outputs:** `image` (IMAGE — the 4-grid as one composite), `cost_info` (STRING — points consumed + USD estimate + elapsed), `cache_key` (STRING — for Hash Vault integration)

## API Key Setup

Resolution priority:

1. `api_key` input on the node
2. `LEGNEXT_API_KEY` environment variable
3. `legnext_api_key.txt` file in your ComfyUI root directory (recommended)

Get a key at [legnext.ai](https://legnext.ai/).

## Cost Notes

- **v8.1 fast (default)**: ~$0.08 per image
- **v8 hd / premium**: ~$0.32 per image (4x cost)
- **--turbo**: doubles the price for 4x speed
- **--draft**: cheaper preview mode, useful for ideation

The `cost_info` output reports the actual points charged by LegNext per call (read from `meta.usage.consume` in the API response). The dollar conversion is approximate — refer to your LegNext dashboard for exact billing.

## Hash Vault Integration

The `cache_key` output is a deterministic hash of every parameter that affects the generation (prompt + version + flags + seed). Wire it into [ComfyUI-API-Optimizer](https://github.com/jeremieLouvaert/ComfyUI-API-Optimizer)'s Hash Vault to skip re-paying when the same prompt + same params have already been generated.

## Important

- The output is the **4-grid as one composite image**, the same way Midjourney returns it. To work with individual tiles, use ComfyUI's Image Crop / Image Tile Split helpers downstream. Upscaling individual tiles is a v0.2 feature (would add upscale + variation nodes targeting the LegNext task-management endpoints).
- The 4-grid composite is typically `1024×1024` (each tile 512×512). Use upscale nodes if you need higher resolution per tile.
- Generations typically take 45–90 seconds via the proxy (slightly slower than direct Discord MJ). Plan workflows accordingly — wire `cache_key` into Hash Vault so re-runs return instantly at $0.

## Limitations

- v0.1 only supports the imagine path. Upscale (U1–U4), variation (V1–V4), reroll, and zoom-out are not yet exposed — they'd target separate LegNext endpoints.
- No image-prompt support yet (passing a reference image URL inside the prompt does work, but the node doesn't help you upload the image — you'd paste a URL into the prompt manually).
- LegNext occasionally rejects flag combinations Midjourney itself accepts. If a generation fails with a flag-related error, simplify the prompt parameters and retry.

## Installation

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/jeremieLouvaert/ComfyUI-Midjourney-Direct.git
pip install -r ComfyUI-Midjourney-Direct/requirements.txt
```

Restart ComfyUI. The node appears under the **Midjourney Direct** category.

## License

MIT
