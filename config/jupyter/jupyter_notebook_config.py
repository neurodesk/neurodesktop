import os
import subprocess

c.ServerProxy.servers = {
  'neurodesktop': {
    'command': ['/opt/neurodesktop/guacamole.sh'],
    'port': 8080,
    'timeout': 60,
      'request_headers_override': {
          'Authorization': 'Basic am92eWFuOnBhc3N3b3Jk',
      },
      'launcher_entry': {
        'path_info' : 'neurodesktop',
        'title': 'Neurodesktop',
        'icon_path': '/opt/neurodesk_brain_logo.svg'
      }
    },
  'ezbids': {
    'command': ['/opt/neurodesktop/ezbids_launcher.sh'],
    'port': 13000,
    'timeout': 300,
    'absolute_url': True,
    'new_browser_tab': True,
    'launcher_entry': {
      'path_info': 'ezbids',
      'title': 'ezBIDS',
      'icon_path': '/opt/neurodesk_brain_icon.svg'
    }
  },
  'api/ezbids': {
    'port': 8082,
    'timeout': 300,
    'absolute_url': False,
    'launcher_entry': {
      'enabled': False
    }
  }
}

# c.ServerApp.root_dir = '/' # this causes an error when clicking on the little house icon when being located in the home directory
c.ServerApp.preferred_dir = os.getcwd()
c.FileContentsManager.allow_hidden = True

before_notebook = subprocess.call("/opt/neurodesktop/jupyterlab_startup.sh")

# Fix for Rise extension: https://github.com/neurodesk/neurodesktop/issues/327
c.NotebookApp.tornado_settings = { "headers": { "Content-Security-Policy": "frame-ancestors 'self'" } }
