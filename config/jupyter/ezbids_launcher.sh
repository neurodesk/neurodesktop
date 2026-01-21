#!/bin/bash
# ezBIDS launcher for JupyterLab
# Uses wrapper server to provide instant splash page while container loads

exec python3 /opt/neurodesktop/ezbids_wrapper/ezbids_wrapper.py
