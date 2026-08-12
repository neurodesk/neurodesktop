"""Tests for patch_nbi.py.

Settings panel: the Notebook Intelligence settings panel auto-saves its
client-side cache on open, which reverts the OpenCode -> NBI model sync (see
nbi_setup.sh). The patcher rewrites the "open settings" command in the bundled
labextension so the panel is rebuilt from freshly fetched capabilities
instead.

Ollama provider: `ollama list` reports an empty details.family for
safetensors/mlx imports, so upstream's f"{family}.context_length" lookup
raises and the model is dropped from the chat model list. The patcher adds a
fallback to the modelinfo's *.context_length key.
"""

import textwrap

import pytest

from testlib import load_source_module, repo_path


def load_patcher_module():
    return load_source_module(
        "nbi_patch",
        "/opt/neurodesktop/patch_nbi.py",
        "config/agents/patch_nbi.py",
    )


# Verbatim excerpts matching the notebook_intelligence 5.3.1 source-built
# labextension bundle:
# the NBI API class holding fetchCapabilities, and the settings command whose
# execute callback shows a panel built from the stale client-side cache.
BUNDLE_FIXTURE = (
    'class T{static async initialize(){await this.fetchCapabilities(),'
    "this.updateGitHubLoginStatus()}}"
    "const O=()=>{const t=new Qo({onSave:()=>{T.fetchCapabilities()}}),"
    'n=new a.MainAreaWidget({content:t});return n.id="nbi-settings",n};'
    "let U=O();"
    "e.commands.addCommand(pe.openConfigurationDialog,"
    '{label:"Notebook Intelligence Settings",'
    "execute:t=>{U.isDisposed&&(U=O()),"
    'U.isAttached||e.shell.add(U,"main"),'
    "e.shell.activateById(U.id)}})"
)

# Verbatim excerpt matching notebook_intelligence 5.3.1's
# ollama_llm_provider.py update_chat_model_list().
OLLAMA_FIXTURE = (
    "class OllamaLLMProvider:\n"
    "    def update_chat_model_list(self):\n"
    "        try:\n"
    "            import ollama\n"
    "            response = ollama.list()\n"
    "            models = response.models\n"
    "            self._chat_models = []\n"
    "            for model in models:\n"
    "                try:\n"
    "                    model_family = model.details.family\n"
    "                    if model_family in OLLAMA_EMBEDDING_FAMILIES:\n"
    "                        continue\n"
    "                    model_show = ollama.show(model.model)\n"
    "                    model_info = model_show.modelinfo\n"
    '                    context_window = model_info[f"{model_family}.context_length"]\n'
    "                    self._chat_models.append(\n"
    "                        OllamaChatModel(self, model.model, model.model, context_window)\n"
    "                    )\n"
    "                except Exception as e:\n"
    '                    log.error(f"Error getting Ollama model info {model}: {e}")\n'
    "        except Exception as e:\n"
    '            log.warning(f"Failed to update supported Ollama models: {e}")\n'
)


def test_settings_patch_rewrites_settings_command():
    patcher = load_patcher_module()

    patched, changed = patcher.patch_settings_bundle_text(BUNDLE_FIXTURE)

    assert changed
    assert patcher.SETTINGS_MARKER in patched
    # The panel is rebuilt after awaiting fresh capabilities.
    assert "execute:async t=>{" in patched
    assert "await T.fetchCapabilities()" in patched
    assert "U.isDisposed||U.dispose();" in patched
    # The stale-cache fast path is gone.
    assert "U.isDisposed&&(U=O())" not in patched
    # Everything around the command registration is untouched.
    assert patched.startswith("class T{static async initialize()")
    assert patched.endswith("e.shell.activateById(U.id)}})")


def test_settings_patch_is_idempotent():
    patcher = load_patcher_module()

    patched, _ = patcher.patch_settings_bundle_text(BUNDLE_FIXTURE)
    repatched, changed = patcher.patch_settings_bundle_text(patched)

    assert not changed
    assert repatched == patched


def test_settings_patch_leaves_unrelated_text_alone():
    patcher = load_patcher_module()

    text = "console.log('no settings command here')"
    patched, changed = patcher.patch_settings_bundle_text(text)

    assert not changed
    assert patched == text


def test_settings_patch_refuses_partial_match():
    """A settings command without a locatable API class must not be patched."""
    patcher = load_patcher_module()

    command_only = BUNDLE_FIXTURE.replace(
        "class T{static async initialize(){await this.fetchCapabilities(),"
        "this.updateGitHubLoginStatus()}}",
        "",
    )
    with pytest.raises(ValueError):
        patcher.patch_settings_bundle_text(command_only)


def test_ollama_patch_replaces_context_lookup():
    patcher = load_patcher_module()

    patched, changed = patcher.patch_ollama_provider_text(OLLAMA_FIXTURE)

    assert changed
    assert patcher.OLLAMA_MARKER in patched
    assert patcher.OLLAMA_ANCHOR not in patched
    # The patched module must still be valid Python.
    compile(patched, "ollama_llm_provider.py", "exec")
    # Everything around the lookup is untouched.
    assert patched.startswith("class OllamaLLMProvider:")
    assert patched.endswith('log.warning(f"Failed to update supported Ollama models: {e}")\n')


def test_ollama_patch_is_idempotent():
    patcher = load_patcher_module()

    patched, _ = patcher.patch_ollama_provider_text(OLLAMA_FIXTURE)
    repatched, changed = patcher.patch_ollama_provider_text(patched)

    assert not changed
    assert repatched == patched


def test_ollama_patch_refuses_when_anchor_missing():
    """A notebook_intelligence upgrade that rewrites the provider must fail
    the image build instead of silently reintroducing the dropped models."""
    patcher = load_patcher_module()

    with pytest.raises(ValueError):
        patcher.patch_ollama_provider_text("def update_chat_model_list(): pass\n")


def run_replacement(patcher, model_family, model_info):
    namespace = {"model_family": model_family, "model_info": model_info}
    exec(textwrap.dedent(patcher.OLLAMA_REPLACEMENT), namespace)
    return namespace["context_window"]


def test_ollama_fallback_registers_empty_family_models():
    """The mlx/safetensors case: empty family from `ollama list`, real
    context length only under the architecture-prefixed modelinfo key."""
    patcher = load_patcher_module()

    context_window = run_replacement(
        patcher,
        "",
        {"general.architecture": "gemma4", "gemma4.context_length": 262144},
    )

    assert context_window == 262144


def test_ollama_fallback_keeps_exact_family_lookup():
    patcher = load_patcher_module()

    context_window = run_replacement(
        patcher,
        "llama",
        {"llama.context_length": 4096, "other.context_length": 1},
    )

    assert context_window == 4096


def test_ollama_fallback_still_skips_models_without_context_length():
    """No *.context_length key must keep upstream's behavior: raise, log,
    and skip the model."""
    patcher = load_patcher_module()

    with pytest.raises(KeyError):
        run_replacement(patcher, "", {"general.architecture": "mystery"})


def test_main_fails_when_bundle_anchor_missing(tmp_path):
    """A notebook_intelligence upgrade that changes the bundle must fail the
    image build instead of silently reintroducing the stale-save bug."""
    patcher = load_patcher_module()

    bundle = tmp_path / "chunk.js"
    bundle.write_text("var unrelated=1;", encoding="utf-8")

    assert patcher.main(["--bundles", str(tmp_path / "*.js")]) == 1


def test_main_patches_and_reruns_cleanly(tmp_path):
    patcher = load_patcher_module()

    bundle = tmp_path / "chunk.js"
    bundle.write_text(BUNDLE_FIXTURE, encoding="utf-8")

    assert patcher.main(["--bundles", str(tmp_path / "*.js")]) == 0
    assert patcher.SETTINGS_MARKER in bundle.read_text(encoding="utf-8")
    # Second run: already patched, still success.
    assert patcher.main(["--bundles", str(tmp_path / "*.js")]) == 0


def test_main_patches_ollama_provider(tmp_path):
    patcher = load_patcher_module()

    provider = tmp_path / "ollama_llm_provider.py"
    provider.write_text(OLLAMA_FIXTURE, encoding="utf-8")

    assert patcher.main(["--ollama", str(provider)]) == 0
    assert patcher.OLLAMA_MARKER in provider.read_text(encoding="utf-8")
    assert patcher.main(["--ollama", str(provider)]) == 0


def test_main_fails_when_ollama_provider_missing(tmp_path):
    patcher = load_patcher_module()

    assert patcher.main(["--ollama", str(tmp_path / "missing.py")]) == 1


def test_main_without_args_patches_both_targets(tmp_path, monkeypatch):
    """The Dockerfile invokes patch_nbi.py with no arguments; that run must
    cover the labextension bundle and the Ollama provider."""
    patcher = load_patcher_module()

    bundle = tmp_path / "chunk.js"
    bundle.write_text(BUNDLE_FIXTURE, encoding="utf-8")
    provider = tmp_path / "ollama_llm_provider.py"
    provider.write_text(OLLAMA_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(patcher, "DEFAULT_BUNDLE_GLOB", str(tmp_path / "*.js"))
    monkeypatch.setattr(patcher, "DEFAULT_OLLAMA_PROVIDER_GLOB", str(provider))

    assert patcher.main([]) == 0
    assert patcher.SETTINGS_MARKER in bundle.read_text(encoding="utf-8")
    assert patcher.OLLAMA_MARKER in provider.read_text(encoding="utf-8")


def test_dockerfile_rebuilds_the_nbi_531_frontend():
    """The 5.3.1 wheel targets JupyterLab 4.2, so the image must rebuild the
    tagged source for JupyterLab 4.6 before applying the settings patch.
    """
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert "notebook_intelligence==5.3.1" in dockerfile
    assert 'ARG NBI_JUPYTERLAB_BUILDER_VERSION="4.5.10"' in dockerfile
    assert 'branch "v${NBI_VERSION}"' in dockerfile
    assert "source=config/jupyter/notebook-intelligence-5.3.1.yarn.lock" in dockerfile
    assert "install -m 0644 /tmp/nbi-yarn.lock yarn.lock" in dockerfile
    assert "jlpm install --immutable" in dockerfile
    assert 'npm pkg set "dependencies.@jupyterlab/launcher=^4.0.0"' in dockerfile
    assert 'npm pkg set "devDependencies.@jupyterlab/builder=${NBI_JUPYTERLAB_BUILDER_VERSION}"' in dockerfile
    assert "jlpm up" not in dockerfile
    assert "cp -a /tmp/notebook-intelligence/notebook_intelligence/labextension" in dockerfile
    assert dockerfile.index("NBI_PACKAGE_DIR=") < dockerfile.index(
        "&& cd /tmp/notebook-intelligence"
    )

    rebuild = dockerfile.index("# Rebuild Notebook Intelligence's frontend")
    patch = dockerfile.index("/opt/neurodesktop/patch_nbi.py", rebuild)
    assert rebuild < patch
