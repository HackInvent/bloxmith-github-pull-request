/** Properties-only accessibility. Never changes values, config bindings or runtime state. */
export function enhanceProperties(root) {
  if (!root?.querySelector || !(root.matches('[data-properties-surface]') || root.querySelector('[data-properties-surface]'))) return () => {};
  const controller = new AbortController();
  const { signal } = controller;
  // IDs are DOM labels, not security tokens. Keep HTTP/LAN installations usable.
  const unique = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const prefix = `properties-${unique}`;
  let counter = 0, pending = false;
  const id = element => element.id || (element.id = `${prefix}-${++counter}`);

  /** Associate existing visible labels; keep every authored aria label and ID intact. */
  function associate() {
    pending = false;
    if (signal.aborted) return;
    for (const control of root.querySelectorAll('input:not([type="hidden"]),textarea,select')) {
      if (!control.labels?.length && !control.hasAttribute('aria-label') && !control.hasAttribute('aria-labelledby')) {
        const group = control.closest('.field-group,.cli-command-row');
        const label = control.closest('label') || group?.querySelector('label');
        if (label) {
          const siblings = group?.querySelectorAll('input:not([type="hidden"]),textarea,select');
          if (siblings?.length === 1 && !label.htmlFor && !label.contains(control)) label.htmlFor = id(control);
          else control.setAttribute('aria-labelledby', id(label));
        }
      }
      if (!control.hasAttribute('aria-describedby')) {
        const group = control.closest('.field-group,.cli-command-row');
        const hint = group?.querySelector('.field-hint,small,.inspector-copy');
        if (hint) control.setAttribute('aria-describedby', id(hint));
      }
    }
  }
  associate();
  const observer = new MutationObserver(() => {
    if (pending || signal.aborted) return;
    pending = true;
    queueMicrotask(associate);
  });
  observer.observe(root, { childList: true, subtree: true });

  // Existing block keyboard handlers run first; add only the missing tab behavior.
  root.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const tab = event.target.closest?.('[role="tab"]');
    const list = tab?.closest('[role="tablist"]');
    if (!tab || !list || !root.contains(list)) return;
    const tabs = [...list.querySelectorAll('[role="tab"]')].filter(t => !t.disabled && t.getClientRects().length);
    const index = tabs.indexOf(tab);
    if (index < 0) return;
    const next = { ArrowRight: (index + 1) % tabs.length, ArrowDown: (index + 1) % tabs.length,
      ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowUp: (index + tabs.length - 1) % tabs.length,
      Home: 0, End: tabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    tabs[next].click();
    tabs[next].focus();
  }, { signal });
  return () => { controller.abort(); observer.disconnect(); };
}

/** Compose with the original surface lifecycle, retaining its API and cleanup. */
export function withProperties(mountOwned) {
  return function mount(root, ...args) {
    const owned = mountOwned.call(this, root, ...args);
    function compose(binding) {
      const cleanup = enhanceProperties(root);
      let disposed = false;
      const dispose = () => {
        if (disposed) return;
        disposed = true;
        cleanup();
        if (typeof binding === 'function') binding();
        else binding?.dispose?.();
      };
      return typeof binding === 'function' ? dispose : { ...binding, dispose };
    }
    return owned?.then ? owned.then(compose) : compose(owned);
  };
}
