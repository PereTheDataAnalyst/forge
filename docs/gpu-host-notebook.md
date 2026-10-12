# Running the worker without a local GPU

The N-ATLaS weights need about 16 GB of GPU memory in 16-bit precision, so most laptops cannot host them. The notebook [`inference/natlas-worker/notebooks/natlas-forge-gpu-worker.ipynb`](../inference/natlas-worker/notebooks/natlas-forge-gpu-worker.ipynb) runs the same worker from this repository on a free dual-T4 notebook (about 30 GB of GPU memory in total) and publishes it through a temporary HTTPS address. Forge then talks to it exactly as it would talk to a local worker.

This is a bridge for development and for integration evidence. It is not a production deployment.

## What stays the same

- The worker code is `inference/natlas-worker`. The notebook clones it from this repository.
- The model is the official `NCAIR1/N-ATLaS` weights, loaded in float16 because T4 GPUs have no native bfloat16, and split across both GPUs with `device_map=auto`. There is no quantisation and no substitute model.
- The gateway authenticates to the worker with a shared bearer token: `NATLAS_WORKER_TOKEN` on the worker side and `NATLAS_INFERENCE_TOKEN` in the backend `.env`.

## Before you start

| Item | Detail |
| --- | --- |
| Hugging Face | An account that accepted the conditions on <https://huggingface.co/NCAIR1/N-ATLaS>, and a **Read** token created in that same account |
| Kaggle | An account with a verified phone number. Without it the GPU options and the Internet switch stay disabled |
| Worker token | Created on your PC by `.\scripts\connect-worker.ps1`. It is stored in `inference\natlas-worker\.env` and is never printed |

The Hugging Face token and the worker token are different secrets. Do not use one for the other.

## Steps

1. Create the worker token and the placeholder gateway settings on your PC:

   ```powershell
   Set-Location C:\Projects\forge
   .\scripts\connect-worker.ps1
   ```

   The command prints that the URL and the token were set, but it does not show the token.
2. Put the token on the clipboard without showing it, and check that it worked. The check prints 64:

   ```powershell
   $line = Select-String -Path .\inference\natlas-worker\.env -Pattern '^NATLAS_WORKER_TOKEN=' | Select-Object -First 1
   $line.Line.Substring("NATLAS_WORKER_TOKEN=".Length) | Set-Clipboard
   (Get-Clipboard).Length
   ```

3. In Kaggle choose Create, then New Notebook, then File, then Import Notebook, and upload the notebook file.
4. Settings menu, Accelerator: **GPU T4 x2**. Right-hand panel (Notebook), Session options: **Internet On**.
5. Add-ons, then Secrets: create `NATLAS_WORKER_TOKEN` (paste the clipboard) and `HF_TOKEN` (the Hugging Face token), and attach both to the notebook.
6. Press Run All. The first run downloads about 16 GB.
7. Cell 8 prints a `https://...trycloudflare.com` address. On your PC run `.\scripts\connect-worker.ps1 -Url <address>`, restart the backend with `.\scripts\dev.ps1`, and run `.\scripts\smoke-day1.ps1`.

The menu names above were observed on 11 October 2026. Kaggle can change them.

## What each cell does

| Cell | Purpose |
| --- | --- |
| 1 | Checks the GPUs. It needs two T4s, or one GPU with 24 GB or more |
| 2 | Checks the internet, installs the worker libraries, and shows pip's own error if it fails |
| 3 | Reads both secrets and checks that Hugging Face lets the account download the gated model |
| 4 | Clones this repository |
| 5 | Starts the worker on `127.0.0.1:8081` |
| 6 | Waits until the model is loaded and prints the worker's information |
| 7 | Sends one real question directly to the worker |
| 8 | Starts a Cloudflare quick tunnel and prints the address and the command for your PC |
| 9 | Keeps the worker and the tunnel running and reports every five minutes |

## Limits

- The tunnel address changes every time the notebook runs. Run `connect-worker.ps1 -Url` again and restart the backend.
- Free notebooks have a weekly GPU quota and a maximum session length. Keep the tab open while you need the worker. Nothing here promises uptime.
- Quick tunnels do not carry server-sent events, so streaming responses cannot be offered through this path.
- Cloudflare closes requests that take longer than about 100 seconds. The notebook sets `NATLAS_WORKER_MAX_GENERATION_SECONDS` to 90 so a reply ends first.
- Official N-ATLAS API credentials and compute credits go to shortlisted teams. Plan to replace this bridge when they arrive.

## Security

- The worker is reachable from the internet while the tunnel runs. The only protection is the 64-character bearer token, compared in constant time. Keep the notebook private and never share the token or a screenshot that shows it.
- To replace the token, set `NATLAS_WORKER_TOKEN=CHANGE_ME` in `inference\natlas-worker\.env`, run `.\scripts\connect-worker.ps1`, and update the Kaggle secret.
- The notebook never prints either secret. The worker never logs prompts or generated text.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md). Verification status is recorded in [status.md](status.md).
