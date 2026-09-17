/** Open the Jupyter-owned T3 web application without exposing another port. */
import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { MainAreaWidget, showDialog, Dialog } from '@jupyterlab/apputils';
import { URLExt } from '@jupyterlab/coreutils';
import { ILauncher } from '@jupyterlab/launcher';
import { ServerConnection } from '@jupyterlab/services';
import { codeIcon } from '@jupyterlab/ui-components';
import { Widget } from '@lumino/widgets';

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'neurodesk-launcher:t3-code',
  autoStart: true,
  requires: [ILauncher],
  activate: (app: JupyterFrontEnd, launcher: ILauncher) => {
    let panel: MainAreaWidget<Widget> | null = null;
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
          const content = new Widget({ node: frame });
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
