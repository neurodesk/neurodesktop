---
title: Jupyter AI
description: The ACP-native Jupyter AI chat surface, its Claude/Codex/OpenCode
  personas, workspace seeding, and collaboration-stack workarounds
parent: ../architecture.md
status: current
last-reviewed: "2026-08-31"
---

# Jupyter AI

Part of [Architecture](../architecture.md). Focused tests are listed in
[Testing](../testing.md#jupyter-ai-and-acp-personas).

Jupyter AI provides a second, ACP-native JupyterLab chat surface alongside
Notebook Intelligence. Its ACP client discovers the existing Claude, Codex,
and OpenCode commands and registers all three personas. Neurodesktop installs
pinned Codex and Claude ACP adapters; OpenCode uses its native `opencode acp`
transport. Machine-facing `opencode --version` and `opencode acp` calls bypass
the interactive terminal wrapper so their output remains protocol-safe; the
`acp` branch still silently loads `NEURODESK_API_KEY` and `BR_MCP_TOKEN` from
`~/.bashrc` before exec, because the Jupyter server that spawns it never
sources that file and the `{env:...}` references in `opencode.json` would
otherwise resolve empty and fail auth in the chat. It also silently sources
`/usr/share/module.sh` before exec. Lmod exports its `module` and `ml`
functions, so OpenCode's non-interactive child Bash shells inherit the module
system even though they do not read `~/.bashrc`; all initialization output is
discarded so the ACP JSON-RPC stream stays clean. The
Codex persona starts sessions in "Agent (full access)" via the
`INITIAL_AGENT_MODE` variable exported in `environment_variables.sh`, because
codex-acp otherwise hardcodes its sandboxed "Agent" preset and ignores the
image's no-approval `~/.codex/config.toml` defaults; users can still switch
modes per chat. Its "Reasoning effort" selector starts on High because the
Codex app-server reports the `model_reasoning_effort = "high"` default seeded
in that same `config.toml`. The Claude persona starts in "Auto" permission
mode with Effort "High": claude-agent-acp resolves both from Claude Code's
own settings files, and the image seeds `permissions.defaultMode` and
`effortLevel` in the user-level `~/.claude/settings.json`
(from `config/agents/claude_settings.json`). Effort falls back to the model's
default when the selected model does not support a High level, and every
default remains a per-chat selection in each session's config options. The
personas reuse each user's agent credentials and configuration; they do not replace
Notebook Intelligence or Neurodesktop's existing model/API-key
configuration. The optional Jupyter AI magic and Jupyternaut extras are not
installed because their LiteLLM/prerelease constraints conflict with the
validated Neurodesktop stack.

Notebook Intelligence 5.3.1 imports the MCP Python SDK's v1
``mcp.server.fastmcp`` module. MCP 2 removed that module without an upper bound
in Notebook Intelligence's package metadata, so an unconstrained image can
pass ``pip check`` while the server extension fails to import. Neurodesktop
pins ``mcp>=1.28.1,<2`` until Notebook Intelligence migrates to MCP 2. The
container test imports the v1 API and Notebook Intelligence itself; the exit
status of ``jupyter server extension list`` alone is insufficient because that
command still returns success when one extension fails validation.

## Chat workspace seeding

Jupyter AI creates a chat as a ``.chat`` file and passes the file's parent
directory to each ACP persona as its working directory. A Jupyter contents
post-save hook seeds that directory with an editable copy of ``/opt/AGENTS.md``
when the first chat is created there. It never overwrites an existing
``AGENTS.md`` and fails open so a missing seed or unwritable directory cannot
prevent the chat itself from being saved. This hook is necessary because the
ACP transports do not run the interactive Codex and OpenCode wrappers that
normally seed the file. It is registered in
``config/jupyter/jupyter_server_config_extra.py``, appended to
``/etc/jupyter/jupyter_server_config.py`` at build time. Jupyter's config
system applies the trait more than once per startup (the shimmed extension
apps re-apply config), and jupyter_server warns on every ``post_save_hook``
reassignment even when the hook is unchanged, so the snippet suppresses
exactly that duplicate self-registration warning next to the single
registration site; a different hook overriding ours still warns.

## Collaboration stack and server workarounds

Jupyter AI 3.1.2 is installed with Jupyter Collaboration 5.0.0. Its published
collaboration and document-provider frontends target JupyterLab 4.6 and
`@jupyter/ydoc` 4, so the image uses the wheels without rebuilding them. The
image test requires both extensions to report `OK` in `jupyter labextension
list --verbose`.

Jupyter AI 3.1.x requires `jupyter-server-documents`; removing the package or
disabling its server extension is not a supported fallback for this release.
Its server-side notebook execution sends cell outputs over the Yjs WebSocket,
while widget model comms still use the kernel WebSocket. Those independent
channels can deliver a widget view before its model, which `ipywidgets` 8.1.8
made permanent as `Error displaying widget: model not found` or `Loading
widget...`. Neurodesktop pins `ipykernel` 6.31.0 so widget comms stay on the
main shell instead of using JupyterLab's experimental per-target comm
subshells. Neurodesktop also pins `ipywidgets` 8.1.9 and
`jupyterlab_widgets` 3.0.17, whose widget manager waits briefly for late model
registration. Its upstream two-second bound is still too short for complex
layouts such as a ``VBox`` containing delayed ``HBox`` models. Neurodesktop
extends that bound to ten seconds and publishes the changed widget-manager
chunk and remote entry under new content-derived names. Waiting cannot recover
a ``comm_open`` that the kernel WebSocket dropped. If the manager has completed
its initial restore and the model is still absent after ten seconds, it makes
one deduplicated restore-lifecycle request and checks again before reporting
``model not found``. Concurrent missing outputs share that request. The
manager records completion for thirty seconds, so several absent saved models
cannot each start another full bulk request after their ten-second waits.
Recovery runs only after the initial restore has completed. Starting another
restore while that first restore is stuck would create competing control comms;
the kernel nudge and bounded control-state retries handle that failure instead.
The recovery enters ``restoreWidgets()`` rather than calling
``_loadFromKernel()`` directly. This keeps ``_kernelRestoreInProgress`` true
while retries reconnect the kernel, so the connection-status callback cannot
start a second restore against the same manager.
If a later bulk request still fails during a connection interruption, the
renderer keeps its MIME model while showing the error. A subsequent successful
manager restore reruns that renderer. A late model registration emits the same
notification, covering a delayed ``comm_open`` that arrives without another
bulk restore. Rerendering consumes the pending MIME model before asynchronous
view creation, so adjacent notifications cannot add two views to one output.

The same widget manager abandons a bulk kernel-state request after four
seconds. Its per-model fallback can overlap the late bulk response and leave
restoration unfinished. Neurodesktop gives the initial bulk request ten
seconds, then makes at most two fresh retries with a full thirty-second response
window before entering the legacy per-model fallback. This preserves time for
complex state while recovering promptly when a request is lost. A control comm
opened while a second client's kernel connection is settling can also be lost
before it reaches ipykernel. The manager also waits for the kernel connection to
report `connected` before opening the control comm. Upstream waits for the
session context but can start restoration while its kernel channel is still
connecting; that restoration then suppresses the connected-event retry until
after the request has already been lost. The manager also attempts one bounded
kernel-info probe before the control comm opens. JupyterLab marks the browser
WebSocket connected before the kernel bridge is necessarily ready, and its own
initial kernel-info request documents that it can be lost during this interval.
If the readiness probe times out, the manager asks JupyterLab to reconnect that
browser's kernel channel before continuing. It also reconnects before each
bulk retry, which replaces a WebSocket that reports `connected` but no longer
delivers comm traffic. Each reconnect is bounded at ten seconds; restoration
continues into its bounded control-state request if reconnecting cannot finish.
The retry bypasses the one-time readiness probe after reconnecting so probe
timeouts cannot consume the retry's rendering window.

JupyterLab 4.6 assigns the manager to existing renderers and replaces the
panel's shared renderer factory in adjacent synchronous operations. The
existing-renderer iterator is lazy, so it also sees renderers created while the
manager owner was resolving. Neurodesktop does not reorder those operations.
It defensively watches code-cell output-length changes and attaches the manager
to any manager-less ``WidgetRenderer`` inserted outside the normal factory
path. A weak set prevents duplicate attachment.

The root of those settling losses is server-side. `jupyter-server-documents`
replaces jupyter_server's kernel WebSocket connection with a per-connection
`AsyncKernelClient`, but skips upstream's connection "nudge". A freshly
connected ZMQ IOPub SUB socket silently drops everything the kernel publishes
before its subscription arrives (the slow-joiner race), so a new client's
bulk-state reply could vanish with no error anywhere. Neurodesktop's anchored
backend patch restores the nudge: `connect()` repeats `kernel_info_request`
on transient shell and control sockets until a reply and at least one IOPub
message prove the bridge, before the channel listen tasks start. The transient
sockets keep nudge replies away from the frontend, and the proving IOPub
message is forwarded to the WebSocket rather than consumed. A busy kernel is
skipped (its subscriptions are long established, and shell requests would
queue behind the running execution), and the nudge is bounded at ten seconds,
after which the connection proceeds with the old behavior. The logic lives in
`config/jupyter/neurodesktop_kernel_nudge.py`, installed into the package as
`jupyter_server_documents/_neurodesktop_kernel_nudge.py`; the frontend probe,
reconnect, and retry bounds above remain as defense in depth.

The ipywidgets backend also stores only the most recently opened widget control
comm. When two JupyterLab clients restore the same kernel concurrently, a state
request received from one client can therefore send its response to the other.
Neurodesktop's anchored backend patch captures the requesting comm in its
callback so both widget managers finish restoring their own state.

ipyniivue 2.4.4 supplies anywidget with a 5 MB ESM file through a synchronized
model trait. Without a workaround, nine NiiVue models send and import nine
copies of that bundle while their comms compete with notebook output delivery.
Neurodesktop moves the heavy module into one content-hashed JupyterLab static
asset and leaves a small bootstrap in the Python package. The shared module
exports a factory, and each bootstrap invocation creates a separate widget
definition so its NiiVue instance and scene synchronization state stay
model-local. Upstream also creates a second ``Disposer`` inside ``render``
and never disposes it, so the child-model listeners registered there outlive
the model: they retain the NiiVue instance and its volumes, a later trait
change runs them against the context this patch has already released, and a
re-render registers a duplicate listener set because the two disposers
disagree about which child models are set up. The patched definition shares
one disposer per model, owned by the same closure as the instance and
released with it. Upstream polls every 30 ms after a focused canvas
synchronizes;
the patched definition instead computes and sends one scene delta directly
from each focused ``NiiVue.sync()`` event, then does no work while the viewer
is idle. When a model is destroyed, the definition runs NiiVue cleanup and
requests ``WEBGL_lose_context``. Removing only a view does not release the
context, because ipyniivue supports moving and redisplaying the same model.

The image regression emits fragmented carriage-return stream updates, delays a
real ``HBox`` comm for three seconds, and creates nine NiiVue models, one
loading a generated NIfTI volume so real image data crosses the widget comm.
The stream and all widgets must render without a YDoc output exception, the
nine models must produce exactly one fetch of the shared ipyniivue bundle, and
re-execution must not exhaust WebGL contexts. The browser removes one live
model from the frontend registry, supplies the retained model through the
manager's restore lifecycle, and requires concurrent missing-model lookups to
share one recovery. Every injected load observes
``_kernelRestoreInProgress``. A sequential absent model must reuse the
thirty-second negative cache. A transition table then fails real
``_loadFromKernel()`` retries at the control-comm and comm-info boundaries. Two
renderers must retain their MIME models, replace their visible errors after one
restore notification, and create exactly one view each. Adjacent restore
notifications must remain single-flight, and a model registration without a
bulk restore must wake a failed renderer. The browser also walks every widget renderer after execution,
re-execution, and replay and requires its manager promise to resolve. It then
inserts a real manager-less renderer into an output, emits the output-length
signal, and requires the defensive watch to repair it. It also interacts
with every canvas, observes the scene updates, then requires both model traffic
and shared-asset interval activity to remain stopped while idle. It re-executes
the cell and restores the populated room in a second client. Companion image
tests drive the patched ``OutputProcessor`` directly over backspace and
interleaved stdout/stderr fragments, and prove one rejected frame cannot stop a
room's message queue. Jupyter
AI 3.2 plans to make RTC optional, but Neurodesktop will not remove the stable
3.1 dependency by adopting an alpha release.

Notebook rooms disable the separate outputs service. In that path,
`jupyter-server-documents` 0.3.3 converts kernel messages to plain Python
dictionaries before appending them to the cell's CRDT output array. JupyterLab
expects each entry to remain a Y.Map and each stream output's ``text`` value to
remain a Y.Text. A plain dictionary fails when `appendStreamOutput()` calls the
missing map `get()` method; a Y.Map containing a plain string fails on the next
call to the missing text `insert()` method. Consecutive stream messages must
also update one CRDT output. Separate maps make JupyterLab merge the fragments
locally and write the same fragment back into the room, which duplicates text
when another client joins and can apply a stream delta before its output row.
The backend patch keeps the outputs-service representation unchanged, requests
CRDT maps, constructs CRDT stream text, and processes carriage returns while
coalescing each contiguous stdout or stderr run into one map. The cursor rules
are a port of JupyterLab's `Private.processText`/`addText`
(`packages/outputarea/src/model.ts`) kept in
`config/jupyter/neurodesktop_stream_output.py`; the patcher installs it into
the package as `outputs/_neurodesktop_stream.py` and splices only thin
delegating methods, so the algorithm stays reviewable and is pinned by parity
vectors in `tests/unit/test_neurodesktop_stream_output.py`. Between messages
the processor retains only a per-cell `(length, cursor, running hash)` —
never the accumulated text — and appends blindly while the cursor sits at
the end and the fragment contains no `\r` or `\b`, so ordinary
newline-terminated output of any size is neither held in memory for the life
of the room nor rebuilt on every fragment. Rewinding fragments materialize
the text once and validate the stored cursor against the running hash, so a
concurrently replaced output conservatively appends at the end; that
materialization matches what JupyterLab's own `addText` does per fragment.
pycrdt `Text` indexes by UTF-8 bytes, so every CRDT index is converted from
the code-point cursor arithmetic — the unit tests pin this with a
byte-indexed fake.

The same server-side executor bypasses JupyterLab's normal code-cell execution
path, which marks a cell trusted before its outputs arrive. Without that state,
JupyterLab refuses unsafe rich renderers and displays an ipywidget's
``text/plain`` representation instead. The anchored server-documents patch
marks user-executed code cells trusted **after** the session and kernel
guards, request preparation, and the execution-scheduled callback. It grants
trust inside the request ``try``, directly before dispatch. Granting trust
earlier, as the first version of this workaround did, trusts a cell when
Run is pressed with no kernel and nothing executes; because this path does
not clear outputs the way JupyterLab's ``clearExecution()`` does, stale
untrusted rich output would then render with unsafe renderers. The executor
records the previous trust value immediately before dispatch and restores it
when the request returns 409, returns another non-success response, or throws.
The patcher migrates an image built with the earlier placement rather than
leaving it in place. The image regression executes a
real ``IntSlider`` through JupyterLab to distinguish a rendered widget from its
plain Python representation. Because Jupyter serves federated extension assets
with a one-year immutable cache, the patch leaves the upstream bundle intact,
publishes the changed chunk and remote entry under new content-derived names,
and updates the extension manifest. Reusing the original hashed filename would
leave browsers that opened JupyterLab before the update on the broken code.

The package also has a reconnect data-loss path tracked upstream as
[`jupyter-server-documents` issue 305](https://github.com/jupyter-ai-contrib/jupyter-server-documents/issues/305).
After a room is freed or the server restarts, an open browser can reconnect
with Yjs history the new room does not know. The browser repairs that divergence
by removing its client-owned ordered items and applying the persisted server
state in one transaction; its SyncStep2 reply is the only message carrying
those tombstones back to the server. Version 0.3.3 waits five seconds for that
reply. A busy browser serializing several document handshakes can miss the
deadline, after which the server drops the late reply and disconnects the
client. The next reconnect is divergent again. The old frontend repair then
clears the full ordered range, including cells it just received from the
server, and a later SyncStep2 can autosave the canonical one-blank-cell
notebook. This incident's 759-byte file beside a complete checkpoint was that
exact signature; the NiiVue renderer had not run and was not the deletion
source.

Neurodesktop closes both sides of the path. The server keeps pending
SyncStep2 futures per client, treats the timeout only as a bound on paused
broadcasts, leaves the client connected, and applies a late reply from the
message queue. The frontend repair receives the server state vector and
deletes only ordered Yjs item ranges not covered by it. Server-owned items are
therefore untouched on every repeated repair. The frontend change is published
in the same content-hashed server-documents chunk as the cell-trust change so
existing browser caches cannot retain the destructive implementation.

`jupyter-server-documents` 0.3.3 also has an upstream stale-client race in which
one queued update can terminate a chat room's background message processor.
Neurodesktop applies an exact-source, build-time workaround for upstream issue
271: missing-client lookup fails cleanly, and one rejected frame cannot stop the
rest of the room queue. The anchored patch intentionally fails the image build
if a future package release changes any backend or frontend seam, forcing the
workarounds to be reassessed rather than silently carried forward.

### Retiring the anchored workarounds

Nothing above retires itself. Every affected package is pinned exactly
(`jupyter-server-documents==0.3.3`, `ipyniivue==2.4.4`, `ipywidgets==8.1.9`,
`jupyterlab_widgets==3.0.17`, `ipykernel==6.31.0`), so an upstream merge
changes nothing until a pin is bumped. On a bump, each anchored seam lands in
one of three states, and only two of them are loud:

- **Upstream changed the anchored lines** (merged our fix, or refactored
  them): the `BEFORE` text no longer matches and the marker is absent, so the
  patcher raises `anchor did not match exactly once; reassess ...` and the
  image build fails. This is the signal to *remove* the seam, not to
  re-anchor it — re-anchor only when the release notes show the bug is still
  open.
- **Upstream left the lines alone**: the patch applies as before; keep it.
- **Upstream fixed the bug elsewhere** without touching the anchored lines:
  the patch applies on top of the fix, silently. Read the release notes for
  every bump and rely on the behavioral image tests (replayed stream text,
  queue guard, nudge, widget rendering), which assert outcomes rather than
  markers.

Frontend seams match minified identifiers (`e.model.trusted=!0`, ipyniivue's
`vA`/`BC`/`S0`, the widget-manager retry loop), so any upstream rebuild
renames them and they fail loudly on *every* version bump, merged fix or not.

Signals that a retirement is due: the weekly `package-update-radar` tracking
issue (it surveys every `pip` pin and `ARG *_VERSION`), and GitHub
notifications on the upstream issues and PRs Neurodesk opened for these
bugs. Retirement conditions per seam:

| Seam (patcher) | Retires when upstream ships | Notes |
| --- | --- | --- |
| Issue-271 client lookup + queue guard (`patch_jupyter_server_documents.py`) | jupyter-server-documents fix for [#271](https://github.com/jupyter-ai-contrib/jupyter-server-documents/issues/271) | composes with the #305 handshake changes |
| Late SyncStep2, per-client futures, `handshake_timeout`, frontend divergent repair (same patcher) | the [#305](https://github.com/jupyter-ai-contrib/jupyter-server-documents/issues/305) PRs (albertmichaelj's `darden/fixes` design) | frontend half is minified-anchored |
| CRDT stream outputs + `neurodesktop_stream_output.py` (same patcher) | fix for [#306](https://github.com/jupyter-ai-contrib/jupyter-server-documents/issues/306) | silent-case risk: check release notes for any other output-path change |
| Server-execution cell trust, frontend (same patcher) | fix for [#307](https://github.com/jupyter-ai-contrib/jupyter-server-documents/issues/307) | |
| Kernel WebSocket nudge + `neurodesktop_kernel_nudge.py` (same patcher) | the nudge issue/PR on jupyter-server-documents | once merged, re-evaluate the frontend probe/reconnect/retry bounds for removal — they masked this transport hole |
| Widget late-model retry, restore-lifecycle recovery, and failed-render rerender (`patch_jupyterlab_widgets.py`) | ipywidgets event-driven `get_model` ([#4026](https://github.com/jupyter-widgets/ipywidgets/issues/4026)) plus recovery after a lost `comm_open` | a `get_model_timeout` setting replaces only the bounded-wait half; retire the rerender seams when upstream keeps failed MIME models, wakes them on registration, and makes rerender single-flight |
| Widget renderer output watch (same patcher) | an upstream insertion API guarantees that extensions and collaboration cannot bypass the panel's manager-backed factory | the browser test injects a manager-less renderer and requires the watch to repair it |
| ipyniivue factory, cleanup, event-driven scene sync (`patch_ipyniivue.py`) | ipyniivue [#298](https://github.com/niivue/ipyniivue/issues/298) and [#299](https://github.com/niivue/ipyniivue/issues/299) | the shared-bundle relocation is Neurodesk's optimization and stays; only the in-bundle anchors retire |
| `ipykernel` 6.x pin (Dockerfile) | ipykernel 7 comm subshells handle delayed server-executed widgets | unrelated to the PRs; watch ipykernel release notes |
| MCP 1.x pin (Dockerfile) | Notebook Intelligence imports the MCP 2 API without `mcp.server.fastmcp` | keep `fastmcp` independently on the version required by `jupyter-server-mcp` |

To remove a seam: bump the pin in the `Dockerfile` and in the version
assertions of `tests/container/test_widget_compatibility_image.py` and
`tests/unit/test_jupyter_server_documents_patch.py`; build; delete the
failing seam's `BEFORE`/`AFTER` constants, marker, partial-state check, and
write, together with its unit-test fixture and any module it installs (and
that module's Dockerfile bind mount and tests); keep the behavioral image
tests and drop only marker-presence assertions; update the AGENTS.md bullet
and this page. When a patcher has no seams left, delete the script, its
Dockerfile `RUN` layer, and its unit-test file.

`jupyter-ai-acp-client` 0.2.1 logs every streamed message chunk at INFO — two
lines per chunk, where a chunk is often a few characters — plus one line per
tool-call start and per once-a-second progress tick, so a single persona reply
floods the Jupyter server log with thousands of lines. A second anchored
build-time patch (`patch_jupyter_ai_acp_client.py`) demotes those four
per-event log statements to DEBUG; rarer events such as permission requests
stay at INFO. Like the issue-271 patch, it fails the image build if a future
release changes any source seam.

`jupyter-server-mcp` 0.2.1 starts FastMCP through its own embedded HTTP
runner and prints the FastMCP ASCII banner unconditionally, ignoring
FastMCP's `show_server_banner` setting. The image exports
`FASTMCP_SHOW_SERVER_BANNER=0` and a third anchored build-time patch
(`patch_jupyter_server_mcp.py`) gates the banner call on that setting, so
the box no longer lands in the server log on every boot. It follows the same
fail-loud contract as the other anchored patches.
