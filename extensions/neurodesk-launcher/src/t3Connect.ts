/** Device authorization stays on T3's hosted page; credentials stay on the server. */
import { MainAreaWidget } from '@jupyterlab/apputils';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { Widget } from '@lumino/widgets';

interface Status {
  state: string;
  message: string;
  code: string | null;
  verification_url: string | null;
  expires_at: number | null;
  label: string | null;
  linked: boolean;
}

export function createConnectPanel(app: JupyterFrontEnd): MainAreaWidget<Widget> {
  const node = document.createElement('div');
  node.style.cssText = 'padding:28px;max-width:640px;overflow:auto;color:var(--jp-ui-font-color1)';
  const heading = document.createElement('h1');
  heading.textContent = 'Connect to my desktop';
  const intro = document.createElement('p');
  intro.textContent = 'Optional: use this Neurodesktop environment from your T3 desktop app. You can use agents in Jupyter without linking. Approving the link gives your T3 account remote access to this environment.';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const label = document.createElement('p');
  const code = document.createElement('strong');
  code.style.cssText = 'display:block;font-size:28px;letter-spacing:3px;margin:16px 0';
  const expiry = document.createElement('p');
  const authorize = document.createElement('a');
  authorize.textContent = 'Authorize T3 Connect';
  authorize.target = '_blank';
  authorize.rel = 'noopener noreferrer';
  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:12px;margin-top:20px';
  node.append(heading, intro, status, label, code, expiry, authorize, actions);
  const content = new Widget({ node });
  const panel = new MainAreaWidget({ content });
  panel.id = 'neurodesk-t3-connect';
  panel.title.label = 'Connect to my desktop';
  panel.title.closable = true;
  const settings = ServerConnection.makeSettings();
  const url = URLExt.join(settings.baseUrl, 'neurodesk-t3', '_connect');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let requestPending = false;
  let current: Status | undefined;
  const buttons = new Map<string, HTMLButtonElement>();
  for (const [action, title] of [['link', 'Connect to my desktop'], ['retry', 'Retry'], ['cancel', 'Cancel setup'], ['disconnect', 'Disconnect']]) {
    const button = document.createElement('button');
    button.className = 'jp-mod-styled';
    button.textContent = title;
    button.onclick = () => { void request(action); };
    buttons.set(action, button);
    actions.append(button);
  }
  function render(value: Status): void {
    current = value;
    status.textContent = value.message;
    label.textContent = value.label ? `Environment: ${value.label}` : '';
    code.textContent = value.code || '';
    expiry.textContent = value.expires_at ? `Code expires at ${new Date(value.expires_at * 1000).toLocaleTimeString()}.` : '';
    // Never turn arbitrary server text into markup or a navigation destination.
    authorize.hidden = !value.code || value.verification_url !== 'https://accounts.t3.codes/device';
    authorize.href = 'https://accounts.t3.codes/device';
    const busy = ['starting', 'authorizing', 'waiting_idle', 'restarting', 'connecting', 'disconnecting'].includes(value.state);
    buttons.get('link')!.hidden = value.state !== 'idle';
    buttons.get('retry')!.hidden = !['error', 'expired'].includes(value.state);
    buttons.get('cancel')!.hidden = !busy;
    buttons.get('disconnect')!.hidden = busy || (!value.linked && value.state === 'idle');
    for (const button of buttons.values()) button.disabled = requestPending;
  }
  async function request(action?: string): Promise<void> {
    if (requestPending || panel.isDisposed) return;
    requestPending = true;
    if (timer) clearTimeout(timer);
    if (current) render(current);
    try {
      const response = await ServerConnection.makeRequest(url, action ? {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      } : {}, settings);
      if (!response.ok) throw new Error(response.status === 409 ? 'Another setup request is still running.' : 'Could not reach Neurodesktop. Check your connection and retry.');
      const value: Status = await response.json();
      if (!panel.isDisposed) render(value);
    } catch (error) {
      if (!panel.isDisposed) {
        status.textContent = error instanceof Error ? error.message : 'Connection check failed.';
        buttons.get('retry')!.hidden = false;
      }
    } finally {
      requestPending = false;
      for (const button of buttons.values()) button.disabled = false;
      if (!panel.isDisposed) timer = setTimeout(() => { void request(); }, 2000);
    }
  }
  panel.disposed.connect(() => { if (timer) clearTimeout(timer); });
  app.shell.add(panel, 'main');
  app.shell.activateById(panel.id);
  void request();
  return panel;
}
