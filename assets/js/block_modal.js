import { withProperties } from "./properties.js";

/**
 * Role: Mounts the GitHub Pull Request block modal frontend.
 * File Name: block_modal.js
 * Author: Alexandre EL
 * Email: alex@hackinvent.com
 * Created Date: 2026-06-10
 */

/**
 * Mark the GitHub Pull Request modal as block-owned while generic fields
 * and Apply stay handled by the framework modal API.
 *
 * @param {HTMLElement} root - Mounted GitHub Pull Request modal root.
 */
function mountOwned(root) {
  if (root instanceof HTMLElement) {
    root.dataset.githubPullRequestModalMounted = "true";
  }
}

/** Keep the block behavior and add properties-only accessibility. */
export function mount(root, ...args) {
  return withProperties(mountOwned).call(this, root, ...args);
}
