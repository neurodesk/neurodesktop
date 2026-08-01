# Appended to /etc/jupyter/jupyter_server_config.py at image build time.
#
# The AGENTS.md seeding hook lives here rather than in
# jupyter_notebook_config.py because the legacy notebook config is re-applied
# by notebook_shim for every shimmed extension app (nbclassic and notebook),
# which assigns post_save_hook more than once and trips jupyter_server's
# "Overriding existing post_save_hook" warning at every startup. The server
# config is loaded exactly once by ServerApp.
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
    c.FileContentsManager.post_save_hook = seed_agents_on_chat_save
