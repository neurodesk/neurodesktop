import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Notification } from '@jupyterlab/apputils';

import { ILauncher } from '@jupyterlab/launcher';

import { LabIcon } from '@jupyterlab/ui-components';

import { ServerConnection } from '@jupyterlab/services';

import { URLExt } from '@jupyterlab/coreutils';

interface ILauncherEntry {
  enabled: boolean;
  title: string;
  path_info: string;
  category: string;
}

interface IServerProcess {
  name: string;
  launcher_entry: ILauncherEntry;
  new_browser_tab: boolean;
}

interface IServersInfoResponse {
  server_processes: IServerProcess[];
}

const DONATION_URL = 'https://donations.uq.edu.au/EAINNEUR';
const DONATION_MESSAGE =
  'We rely on your financial support to cover our infrastructure costs. Please support us here: https://donations.uq.edu.au/EAINNEUR';
const DONATION_NOTIFICATION_DISMISSED_KEY =
  'neurodesk-launcher:donation-notification-dismissed-v1';

let donationNotificationShown = false;

const BYTES_PER_GB = 1024 ** 3;
const MEMORY_SEGMENT_REGEX =
  /(Mem:\s*)(\d+(?:\.\d+)?)(?:\s*\/\s*(\d+(?:\.\d+)?))?\s*(B|KB|MB|GB|TB)\b/;
const UNIT_TO_GB_FACTOR: Record<string, number> = {
  B: 1 / BYTES_PER_GB,
  KB: 1024 / BYTES_PER_GB,
  MB: (1024 ** 2) / BYTES_PER_GB,
  GB: 1,
  TB: 1024
};

function showDonationNotificationOnce(): void {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    if (
      donationNotificationShown ||
      window.localStorage.getItem(DONATION_NOTIFICATION_DISMISSED_KEY) ===
        'dismissed'
    ) {
      return;
    }
    donationNotificationShown = true;
  } catch {
    // Keep going if localStorage is unavailable in this browser context.
  }

  const notificationId = Notification.info(DONATION_MESSAGE, {
    autoClose: false,
    actions: [
      {
        label: 'Open donation page',
        caption: 'Open the Neurodesk donations page in a new tab.',
        displayType: 'link',
        callback: (_event: MouseEvent) => {
          window.open(DONATION_URL, '_blank', 'noopener,noreferrer');
        }
      }
    ]
  });

  const onNotificationChanged = (
    _manager: unknown,
    change: Notification.IChange
  ): void => {
    if (change.type !== 'removed' || change.notification.id !== notificationId) {
      return;
    }

    try {
      window.localStorage.setItem(
        DONATION_NOTIFICATION_DISMISSED_KEY,
        'dismissed'
      );
    } catch {
      // Ignore storage write errors in restricted browser contexts.
    }

    Notification.manager.changed.disconnect(onNotificationChanged);
  };

  Notification.manager.changed.connect(onNotificationChanged);
}

function normalizeMemorySegmentToGb(text: string): string {
  const match = text.match(MEMORY_SEGMENT_REGEX);
  if (!match) {
    return text;
  }

  const [fullMatch, prefix, currentRaw, limitRaw, unitRaw] = match;
  const factor = UNIT_TO_GB_FACTOR[unitRaw.toUpperCase()];
  if (!factor) {
    return text;
  }

  const current = Number.parseFloat(currentRaw);
  if (Number.isNaN(current)) {
    return text;
  }

  const currentGb = (current * factor).toFixed(2);
  let replacement = `${prefix}${currentGb} GB`;

  if (limitRaw !== undefined) {
    const limit = Number.parseFloat(limitRaw);
    if (!Number.isNaN(limit)) {
      replacement = `${prefix}${currentGb} / ${(limit * factor).toFixed(2)} GB`;
    }
  }

  return text.replace(fullMatch, replacement);
}

function updateResourceUsageUnits(): void {
  document
    .querySelectorAll<HTMLElement>('[title="Current resource usage"]')
    .forEach(el => {
      const text = el.textContent;
      if (!text || !text.includes('Mem:')) {
        return;
      }

      const normalized = normalizeMemorySegmentToGb(text);
      if (normalized !== text) {
        el.textContent = normalized;
      }
    });
}

function startResourceUsageUnitOverride(): void {
  updateResourceUsageUnits();
  window.setInterval(updateResourceUsageUnits, 1000);
}

async function fetchSvgText(
  url: string,
  settings: ServerConnection.ISettings
): Promise<string | null> {
  try {
    const response = await ServerConnection.makeRequest(url, {}, settings);
    if (!response.ok) {
      return null;
    }
    const text = await response.text();
    if (text.includes('<svg')) {
      return text;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Category ordering via CSS flexbox order property.
 * Lower numbers appear first. Categories not listed get order 100.
 */
const CATEGORY_ORDER: Record<string, number> = {
  'Neurodesk': 1,
  'Webapps': 2
};
const DEFAULT_ORDER = 100;

/** Categories to hide from the launcher. */
const HIDDEN_CATEGORIES = new Set(['HPC Tools']);

/** Categories whose section-header icon should be replaced with the Neurodesk icon. */
const ICON_OVERRIDE_CATEGORIES = new Set(['Webapps']);

/** Neurodesk icon SVG, fetched once during activation. */
let neuroIconSvg: string | null = null;

/**
 * Apply CSS order to launcher category sections, hide unwanted categories,
 * and override section-header icons where configured.
 * Uses flexbox order instead of DOM moves so React doesn't undo it.
 */
function applyLauncherOrder(): void {
  // DOM structure: .jp-Launcher-body > .jp-Launcher-content > .jp-Launcher-section
  document.querySelectorAll('.jp-Launcher-content').forEach(content => {
    const el = content as HTMLElement;
    el.style.display = 'flex';
    el.style.flexDirection = 'column';

    content.querySelectorAll(':scope > .jp-Launcher-section').forEach(child => {
      const section = child as HTMLElement;
      const titleEl = section.querySelector('.jp-Launcher-sectionTitle');
      const name = titleEl?.textContent?.trim() || '';

      if (HIDDEN_CATEGORIES.has(name)) {
        section.style.display = 'none';
        return;
      }

      section.style.order = String(CATEGORY_ORDER[name] ?? DEFAULT_ORDER);

      // Replace the section-header icon with the Neurodesk icon
      if (ICON_OVERRIDE_CATEGORIES.has(name) && neuroIconSvg) {
        const sectionHeader = section.querySelector('.jp-Launcher-sectionHeader');
        if (!sectionHeader) {
          return;
        }
        const existingSvg = sectionHeader.querySelector('svg');
        if (existingSvg && existingSvg.dataset.neurodesk) {
          return; // already replaced
        }
        if (existingSvg) {
          const temp = document.createElement('div');
          temp.innerHTML = neuroIconSvg;
          const newSvg = temp.querySelector('svg');
          if (newSvg) {
            // Preserve the existing classes and dimensions
            newSvg.setAttribute('class', existingSvg.getAttribute('class') || '');
            if (existingSvg.hasAttribute('width')) {
              newSvg.setAttribute('width', existingSvg.getAttribute('width')!);
            }
            if (existingSvg.hasAttribute('height')) {
              newSvg.setAttribute('height', existingSvg.getAttribute('height')!);
            }
            newSvg.dataset.neurodesk = 'true';
            existingSvg.replaceWith(newSvg);
          }
        }
      }
    });
  });
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'neurodesk-launcher:plugin',
  description: 'Neurodesk launcher with proper icons for custom categories',
  autoStart: true,
  requires: [ILauncher],
  activate: async (app: JupyterFrontEnd, launcher: ILauncher) => {
    showDonationNotificationOnce();
    startResourceUsageUnitOverride();

    const settings = ServerConnection.makeSettings();
    const infoUrl = URLExt.join(
      settings.baseUrl,
      'server-proxy',
      'servers-info'
    );

    let data: IServersInfoResponse;
    try {
      const resp = await ServerConnection.makeRequest(infoUrl, {}, settings);
      if (!resp.ok) {
        console.error(
          'neurodesk-launcher: /server-proxy/servers-info returned',
          resp.status
        );
        return;
      }
      data = await resp.json();
    } catch (err) {
      console.error('neurodesk-launcher: failed to fetch servers-info', err);
      return;
    }

    // Fetch the Neurodesk icon for use as category header icon.
    // Construct URL directly (like infoUrl) to avoid base-path issues on JupyterHub.
    const ndProcess = (data.server_processes || []).find(
      sp => sp.name === 'neurodesktop'
    );
    if (ndProcess) {
      const iconUrl = URLExt.join(
        settings.baseUrl,
        'server-proxy',
        'icon',
        ndProcess.name
      );
      neuroIconSvg = await fetchSvgText(iconUrl, settings);
    }

    for (const sp of data.server_processes || []) {
      const { launcher_entry: entry, name, new_browser_tab: newTab } = sp;
      if (!entry.enabled) {
        continue;
      }
      const title = entry.title || name;
      const category = entry.category || 'Other';
      const pathInfo = entry.path_info || name;
      const url = URLExt.join(settings.baseUrl, pathInfo) + '/';

      // Fetch SVG icon via the server-proxy icon endpoint.
      // Construct URL directly (like infoUrl) to avoid base-path issues on JupyterHub.
      let icon: LabIcon | undefined;
      const iconFullUrl = URLExt.join(
        settings.baseUrl,
        'server-proxy',
        'icon',
        name
      );
      const svgStr = await fetchSvgText(iconFullUrl, settings);
      if (svgStr) {
        icon = new LabIcon({
          name: `neurodesk-launcher:${name}`,
          svgstr: svgStr
        });
      }

      // Register a unique command for this server
      const commandId = `neurodesk-launcher:open-${name}`;
      app.commands.addCommand(commandId, {
        label: title,
        icon: icon,
        execute: () => {
          window.open(url, newTab ? '_blank' : '_self');
        }
      });

      launcher.add({
        command: commandId,
        category: category,
        rank: 0
      });
    }

    // Add built-in tools to the Neurodesk category
    launcher.add({
      command: 'terminal:create-new',
      category: 'Neurodesk',
      rank: 2
    });
    launcher.add({
      command: 'scheduling:list-jobs-from-launcher',
      category: 'Neurodesk',
      rank: 3
    });
    launcher.add({
      command: 'slurm:open',
      category: 'Neurodesk',
      rank: 4
    });

    // Apply category ordering via CSS flexbox order property.
    // MutationObserver ensures ordering is applied whenever the launcher re-renders.
    const observer = new MutationObserver(() => {
      applyLauncherOrder();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    requestAnimationFrame(() => applyLauncherOrder());
  }
};

export default plugin;
