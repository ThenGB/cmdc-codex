# cmdc-codex

On Windows, double-click install.cmd for a visible terminal that remains open after success or failure. The installer detects the official CommandCode CLI login, validates it with CommandCode, and asks for an API key using hidden input when no valid login exists.

Use CommandCode models alongside native authenticated models in Codex Desktop and CLI through the `cmdc-codex` gateway provider. Native models keep their original IDs, metadata, credentials and default selection. CommandCode models use the `commandcode/` prefix so selecting a model determines its route.

## Requirements

- Node.js 24+ (including zstd support), Python 3.11+, and signed-in Codex.
- Windows Task Scheduler and `pythonw.exe`, or Linux systemd with a working user session.
- An active CommandCode account/API key with Provider API access.

## Install

Download and extract a release, inspect its source, and verify the archive against `SHA256SUMS`.

Windows PowerShell:

```powershell
./install.ps1
```

Linux:

```sh
sh install.sh
```

The installer reuses `~/.commandcode/auth.json` if it contains an API key. Otherwise it asks for a key using hidden input and stores it only in the per-user installation directory (restricted permissions). Never put your key in a command argument, repository or issue. Existing Codex and CommandCode auth files are never modified or deleted.

Restart Codex and open a new chat. Select native models as before, or select a model ending in `(CommandCode)`. The router preserves native credentials and sends only the CommandCode key to CommandCode.

Installation is per user, without administrator/root access. Existing custom providers or static model catalogs are not overwritten: restore native provider configuration first. An existing local native gateway is preserved as the native upstream; it must keep running independently. The router listens only on `127.0.0.1:17842` and requires the current Codex bearer credential. The configured gateway uses existing Codex authentication (`requires_openai_auth = true`) and lets Desktop display the live catalog without applying its built-in OpenAI account model whitelist. Existing conversations can retain provider `openai`, so `openai_base_url` also points to the router. The original native upstream is saved separately in router settings and restored on uninstall; auth files remain untouched.

On Windows, Task Scheduler runs a `pythonw.exe` supervisor that launches Node without a console. The supervisor waits for Node and returns its exit status, so task retries still work. Closing an interactive console cannot terminate this router through a shared console control event.

## Automatic model and effort updates

The router reads CommandCode's authenticated client catalog, public endpoint support, account/org subscription and extra-credit metadata. Model IDs, context lengths, modalities and effort options come from the API. Only entitled models advertising `/responses` are added. The plan ordering matches the CommandCode CLI; unknown plan identifiers are conservatively excluded rather than granted access.

Refresh follows the server's `refreshAfterSeconds` (currently 900 seconds), checked every minute and when requested. Changing the locally stored API key forces the next refresh. Reopening the selector/refreshing the Codex model list loads the refreshed catalog; the app can cache its own list independently. Native models are fetched from the native authenticated endpoint, not pinned to a bundled list. A CommandCode refresh failure leaves native models available. A transient refresh failure may keep the last successful CommandCode catalog for the same key; upstream inference still enforces current access.

```sh
python3 cmdc-codex.py status
python3 cmdc-codex.py refresh
```

On Windows use `python` instead of `python3`.

Effort values are not invented. `off` in the CommandCode client catalog is exposed as Codex `none`; other advertised levels are preserved. Unsupported effort choices are rejected. Effort changes use a fresh top-level `reasoning.effort`, rather than assuming support for OpenAI's incremental effort-update protocol.

## Safe uninstall

```powershell
./uninstall.ps1
```

```sh
sh uninstall.sh
```

Uninstall stops only the owned user service/task and restores only the managed provider and effort settings. Unrelated configuration changes and existing credential stores remain intact. If a managed setting or service has been edited after installation, uninstall refuses to overwrite it; review the local backup and journal to resolve the conflict. Files outside the known installation manifest are retained.

If you manually removed the routing config while leaving an installation present, start its service first, then run `python cmdc-codex.py activate`. Recovery verifies that both live catalogs are available before activating the gateway, and uses your current config as the uninstall baseline. Restart Desktop afterward to replace its existing provider connection and cached picker state.

## Limits and diagnostics

The catalog cache is persisted with an API-key fingerprint and reloaded after service restart. The same key can reuse the last successful catalog during an upstream outage; a changed or missing key cannot reuse another account's catalog.

The test suite verifies catalog TTL reuse, forced/auth-triggered refreshes, prompt cache key forwarding, and cached token passthrough. Run verify_release.py to check tracked files and reachable Git history; pass --archive after building a release ZIP to scan that archive with the same local credential values. The report never prints credential contents.

- SSE responses, cancellation, client-side function/custom tools and cache usage pass through. WebSockets fall back to HTTP/SSE.
- Multimodal tool results retain their text and images, with images sent as image messages. This avoids counting image base64 as plain text in CommandCode's tool-result conversion; no history is silently trimmed.
- External models cannot inherit every OpenAI-hosted feature. Image input requires upstream support; hosted search, remote compaction, voice and other specialized features depend on the selected provider. Native routes remain available for native features.
- Prompt caching is upstream-managed. Cold prompts, effort changes, different tools/prefixes and cache expiry can incur normal input cost. Check `usage.input_tokens_details.cached_tokens`; a high warm-cache hit does not guarantee every request or invoice discount.
- The local request log contains timestamps, model, effort, stream flag and whether a cache key was supplied. It contains no prompts, keys, authorization headers or cache-key values.
- A Python 3.11 and Node.js 24 test suite is included: `python test_installer.py`, `python test_windows_launcher.py`, and `node --test src/*.test.cjs`.

Metadata endpoints are taken from the official CommandCode client and may change. See [CommandCode Provider API](https://commandcode.ai/docs/provider), [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), and [DeepSeek effort controls](https://api-docs.deepseek.com/guides/thinking_mode/).

## License

Apache-2.0. Includes an express patent grant and warranty disclaimer; no license eliminates every legal or operational risk. See `LICENSE`.
