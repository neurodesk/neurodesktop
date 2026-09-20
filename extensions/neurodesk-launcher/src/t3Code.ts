/** Open the Jupyter-owned T3 web application without exposing another port. */
import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { MainAreaWidget, showDialog, Dialog } from '@jupyterlab/apputils';
import { URLExt } from '@jupyterlab/coreutils';
import { ILauncher } from '@jupyterlab/launcher';
import { ServerConnection } from '@jupyterlab/services';
import { codeIcon } from '@jupyterlab/ui-components';
import { Widget } from '@lumino/widgets';
import { createConnectPanel } from './t3Connect';

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'neurodesk-launcher:t3-code',
  autoStart: true,
  requires: [ILauncher],
  activate: (app: JupyterFrontEnd, launcher: ILauncher) => {
    let panel: MainAreaWidget<Widget> | null = null;
    let connectPanel: MainAreaWidget<Widget> | null = null;
    const openConnect = () => {
      if (connectPanel && !connectPanel.isDisposed) app.shell.activateById(connectPanel.id);
      else connectPanel = createConnectPanel(app);
    };
    app.commands.addCommand('neurodesk-launcher:t3-connect', {
      label: 'Setup T3 connect', icon: codeIcon, execute: openConnect
    });
    const command = 'neurodesk-launcher:open-t3-code';
    app.commands.addCommand(command, {
      label: 'scigent.ai',
      caption: 'Open scigent.ai in JupyterLab',
      icon: codeIcon,
      execute: async () => {
        if (panel && !panel.isDisposed) {
          app.shell.activateById(panel.id);
          return;
        }
        const settings = ServerConnection.makeSettings();
        try {
          const response = await ServerConnection.makeRequest(
            URLExt.join(settings.baseUrl, 'neurodesk-t3-status'), {}, settings
          );
          if (!response.ok) throw new Error('The scigent.ai server extension is unavailable.');
          const status = await response.json();
          if (status.state !== 'ready') {
            throw new Error('scigent.ai is not ready. Wait a moment and reopen it. If this persists, check the Jupyter server log.');
          }
          const session = await ServerConnection.makeRequest(
            URLExt.join(settings.baseUrl, 'neurodesk-t3', '_session'),
            { method: 'POST' }, settings
          );
          if (!session.ok) {
            throw new Error('Could not connect to scigent.ai. Wait a moment and reopen it.');
          }
          const frame = document.createElement('iframe');
          frame.title = 'scigent.ai';
          frame.src = URLExt.join(settings.baseUrl, 'neurodesk-t3') + '/';
          frame.style.cssText = 'width:100%;height:100%;border:0;display:block';
          frame.allow = 'clipboard-read; clipboard-write';
          const container = document.createElement('div');
          container.style.cssText = 'display:flex;flex-direction:column;height:100%';
          const connect = document.createElement('button');
          connect.className = 'jp-mod-styled';
          connect.textContent = 'Setup T3 connect';
          connect.style.cssText = 'align-self:flex-end;flex-shrink:0;height:22px;min-height:0;margin:2px 6px;padding:0 8px;font-size:11px;line-height:20px';
          connect.onclick = openConnect;
          frame.style.flex = '1';
          frame.style.minHeight = '0';
          container.append(connect, frame);
          const content = new Widget({ node: container });
          const onMessage = (event: MessageEvent) => {
            if (event.origin === window.location.origin && event.source === frame.contentWindow &&
                event.data?.type === 'neurodesk-t3-connect') openConnect();
          };
          window.addEventListener('message', onMessage);
          content.disposed.connect(() => window.removeEventListener('message', onMessage));
          panel = new MainAreaWidget({ content });
          panel.id = 'neurodesk-t3-code';
          panel.title.label = 'scigent.ai';
          panel.title.icon = codeIcon;
          panel.title.closable = true;
          app.shell.add(panel, 'main');
          app.shell.activateById(panel.id);
        } catch (error) {
          await showDialog({
            title: 'scigent.ai',
            body: error instanceof Error ? error.message : 'Could not open scigent.ai.',
            buttons: [Dialog.okButton()]
          });
        }
      }
    });
    launcher.add({ command, category: 'Neurodesk', rank: 5 });
  }
};

export default plugin;
