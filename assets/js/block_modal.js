/**
 * Role: Mounts the GitHub Pull Request block modal frontend.
 * File Name: block_modal.js
 * Author: Alexandre EL
 * Email: alex@hackinvent.com
 * Created Date: 2026-06-10
 */

(function () {
  "use strict";

  const registry = (window.CWBlockUiBlocks = window.CWBlockUiBlocks || {});

  registry.github_pull_request = {
    /**
     * Mark the GitHub Pull Request modal as block-owned while generic fields
     * and Apply stay handled by the framework modal API.
     *
     * @param {HTMLElement} root - Mounted GitHub Pull Request modal root.
     */
    mount(root) {
      if (root instanceof HTMLElement) {
        root.dataset.githubPullRequestModalMounted = "true";
      }
    },
  };
})();
