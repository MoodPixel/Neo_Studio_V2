// SD-28.7 — read-only release UX for Scene Director Inspector.
// This file only renders metadata; it never mutates generation state.
(function () {
  const api = window.NeoSceneDirectorInspector;
  if (!api) return;

  const originalGetInspector = api.getInspector;
  const originalRender = api.render;

  function asObject(value) {
    if (!value) return {};
    if (typeof value === 'string') {
      try { return JSON.parse(value); } catch (_err) { return {}; }
    }
    return typeof value === 'object' ? value : {};
  }

  function getInspector(metadata) {
    const meta = asObject(metadata);
    const patch = asObject(meta.workflow_patch);
    const validation = asObject(meta.validation);
    return asObject(
      meta.inspector_debug_ui ||
      meta.scene_director_release_inspector ||
      patch.inspector_debug_ui ||
      patch.scene_director_release_inspector ||
      validation.inspector_debug_ui ||
      (meta.metadata && meta.metadata.inspector_debug_ui) ||
      (originalGetInspector ? originalGetInspector(metadata) : {})
    );
  }

  function resolveRoot(container) {
    return typeof container === 'string' ? document.querySelector(container) : container;
  }

  function makeChip(chip) {
    const el = document.createElement('span');
    el.className = `neo-scene-release-chip tone-${String(chip.tone || 'neutral')}`;
    el.dataset.chipId = String(chip.id || 'status');
    el.title = String(chip.detail || '');

    const label = document.createElement('span');
    label.className = 'neo-scene-release-chip-label';
    label.textContent = String(chip.label || chip.id || 'Status');

    const state = document.createElement('strong');
    state.textContent = String(chip.state || 'unknown').replace(/_/g, ' ');

    el.append(label, state);
    return el;
  }

  function enhance(root, inspector) {
    if (!root || !inspector || !inspector.schema) return;
    root.hidden = false;
    const section = root.querySelector('.neo-scene-inspector');
    if (!section) return;
    section.dataset.releaseLock = String((inspector.release_lock || {}).status || 'unknown');
    section.dataset.gpuProof = String((inspector.gpu_proof || {}).status || 'not_requested');

    const header = section.querySelector('.neo-scene-inspector-header');
    if (header && inspector.summary) {
      let summary = header.querySelector('.neo-scene-release-summary');
      if (!summary) {
        summary = document.createElement('p');
        summary.className = 'neo-scene-release-summary';
        header.firstElementChild?.appendChild(summary);
      }
      summary.textContent = String(inspector.summary);
    }

    let chips = section.querySelector('.neo-scene-release-chips');
    if (!chips) {
      chips = document.createElement('div');
      chips.className = 'neo-scene-release-chips';
      const headerNode = section.querySelector('.neo-scene-inspector-header');
      headerNode?.insertAdjacentElement('afterend', chips);
    }
    if (chips) {
      chips.replaceChildren(...(Array.isArray(inspector.status_chips) ? inspector.status_chips.map(makeChip) : []));
    }

    const proof = asObject(inspector.gpu_proof);
    if (proof.required && !proof.proven) {
      section.setAttribute('aria-label', 'Scene Director release locked; regional LoRA GPU proof pending');
    } else if (proof.proven) {
      section.setAttribute('aria-label', 'Scene Director release locked; regional LoRA GPU proof verified');
    }
  }

  api.getInspector = getInspector;
  api.render = function renderReleaseInspector(container, metadata) {
    const inspector = getInspector(metadata);
    const rendered = originalRender(container, { inspector_debug_ui: inspector });
    enhance(resolveRoot(container), inspector);
    return rendered;
  };

  window.NeoSceneDirectorReleaseUX = {
    phase: 'SD-28.7',
    schema: 'neo.image.scene_director.release_ux.v1',
    getInspector,
    enhance,
  };
})();
