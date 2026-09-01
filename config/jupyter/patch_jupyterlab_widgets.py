#!/usr/bin/env python3
"""Harden ipywidgets' bounded frontend model and control-state restore.

``ipywidgets==8.1.9`` retries a missing frontend model for two seconds. Server
Documents sends notebook output over its collaboration WebSocket while widget
comms use the kernel WebSocket, so a complex output can legitimately arrive
more than two seconds before one of its nested models. A comm opened before a
fresh IOPub subscription settles can be lost altogether. After the bounded
wait, enter the manager's restore lifecycle once so the browser can recover
that model without allowing reconnect events to start a competing restore.
Keep failed renderers retryable on a later restore or model registration, and
consume each pending rerender before asynchronous view creation so adjacent
notifications cannot create duplicate views.

The widget manager also abandons its bulk control-state request after four
seconds and falls back to individual model requests that can be stranded by a
late bulk response. A control comm opened while a second client's kernel
connection is settling can also be lost before it reaches ipykernel. Wait for
that connection, probe it, and reconnect a stale channel before opening or
retrying the bulk request. Make up to two retries before falling back.

During manager setup, watch code-cell output changes and attach the manager to
any manager-less widget renderer that appears outside the normal factory path.
JupyterLab 4.6 shares the panel renderer registry with its cells, so the
manager-backed factory handles ordinary later outputs; the watch is a defensive
repair for renderers inserted by collaboration or another extension.
Publish every anchored change under new content-derived asset names because
Jupyter serves the original federated extension URLs as immutable for one year.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


MODEL_RETRY_MARKER = "neurodesktop-widget-model-retry"
MODEL_RECOVERY_MARKER = "neurodesktop-widget-missing-model-recovery"
MODEL_RECOVERY_COOLDOWN_MARKER = (
    "neurodesktop-widget-missing-model-recovery-cooldown"
)
MODEL_RECOVERY_LIFECYCLE_MARKER = (
    "neurodesktop-widget-missing-model-restore-lifecycle"
)
CONTROL_TIMEOUT_V1_MARKER = "neurodesktop-widget-control-timeout"
CONTROL_TIMEOUT_MARKER = "neurodesktop-widget-control-timeout-staged-retry"
CONTROL_RETRY_V1_MARKER = "neurodesktop-widget-control-retry"
CONTROL_RETRY_V2_MARKER = "neurodesktop-widget-control-retry-v2"
CONTROL_RETRY_MARKER = "neurodesktop-widget-control-retry-reconnect"
CONNECTION_WAIT_V1_MARKER = "neurodesktop-widget-kernel-connection-wait"
CONNECTION_WAIT_MARKER = "neurodesktop-widget-kernel-connection-reconnect"
LEGACY_RENDERER_MANAGER_ORDER_MARKER = (
    "neurodesktop-widget-manager-factory-first"
)
RENDERER_OUTPUT_WATCH_MARKER = "neurodesktop-widget-output-watch"
RENDERER_RECOVERY_RERENDER_MARKER = (
    "neurodesktop-widget-rerender-after-recovery-failure"
)
RENDERER_RERENDER_SINGLE_FLIGHT_MARKER = (
    "neurodesktop-widget-rerender-single-flight"
)
MODEL_REGISTRATION_RERENDER_MARKER = (
    "neurodesktop-widget-rerender-on-model-registration"
)
CACHE_SAFE_MARKER = "neurodesktop-widget-retry-cache-safe-entry"

MODEL_RETRY_BEFORE = (
    "let o=Date.now();for(;Date.now()-o<2e3;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
)

MODEL_RETRY_AFTER = (
    f"let o=Date.now();/*{MODEL_RETRY_MARKER}*/"
    "for(;Date.now()-o<1e4;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
)

MODEL_RECOVERY_V1_AFTER = (
    f"let o=Date.now();/*{MODEL_RETRY_MARKER}*/"
    "for(;Date.now()-o<1e4;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
    f"if(/*{MODEL_RECOVERY_MARKER}*/"
    "this.restoredStatus&&this._loadFromKernel){"
    "let neurodeskRecovery=this.__neurodesktopMissingModelRecovery;"
    "neurodeskRecovery||("
    "neurodeskRecovery=this._loadFromKernel(),"
    "this.__neurodesktopMissingModelRecovery=neurodeskRecovery);"
    "try{await neurodeskRecovery}catch(neurodeskError){}finally{"
    "this.__neurodesktopMissingModelRecovery===neurodeskRecovery&&"
    "(this.__neurodesktopMissingModelRecovery=null)}"
    "if(void 0!==(t=this._models[e]))return t}"
)

MODEL_RECOVERY_V2_AFTER = (
    f"let o=Date.now();/*{MODEL_RETRY_MARKER}*/"
    "for(;Date.now()-o<1e4;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
    f"if(/*{MODEL_RECOVERY_MARKER}*/"
    "this.restoredStatus&&this._loadFromKernel){"
    "let neurodeskRecovery=this.__neurodesktopMissingModelRecovery;"
    f"let neurodeskRecoveredAt=/*{MODEL_RECOVERY_COOLDOWN_MARKER}*/"
    "this.__neurodesktopMissingModelRecoveryAt||0;"
    "neurodeskRecovery||Date.now()-neurodeskRecoveredAt<30e3||("
    "neurodeskRecovery=this._loadFromKernel(),"
    "this.__neurodesktopMissingModelRecovery=neurodeskRecovery);"
    "if(neurodeskRecovery)try{await neurodeskRecovery}"
    "catch(neurodeskError){}finally{"
    "this.__neurodesktopMissingModelRecoveryAt=Date.now();"
    "this.__neurodesktopMissingModelRecovery===neurodeskRecovery&&"
    "(this.__neurodesktopMissingModelRecovery=null)}"
    "if(void 0!==(t=this._models[e]))return t}"
)

MODEL_RECOVERY_AFTER = (
    f"let o=Date.now();/*{MODEL_RETRY_MARKER}*/"
    "for(;Date.now()-o<1e4;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
    f"if(/*{MODEL_RECOVERY_MARKER}*/"
    "this.restoredStatus&&this.restoreWidgets){"
    "let neurodeskRecovery=this.__neurodesktopMissingModelRecovery;"
    f"let neurodeskRecoveredAt=/*{MODEL_RECOVERY_COOLDOWN_MARKER}*/"
    "this.__neurodesktopMissingModelRecoveryAt||0;"
    "neurodeskRecovery||Date.now()-neurodeskRecoveredAt<30e3||("
    "neurodeskRecovery=("
    f"/*{MODEL_RECOVERY_LIFECYCLE_MARKER}*/"
    "this.restoreWidgets(this.context&&this.context.model,"
    "{loadKernel:!0,loadNotebook:!1})),"
    "this.__neurodesktopMissingModelRecovery=neurodeskRecovery);"
    "if(neurodeskRecovery)try{await neurodeskRecovery}"
    "catch(neurodeskError){}finally{"
    "this.__neurodesktopMissingModelRecoveryAt=Date.now();"
    "this.__neurodesktopMissingModelRecovery===neurodeskRecovery&&"
    "(this.__neurodesktopMissingModelRecovery=null)}"
    "if(void 0!==(t=this._models[e]))return t}"
)

CONTROL_TIMEOUT_BEFORE = (
    'setTimeout(()=>l("Control comm did not respond in time"),4e3)'
)

CONTROL_TIMEOUT_V1_AFTER = (
    "setTimeout(()=>l("
    f'/*{CONTROL_TIMEOUT_V1_MARKER}*/'
    '"Control comm did not respond in time"),3e4)'
)

CONTROL_TIMEOUT_AFTER = (
    "setTimeout(()=>l("
    f'/*{CONTROL_TIMEOUT_MARKER}*/'
    '"Control comm did not respond in time"),'
    "this.__neurodesktopControlRetry?3e4:1e4)"
)

CONTROL_RETRY_BEFORE = (
    "}catch(e){return this._loadFromKernelModels()}let o=e.states"
)

CONTROL_RETRY_V1_AFTER = (
    "}catch(e){if(!this.__neurodesktopControlRetry){"
    f"/*{CONTROL_RETRY_V1_MARKER}*/"
    "this.__neurodesktopControlRetry=!0;"
    "try{return await this._loadFromKernel()}"
    "finally{this.__neurodesktopControlRetry=!1}}"
    "return this._loadFromKernelModels()}let o=e.states"
)

CONTROL_RETRY_V2_AFTER = (
    "}catch(e){let neurodeskRetries="
    "this.__neurodesktopControlRetryCount||0;"
    "if(neurodeskRetries<2){"
    f"/*{CONTROL_RETRY_V2_MARKER}*/"
    "this.__neurodesktopControlRetryCount=neurodeskRetries+1;"
    "this.__neurodesktopControlRetry=!0;"
    "try{return await this._loadFromKernel()}finally{"
    "this.__neurodesktopControlRetryCount=neurodeskRetries;"
    "this.__neurodesktopControlRetry=neurodeskRetries>0}}"
    "return this._loadFromKernelModels()}let o=e.states"
)

CONTROL_RETRY_AFTER = (
    "}catch(e){let neurodeskRetries="
    "this.__neurodesktopControlRetryCount||0;"
    "if(neurodeskRetries<2){"
    f"/*{CONTROL_RETRY_MARKER}*/"
    "this.__neurodesktopControlRetryCount=neurodeskRetries+1;"
    "this.__neurodesktopControlRetry=!0;"
    "try{let neurodeskRetryKernel=this.kernel;"
    "if(neurodeskRetryKernel&&neurodeskRetryKernel.reconnect)try{"
    "await Promise.race([neurodeskRetryKernel.reconnect(),"
    "new Promise(e=>setTimeout(e,1e4))])}catch(e){}"
    "return await this._loadFromKernel()}finally{"
    "this.__neurodesktopControlRetryCount=neurodeskRetries;"
    "this.__neurodesktopControlRetry=neurodeskRetries>0}}"
    "return this._loadFromKernelModels()}let o=e.states"
)

CONNECTION_WAIT_BEFORE = "async _loadFromKernel(){let e,t;try{"

CONNECTION_WAIT_V1_AFTER = (
    "async _loadFromKernel(){if(!this.__neurodesktopControlRetry){"
    "let neurodeskKernel=this.kernel;"
    "if(neurodeskKernel&&neurodeskKernel.connectionStatusChanged&&"
    '"connected"!==neurodeskKernel.connectionStatus)'
    "await new Promise(e=>{"
    "let neurodeskOnStatus=(i,r)=>{if(\"connected\"===r){"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e()}},"
    "neurodeskTimer=setTimeout(()=>{"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "e()},3e4);"
    "neurodeskKernel.connectionStatusChanged.connect(neurodeskOnStatus),"
    '"connected"===neurodeskKernel.connectionStatus&&('
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e())});"
    "if(neurodeskKernel&&neurodeskKernel.requestKernelInfo)try{"
    "await new Promise((e,t)=>{"
    "let neurodeskProbeTimer=setTimeout(()=>t(Error("
    '"Kernel connection did not answer its readiness probe")),3e3);'
    "neurodeskKernel.requestKernelInfo().then(i=>{"
    "clearTimeout(neurodeskProbeTimer),e(i)},i=>{"
    "clearTimeout(neurodeskProbeTimer),t(i)})})}catch(e){}}"
    f"/*{CONNECTION_WAIT_V1_MARKER}*/"
    "let e,t;try{"
)

CONNECTION_WAIT_AFTER = (
    "async _loadFromKernel(){if(!this.__neurodesktopControlRetry){"
    "let neurodeskKernel=this.kernel;"
    "if(neurodeskKernel&&neurodeskKernel.connectionStatusChanged&&"
    '"connected"!==neurodeskKernel.connectionStatus)'
    "await new Promise(e=>{"
    "let neurodeskOnStatus=(i,r)=>{if(\"connected\"===r){"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e()}},"
    "neurodeskTimer=setTimeout(()=>{"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "e()},3e4);"
    "neurodeskKernel.connectionStatusChanged.connect(neurodeskOnStatus),"
    '"connected"===neurodeskKernel.connectionStatus&&('
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e())});"
    "if(neurodeskKernel&&neurodeskKernel.requestKernelInfo){"
    'this.__neurodesktopKernelProbeStatus="pending";'
    "let neurodeskProbeReady=!1;try{await new Promise((e,t)=>{"
    "let neurodeskProbeTimer=setTimeout(()=>t(Error("
    '"Kernel connection did not answer its readiness probe")),3e3);'
    "neurodeskKernel.requestKernelInfo().then(i=>{"
    "clearTimeout(neurodeskProbeTimer),e(i)},i=>{"
    "clearTimeout(neurodeskProbeTimer),t(i)})}),"
    'neurodeskProbeReady=!0,this.__neurodesktopKernelProbeStatus="ready"}'
    'catch(e){this.__neurodesktopKernelProbeStatus="failed"}'
    "if(!neurodeskProbeReady&&neurodeskKernel.reconnect)try{"
    "let neurodeskReconnected=await Promise.race(["
    "neurodeskKernel.reconnect().then(()=>!0),"
    "new Promise(e=>setTimeout(()=>e(!1),1e4))]);"
    "this.__neurodesktopKernelProbeStatus="
    'neurodeskReconnected?"reconnected":"reconnect-timeout"}'
    'catch(e){this.__neurodesktopKernelProbeStatus="reconnect-failed"}}}'
    f"/*{CONNECTION_WAIT_MARKER}*/"
    "let e,t;try{"
)

RENDERER_SETUP_BEFORE = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);for(let i of(s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}})),o))i.manager=s;return "
    "i.removeMimeType(D),i.addFactory({safe:!1,mimeTypes:[D],"
    "createRenderer:e=>new h(e,s)},-10),new c.DisposableDelegate(()=>{"
    "i&&i.removeMimeType(D),s.dispose()})}"
)

LEGACY_RENDERER_MANAGER_ORDER_AFTER = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}}));i.removeMimeType(D),"
    "i.addFactory({safe:!1,mimeTypes:[D],createRenderer:e=>new h(e,s)},-10);"
    f"/*{LEGACY_RENDERER_MANAGER_ORDER_MARKER}*/"
    "for(let i of o)i.manager=s;return new c.DisposableDelegate(()=>{"
    "i&&i.removeMimeType(D),s.dispose()})}"
)

RENDERER_OUTPUT_WATCH_AFTER = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}}));"
    "let neurodeskAreas=[],neurodeskSeen=new WeakSet,"
    "neurodeskAttach=e=>{for(let t of e.widgets)"
    "for(let e of Array.from(t.children()))"
    "e instanceof h&&!neurodeskSeen.has(e)&&"
    "(neurodeskSeen.add(e),e.manager=s)};"
    "for(let i of o)i.manager=s,neurodeskSeen.add(i);"
    "i.removeMimeType(D),i.addFactory({safe:!1,mimeTypes:[D],"
    "createRenderer:e=>{let t=new h(e,s);return neurodeskSeen.add(t),t}},-10);"
    f"/*{RENDERER_OUTPUT_WATCH_MARKER}*/"
    'for(let t of("cells"in e?Array.from(e.cells):e.widgets))'
    '"code"===t.model.type&&(neurodeskAreas.push(t.outputArea),'
    "t.outputArea.outputLengthChanged.connect(neurodeskAttach),"
    "neurodeskAttach(t.outputArea));"
    "return new c.DisposableDelegate(()=>{"
    "for(let e of neurodeskAreas)"
    "e.outputLengthChanged.disconnect(neurodeskAttach);"
    "i&&i.removeMimeType(D),s.dispose()})}"
)

RENDERER_RECOVERY_RERENDER_BEFORE = (
    "try{t=await o.get_model(r.model_id)}catch(t){"
    "if(o.restoredStatus){this.node.textContent="
    '"Error displaying widget: model not found",'
    'this.addClass("jupyter-widgets"),console.error(t);return}'
    "this._rerenderMimeModel=e;return}this._rerenderMimeModel=null;"
)

RENDERER_RECOVERY_RERENDER_AFTER = (
    "try{t=await o.get_model(r.model_id)}catch(t){"
    "this._rerenderMimeModel=e;"
    f"/*{RENDERER_RECOVERY_RERENDER_MARKER}*/"
    "if(o.restoredStatus){this.node.textContent="
    '"Error displaying widget: model not found",'
    'this.addClass("jupyter-widgets"),console.error(t);return}'
    "return}this._rerenderMimeModel=null;"
)

RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE = (
    '_rerender(){this._rerenderMimeModel&&(this.node.textContent="",'
    'this.removeClass("jupyter-widgets"),'
    "this.renderModel(this._rerenderMimeModel))}"
)

RENDERER_RERENDER_SINGLE_FLIGHT_AFTER = (
    "_rerender(){if(this._rerenderMimeModel){"
    "let neurodeskMimeModel=this._rerenderMimeModel;"
    "this._rerenderMimeModel=null;"
    f"/*{RENDERER_RERENDER_SINGLE_FLIGHT_MARKER}*/"
    'this.node.textContent="",this.removeClass("jupyter-widgets"),'
    "this.renderModel(neurodeskMimeModel)}}"
)

MODEL_REGISTRATION_RERENDER_BEFORE = (
    "register_model(e,t){super.register_model(e,t),this.setDirty()}"
)

MODEL_REGISTRATION_RERENDER_AFTER = (
    "register_model(e,t){super.register_model(e,t),this.setDirty(),"
    f"/*{MODEL_REGISTRATION_RERENDER_MARKER}*/"
    "this._restoredStatus&&this._restored.emit()}"
)

HASHED_BUNDLE_NAME = re.compile(
    r"^(?P<prefix>.+)\.(?P<hash>[0-9a-f]{16,20})\.js$"
)


def installed_labextension_dir() -> Path:
    """Return the installed JupyterLab widget manager directory."""
    return (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-widgets/jupyterlab-manager"
    )


def patch_labextension(labextension_dir: Path) -> bool:
    """Patch *labextension_dir* and return whether files changed."""
    labextension_dir = Path(labextension_dir)
    static_dir = labextension_dir / "static"
    bundles = sorted(static_dir.glob("*.js"))
    if not bundles:
        raise ValueError("JupyterLab widget manager bundles were not found")

    package_path = labextension_dir / "package.json"
    package_text = package_path.read_text(encoding="utf-8")
    try:
        package_data = json.loads(package_text)
        load_path = package_data["jupyterlab"]["_build"]["load"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            "widget manager package.json has no valid JupyterLab build entry"
        ) from exc
    if not isinstance(load_path, str) or not load_path.startswith("static/"):
        raise ValueError("widget manager remote entry path is invalid")

    remote_entry = labextension_dir / load_path
    remote_text = remote_entry.read_text(encoding="utf-8")
    texts = {path: path.read_text(encoding="utf-8") for path in bundles}

    def is_active_asset(path: Path) -> bool:
        name_match = HASHED_BUNDLE_NAME.match(path.name)
        return bool(name_match and name_match.group("hash") in remote_text)

    def active_paths(marker: str) -> list[Path]:
        return [
            path
            for path, text in texts.items()
            if marker in text and is_active_asset(path)
        ]

    model_before_paths = [
        path
        for path, text in texts.items()
        if MODEL_RETRY_BEFORE in text and is_active_asset(path)
    ]
    active_recovery_paths = active_paths(MODEL_RECOVERY_MARKER)
    active_retry_paths = [
        path
        for path in active_paths(MODEL_RETRY_MARKER)
        if MODEL_RECOVERY_MARKER not in texts[path]
    ]
    renderer_before_paths = [
        path
        for path, text in texts.items()
        if RENDERER_SETUP_BEFORE in text and is_active_asset(path)
    ]
    renderer_order_paths = [
        path
        for path, text in texts.items()
        if LEGACY_RENDERER_MANAGER_ORDER_AFTER in text
        and is_active_asset(path)
    ]
    active_renderer_paths = active_paths(RENDERER_OUTPUT_WATCH_MARKER)
    active_rerender_paths = active_paths(RENDERER_RECOVERY_RERENDER_MARKER)
    active_single_flight_paths = active_paths(
        RENDERER_RERENDER_SINGLE_FLIGHT_MARKER
    )
    active_registration_paths = active_paths(
        MODEL_REGISTRATION_RERENDER_MARKER
    )

    required_markers = (
        MODEL_RETRY_MARKER,
        MODEL_RECOVERY_MARKER,
        MODEL_RECOVERY_COOLDOWN_MARKER,
        MODEL_RECOVERY_LIFECYCLE_MARKER,
        CONTROL_TIMEOUT_MARKER,
        CONTROL_RETRY_MARKER,
        CONNECTION_WAIT_MARKER,
    )
    if (
        len(active_recovery_paths) == 1
        and all(
            marker in texts[active_recovery_paths[0]]
            for marker in required_markers
        )
        and len(active_renderer_paths) == 1
        and len(active_rerender_paths) == 1
        and len(active_single_flight_paths) == 1
        and len(active_registration_paths) == 1
        and active_renderer_paths[0] == active_rerender_paths[0]
        and active_renderer_paths[0] == active_single_flight_paths[0]
        and active_renderer_paths[0] == active_registration_paths[0]
        and CACHE_SAFE_MARKER in remote_text
    ):
        return False

    if len(active_recovery_paths) + len(active_retry_paths) > 1:
        raise ValueError(
            "more than one active widget model workaround bundle was found"
        )
    if len(active_renderer_paths) > 1:
        raise ValueError(
            "more than one active widget renderer workaround bundle was found"
        )
    if len(active_rerender_paths) > 1:
        raise ValueError(
            "more than one active widget rerender workaround bundle was found"
        )
    if len(active_single_flight_paths) > 1:
        raise ValueError(
            "more than one active widget single-flight rerender bundle was found"
        )
    if len(active_registration_paths) > 1:
        raise ValueError(
            "more than one active widget registration rerender bundle was found"
        )

    replacements_by_path: dict[Path, list[tuple[str, str]]] = {}

    def add_replacement(path: Path, before: str, after: str) -> None:
        replacements_by_path.setdefault(path, []).append((before, after))

    if active_recovery_paths:
        model_bundle = active_recovery_paths[0]
    elif active_retry_paths:
        model_bundle = active_retry_paths[0]
        add_replacement(model_bundle, MODEL_RETRY_AFTER, MODEL_RECOVERY_AFTER)
    else:
        if len(model_before_paths) != 1:
            raise ValueError(
                "widget model retry anchor did not match exactly once; "
                "reassess the widget restore workaround"
            )
        model_bundle = model_before_paths[0]
        add_replacement(model_bundle, MODEL_RETRY_BEFORE, MODEL_RECOVERY_AFTER)

    model_text = texts[model_bundle]
    if (
        MODEL_RECOVERY_MARKER in model_text
        and MODEL_RECOVERY_COOLDOWN_MARKER not in model_text
    ):
        add_replacement(
            model_bundle,
            MODEL_RECOVERY_V1_AFTER,
            MODEL_RECOVERY_AFTER,
        )
    elif (
        MODEL_RECOVERY_COOLDOWN_MARKER in model_text
        and MODEL_RECOVERY_LIFECYCLE_MARKER not in model_text
    ):
        add_replacement(
            model_bundle,
            MODEL_RECOVERY_V2_AFTER,
            MODEL_RECOVERY_AFTER,
        )
    if CONTROL_TIMEOUT_MARKER not in model_text:
        add_replacement(
            model_bundle,
            (
                CONTROL_TIMEOUT_V1_AFTER
                if CONTROL_TIMEOUT_V1_MARKER in model_text
                else CONTROL_TIMEOUT_BEFORE
            ),
            CONTROL_TIMEOUT_AFTER,
        )
    if CONTROL_RETRY_MARKER not in model_text:
        add_replacement(
            model_bundle,
            (
                CONTROL_RETRY_V2_AFTER
                if CONTROL_RETRY_V2_MARKER in model_text
                else (
                    CONTROL_RETRY_V1_AFTER
                    if CONTROL_RETRY_V1_MARKER in model_text
                    else CONTROL_RETRY_BEFORE
                )
            ),
            CONTROL_RETRY_AFTER,
        )
    if CONNECTION_WAIT_MARKER not in model_text:
        add_replacement(
            model_bundle,
            (
                CONNECTION_WAIT_V1_AFTER
                if CONNECTION_WAIT_V1_MARKER in model_text
                else CONNECTION_WAIT_BEFORE
            ),
            CONNECTION_WAIT_AFTER,
        )

    if active_renderer_paths:
        renderer_path = active_renderer_paths[0]
    else:
        renderer_candidates = [
            (path, RENDERER_SETUP_BEFORE)
            for path in renderer_before_paths
        ] + [
            (path, LEGACY_RENDERER_MANAGER_ORDER_AFTER)
            for path in renderer_order_paths
        ]
        if len(renderer_candidates) != 1:
            raise ValueError(
                "widget renderer setup anchor did not match exactly once; "
                "reassess the widget restore workaround"
            )
        renderer_path, renderer_before = renderer_candidates[0]
        add_replacement(
            renderer_path,
            renderer_before,
            RENDERER_OUTPUT_WATCH_AFTER,
        )
    if active_rerender_paths:
        if active_rerender_paths[0] != renderer_path:
            raise ValueError(
                "widget renderer workarounds are split across active bundles"
            )
    else:
        add_replacement(
            renderer_path,
            RENDERER_RECOVERY_RERENDER_BEFORE,
            RENDERER_RECOVERY_RERENDER_AFTER,
        )
    if active_single_flight_paths:
        if active_single_flight_paths[0] != renderer_path:
            raise ValueError(
                "widget renderer workarounds are split across active bundles"
            )
    else:
        add_replacement(
            renderer_path,
            RENDERER_RERENDER_SINGLE_FLIGHT_BEFORE,
            RENDERER_RERENDER_SINGLE_FLIGHT_AFTER,
        )
    if active_registration_paths:
        if active_registration_paths[0] != renderer_path:
            raise ValueError(
                "widget renderer workarounds are split across active bundles"
            )
    else:
        add_replacement(
            renderer_path,
            MODEL_REGISTRATION_RERENDER_BEFORE,
            MODEL_REGISTRATION_RERENDER_AFTER,
        )

    remote_match = HASHED_BUNDLE_NAME.match(remote_entry.name)
    if remote_match is None:
        raise ValueError("widget manager frontend assets are not content hashed")

    patched_remote_text = remote_text
    patched_bundles: list[tuple[Path, str]] = []
    for source_bundle, replacements in sorted(replacements_by_path.items()):
        source_text = texts[source_bundle]
        for before, _ in replacements:
            if source_text.count(before) != 1:
                raise ValueError(
                    "widget restore anchor did not match exactly once; "
                    "reassess the widget restore workaround"
                )

        source_match = HASHED_BUNDLE_NAME.match(source_bundle.name)
        if source_match is None:
            raise ValueError(
                "widget manager frontend assets are not content hashed"
            )
        source_hash = source_match.group("hash")
        if source_hash not in patched_remote_text:
            raise ValueError(
                "widget manager remote entry does not reference a patched bundle"
            )

        patched_text = source_text
        for before, after in replacements:
            patched_text = patched_text.replace(before, after)
        patched_hash = hashlib.sha256(
            patched_text.encode("utf-8")
        ).hexdigest()[:20]
        patched_bundle = static_dir / (
            f"{source_match.group('prefix')}.{patched_hash}.js"
        )
        patched_bundles.append((patched_bundle, patched_text))
        patched_remote_text = patched_remote_text.replace(
            source_hash, patched_hash
        )

    if CACHE_SAFE_MARKER not in patched_remote_text:
        patched_remote_text += f"\n/*{CACHE_SAFE_MARKER}*/\n"
    patched_remote_hash = hashlib.sha256(
        patched_remote_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_remote_entry = static_dir / (
        f"{remote_match.group('prefix')}.{patched_remote_hash}.js"
    )
    patched_load_path = f"static/{patched_remote_entry.name}"
    if package_text.count(load_path) != 1:
        raise ValueError(
            "widget manager package.json remote entry did not match exactly once"
        )

    for patched_bundle, patched_text in patched_bundles:
        patched_bundle.write_text(patched_text, encoding="utf-8")
    patched_remote_entry.write_text(patched_remote_text, encoding="utf-8")
    package_path.write_text(
        package_text.replace(load_path, patched_load_path), encoding="utf-8"
    )
    return True


def main() -> int:
    labextension_dir = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else installed_labextension_dir()
    )
    try:
        changed = patch_labextension(labextension_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch JupyterLab widgets: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"JupyterLab widget restore workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
