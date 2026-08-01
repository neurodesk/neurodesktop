# Appended to /etc/jupyter/jupyter_server_config.py at image build time.
#
# The AGENTS.md seeding hook lives here rather than in
# jupyter_notebook_config.py so it has exactly one registration site next to
# its warning suppression below; the legacy notebook config would add further
# re-applications through notebook_shim's per-extension-app loads.
import sys

if '/opt/neurodesktop' not in sys.path:
    sys.path.insert(0, '/opt/neurodesktop')

try:
    from jupyter_ai_workspace import seed_agents_on_chat_save
except Exception as _jupyter_ai_workspace_error:
    # Seeding is best-effort: a missing or broken module must only disable
    # AGENTS.md seeding, never block server startup or chat saves.
    print(f'[WARN] Jupyter AI chat workspace seeding unavailable: {_jupyter_ai_workspace_error}')
else:
    # Jupyter's config system applies this trait more than once per startup
    # (the shimmed extension apps re-apply server config), and jupyter_server
    # warns on every post_save_hook reassignment even when the hook is
    # unchanged. Suppress exactly that duplicate self-registration message;
    # a genuinely different hook overriding ours still warns.
    import warnings
    warnings.filterwarnings(
        'ignore',
        message=(
            r'Overriding existing post_save_hook \(seed_agents_on_chat_save\) '
            r'with a new one \(seed_agents_on_chat_save\)'
        ),
    )
    c.FileContentsManager.post_save_hook = seed_agents_on_chat_save
