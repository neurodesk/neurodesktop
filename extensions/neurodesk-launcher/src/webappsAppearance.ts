/** Keep the catalog last and reuse its rendered icon after React updates. */
export function applyWebappsAppearance(section: HTMLElement): void {
  const cards = section.querySelectorAll<HTMLElement>('.jp-LauncherCard');
  let catalog: HTMLElement | undefined;
  cards.forEach(card => {
    const isCatalog = Boolean(card.querySelector(
      '[data-icon="neurodesk-launcher:more-webapps"]'
    )) || card.querySelector('.jp-LauncherCard-label')?.textContent?.trim() === 'More webapps';
    card.style.order = isCatalog ? '1' : '0';
    if (isCatalog) {
      catalog = card;
    }
  });

  const source = catalog?.querySelector('svg');
  const existing = section.querySelector('.jp-Launcher-sectionHeader svg');
  if (!source || !existing) {
    return;
  }
  const replacement = source.cloneNode(true) as SVGElement;
  // Keep the heading's sizing, but use the exact same artwork as the tile.
  for (const attribute of ['class', 'width', 'height']) {
    if (existing.hasAttribute(attribute)) {
      replacement.setAttribute(attribute, existing.getAttribute(attribute)!);
    }
  }
  replacement.dataset.neurodesk = 'true';
  if (!existing.isEqualNode(replacement)) {
    existing.replaceWith(replacement);
  }
}
