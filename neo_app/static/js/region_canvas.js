(function () {
  'use strict';

  const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
  const DEFAULT_FRAME_LIMITS = Object.freeze({ maxWidth: 760, maxHeight: 680, minWidth: 120, minHeight: 120 });
  const FIT_STATE = new WeakMap();
  const FIT_GROUPS = new Map();

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function clamp(value, minimum, maximum) {
    const number = Number(value);
    return Math.max(minimum, Math.min(maximum, Number.isFinite(number) ? number : minimum));
  }

  function positiveNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : fallback;
  }

  function clampBox(box = {}, minSize = 0.03) {
    const safeMin = clamp(minSize, 0.005, 0.5);
    const w = clamp(box.w ?? 0.28, safeMin, 1);
    const h = clamp(box.h ?? 0.70, safeMin, 1);
    const x = clamp(box.x ?? 0.08, 0, Math.max(0, 1 - w));
    const y = clamp(box.y ?? 0.14, 0, Math.max(0, 1 - h));
    return { x, y, w, h };
  }

  function sizeSnapshot(width, height) {
    const safeWidth = positiveNumber(width, 1024);
    const safeHeight = positiveNumber(height, 1024);
    const aspect = clamp(safeWidth / safeHeight, 0.25, 4);
    const orientation = Math.abs(aspect - 1) < 0.04 ? 'square' : (aspect > 1 ? 'landscape' : 'portrait');
    return { width: Math.round(safeWidth), height: Math.round(safeHeight), aspect, orientation };
  }

  function frameSnapshot(width, height, options = {}) {
    const size = sizeSnapshot(width, height);
    const maxWidth = positiveNumber(options.maxWidth, DEFAULT_FRAME_LIMITS.maxWidth);
    const maxHeight = positiveNumber(options.maxHeight, DEFAULT_FRAME_LIMITS.maxHeight);
    const minWidth = Math.min(maxWidth, positiveNumber(options.minWidth, DEFAULT_FRAME_LIMITS.minWidth));
    const minHeight = Math.min(maxHeight, positiveNumber(options.minHeight, DEFAULT_FRAME_LIMITS.minHeight));

    // Width is the responsive CSS authority. Constrain it by both the inline
    // limit and the maximum block size so portrait frames never become a huge
    // full-width column and landscape frames never overflow their panel.
    const widthFromHeight = maxHeight * size.aspect;
    let frameWidth = Math.min(maxWidth, widthFromHeight);
    let frameHeight = frameWidth / size.aspect;

    // The aspect clamp keeps these minimum guards from violating the limits in
    // normal use. They only protect deliberately tiny custom limits.
    if (frameWidth < minWidth && minWidth / size.aspect <= maxHeight) {
      frameWidth = minWidth;
      frameHeight = frameWidth / size.aspect;
    }
    if (frameHeight < minHeight && minHeight * size.aspect <= maxWidth) {
      frameHeight = minHeight;
      frameWidth = frameHeight * size.aspect;
    }

    frameWidth = Math.min(frameWidth, maxWidth);
    frameHeight = Math.min(frameHeight, maxHeight);
    return {
      ...size,
      frameWidth: Number(frameWidth.toFixed(3)),
      frameHeight: Number(frameHeight.toFixed(3)),
      maxWidth,
      maxHeight,
    };
  }

  function frameStyle(width, height, options = {}) {
    const frame = frameSnapshot(width, height, options);
    return [
      `--neo-region-aspect:${frame.aspect}`,
      `--neo-region-frame-max-inline:${frame.frameWidth}px`,
      `--neo-region-frame-max-block:${frame.frameHeight}px`,
      `--neo-region-source-width:${frame.width}`,
      `--neo-region-source-height:${frame.height}`,
    ].join(';') + ';';
  }

  function fitSnapshot(width, height, availableInline, options = {}) {
    const base = frameSnapshot(width, height, options);
    const inlineLimit = Math.max(1, Math.min(base.frameWidth, positiveNumber(availableInline, base.frameWidth)));
    const blockLimit = Math.max(1, Math.min(base.frameHeight, positiveNumber(options.availableBlock, base.frameHeight)));
    const scale = Math.min(inlineLimit / base.width, blockLimit / base.height);
    const frameWidth = Math.max(1, base.width * scale);
    const frameHeight = Math.max(1, base.height * scale);
    return {
      ...base,
      availableInline: Number(inlineLimit.toFixed(3)),
      availableBlock: Number(blockLimit.toFixed(3)),
      fittedWidth: Number(frameWidth.toFixed(3)),
      fittedHeight: Number(frameHeight.toFixed(3)),
      scale: Number(scale.toFixed(6)),
    };
  }

  function numericDataset(node, key, fallback) {
    const value = Number(node?.dataset?.[key]);
    return Number.isFinite(value) && value > 0 ? value : fallback;
  }

  function wrapperContentWidth(wrapper) {
    if (!wrapper) return 0;
    const style = typeof window.getComputedStyle === 'function' ? window.getComputedStyle(wrapper) : null;
    const left = Number.parseFloat(style?.paddingLeft || '0') || 0;
    const right = Number.parseFloat(style?.paddingRight || '0') || 0;
    return Math.max(0, Number(wrapper.clientWidth || 0) - left - right);
  }

  function fitGroupContentWidth(root, fallback) {
    const group = String(root?.dataset?.neoRegionFitGroup || '').trim();
    if (!group || typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') return fallback;
    const widths = Array.from(document.querySelectorAll('[data-neo-region-fit-group]'))
      .filter((node) => String(node?.dataset?.neoRegionFitGroup || '').trim() === group)
      .map((node) => wrapperContentWidth(node.closest?.('.neo-region-canvas-wrap') || node.parentElement))
      .filter((value) => Number.isFinite(value) && value >= 2);
    return widths.length ? Math.min(fallback, ...widths) : fallback;
  }

  function fit(root, options = {}) {
    if (!root) return null;
    const wrapper = root.closest?.('.neo-region-canvas-wrap') || root.parentElement;
    if (!wrapper) return null;
    const availableInline = fitGroupContentWidth(root, wrapperContentWidth(wrapper));
    if (availableInline < 2) {
      root.dataset.neoRegionFitState = 'waiting';
      wrapper.dataset.neoRegionFitState = 'waiting';
      return null;
    }
    const sourceWidth = numericDataset(root, 'neoRegionSourceWidth', positiveNumber(options.width, 1024));
    const sourceHeight = numericDataset(root, 'neoRegionSourceHeight', positiveNumber(options.height, 1024));
    const maxWidth = numericDataset(root, 'neoRegionFrameMaxInline', positiveNumber(options.maxWidth, DEFAULT_FRAME_LIMITS.maxWidth));
    const maxHeight = numericDataset(root, 'neoRegionFrameMaxBlock', positiveNumber(options.maxHeight, DEFAULT_FRAME_LIMITS.maxHeight));
    const fitted = fitSnapshot(sourceWidth, sourceHeight, availableInline, {
      maxWidth,
      maxHeight,
      availableBlock: maxHeight,
      minWidth: options.minWidth,
      minHeight: options.minHeight,
    });
    root.style.width = `${fitted.fittedWidth}px`;
    root.style.height = `${fitted.fittedHeight}px`;
    root.style.maxWidth = 'none';
    root.style.maxHeight = 'none';
    root.style.aspectRatio = 'auto';
    root.style.setProperty('--neo-region-fitted-inline', `${fitted.fittedWidth}px`);
    root.style.setProperty('--neo-region-fitted-block', `${fitted.fittedHeight}px`);
    root.dataset.neoRegionFitState = 'ready';
    root.dataset.neoRegionFittedWidth = String(fitted.fittedWidth);
    root.dataset.neoRegionFittedHeight = String(fitted.fittedHeight);
    wrapper.dataset.neoRegionFitState = 'ready';
    wrapper.style.setProperty('--neo-region-fitted-inline', `${fitted.fittedWidth}px`);
    wrapper.style.setProperty('--neo-region-fitted-block', `${fitted.fittedHeight}px`);
    options.onFit?.(fitted, root, wrapper);
    return fitted;
  }

  function observeFit(root, options = {}) {
    if (!root) return () => {};
    const existing = FIT_STATE.get(root);
    if (existing) {
      existing.schedule();
      return existing.cleanup;
    }
    const wrapper = root.closest?.('.neo-region-canvas-wrap') || root.parentElement;
    let rafId = 0;
    let secondRafId = 0;
    let disposed = false;
    const requestFrame = typeof window.requestAnimationFrame === 'function'
      ? window.requestAnimationFrame.bind(window)
      : (callback) => window.setTimeout(callback, 0);
    const cancelFrame = typeof window.cancelAnimationFrame === 'function'
      ? window.cancelAnimationFrame.bind(window)
      : window.clearTimeout.bind(window);

    const fitGroup = String(root?.dataset?.neoRegionFitGroup || '').trim();
    let stateEntry = null;
    const cleanup = () => {
      if (disposed) return;
      disposed = true;
      if (rafId) cancelFrame(rafId);
      if (secondRafId) cancelFrame(secondRafId);
      observer?.disconnect?.();
      window.removeEventListener?.('resize', schedule);
      if (fitGroup && stateEntry) {
        const group = FIT_GROUPS.get(fitGroup);
        group?.delete?.(stateEntry);
        if (group && group.size === 0) FIT_GROUPS.delete(fitGroup);
      }
      FIT_STATE.delete(root);
    };
    const run = () => {
      rafId = 0;
      if (!root.isConnected) {
        cleanup();
        return;
      }
      fit(root, options);
    };
    const scheduleOwn = () => {
      if (disposed) return;
      if (rafId) cancelFrame(rafId);
      rafId = requestFrame(run);
    };
    const schedule = () => {
      if (disposed) return;
      if (!fitGroup) {
        scheduleOwn();
        return;
      }
      const group = FIT_GROUPS.get(fitGroup);
      if (!group?.size) {
        scheduleOwn();
        return;
      }
      group.forEach((entry) => entry.scheduleOwn());
    };
    const observer = typeof window.ResizeObserver === 'function'
      ? new window.ResizeObserver(schedule)
      : null;
    observer?.observe?.(wrapper || root);
    window.addEventListener?.('resize', schedule, { passive: true });
    stateEntry = { root, cleanup, schedule, scheduleOwn };
    FIT_STATE.set(root, stateEntry);
    if (fitGroup) {
      if (!FIT_GROUPS.has(fitGroup)) FIT_GROUPS.set(fitGroup, new Set());
      FIT_GROUPS.get(fitGroup).add(stateEntry);
    }
    schedule();
    // A second frame catches accordion expansion, late CSS, fonts, and sidebar
    // width settlement that can happen after the first DOM measurement.
    secondRafId = requestFrame(() => {
      secondRafId = 0;
      schedule();
    });
    return cleanup;
  }

  function regionMarkup(region, index, selectedIndex, disabled, minSize) {
    const box = clampBox(region.box || {}, minSize);
    const selected = index === Number(selectedIndex ?? 0) ? ' selected' : '';
    const hidden = region.hidden ? ' hidden-region' : '';
    const locked = region.locked ? ' locked-region' : '';
    const tone = String(region.tone || 'character').replace(/[^a-z0-9_-]/gi, '');
    const canResize = !disabled && !region.disabled && !region.locked && region.resizable !== false;
    const handles = canResize
      ? HANDLES.map((handle) => `<span class="neo-region-canvas__resize ${handle}" data-neo-region-resize="${handle}" aria-hidden="true"></span>`).join('')
      : '';
    const footer = region.footer ? `<small>${escapeHtml(region.footer)}</small>` : '';
    const image = region.image
      ? `<img class="neo-region-canvas__region-image ${escapeHtml(region.imageClass || '')}" src="${escapeHtml(region.image)}" alt="" draggable="false">`
      : '';
    return `<button type="button" class="neo-region-canvas__region ${tone}${selected}${hidden}${locked}" data-neo-region-index="${index}" ${disabled || region.disabled ? 'disabled' : ''} data-neo-region-draggable="${region.draggable === false ? '0' : '1'}" style="left:${box.x * 100}%;top:${box.y * 100}%;width:${box.w * 100}%;height:${box.h * 100}%;--neo-region-index:${index};" title="${escapeHtml(region.title || region.label || `Region ${index + 1}`)}">
      ${image}
      <span class="neo-region-canvas__label">${escapeHtml(region.label || `Region ${index + 1}`)}</span>
      ${region.subtitle ? `<em>${escapeHtml(region.subtitle)}</em>` : ''}
      ${footer}
      ${handles}
    </button>`;
  }

  function render(config = {}) {
    const id = String(config.id || `neoRegionCanvas_${Math.random().toString(36).slice(2)}`);
    const frame = frameSnapshot(config.width, config.height, config);
    const regions = Array.isArray(config.regions) ? config.regions : [];
    const minSize = Number(config.minSize ?? 0.03);
    const boxes = regions.map((region, index) => regionMarkup(region || {}, index, config.selectedIndex, Boolean(config.disabled), minSize)).join('');
    const backgroundStyle = config.backgroundUrl
      ? `background-image:linear-gradient(rgba(2,6,23,.32),rgba(2,6,23,.32)),url('${escapeHtml(config.backgroundUrl)}');`
      : '';
    const geometryStyle = frameStyle(frame.width, frame.height, frame);
    const fitGroup = String(config.fitGroup || '').trim();
    const fitGroupAttr = fitGroup ? ` data-neo-region-fit-group="${escapeHtml(fitGroup)}"` : '';
    const emptyMarkup = config.showEmpty === false ? '' : `<p class="neo-muted neo-region-canvas__empty">${escapeHtml(config.emptyText || 'Add a region to begin.')}</p>`;
    return `<div class="neo-region-canvas-wrap ${escapeHtml(frame.orientation)} ${escapeHtml(config.wrapClass || '')}" data-neo-region-orientation="${escapeHtml(frame.orientation)}" data-neo-region-fit-state="pending"${fitGroupAttr}>
      <div id="${escapeHtml(id)}" class="neo-region-canvas ${escapeHtml(config.className || '')}" data-neo-region-canvas="1" data-neo-region-fit-state="pending" data-neo-region-source-width="${frame.width}" data-neo-region-source-height="${frame.height}" data-neo-region-frame-max-inline="${frame.frameWidth}" data-neo-region-frame-max-block="${frame.frameHeight}"${fitGroupAttr} aria-label="${escapeHtml(config.ariaLabel || 'Region canvas')}" style="${geometryStyle}${backgroundStyle}">
        <div class="neo-region-canvas__grid"></div>
        ${config.layersMarkup || ''}
        ${boxes || emptyMarkup}
      </div>
    </div>`;
  }

  function renderSurface(config = {}) {
    return render({ ...config, regions: [], showEmpty: false });
  }

  function interactionSnapshot(handle, startBox, nextBox, pointerId = null, minSize = 0.03) {
    const safeHandle = HANDLES.includes(String(handle || '')) ? String(handle) : '';
    return {
      kind: safeHandle ? 'resize' : 'translate',
      handle: safeHandle,
      startBox: clampBox(startBox || {}, minSize),
      nextBox: clampBox(nextBox || startBox || {}, minSize),
      pointerId,
    };
  }

  function applyBoxStyle(node, box) {
    node.style.left = `${box.x * 100}%`;
    node.style.top = `${box.y * 100}%`;
    node.style.width = `${box.w * 100}%`;
    node.style.height = `${box.h * 100}%`;
  }

  function bind(config = {}) {
    const root = typeof config.root === 'string' ? document.querySelector(config.root) : config.root;
    if (!root) return;
    observeFit(root, config);
    if (root.dataset.neoRegionBound === '1') return;
    root.dataset.neoRegionBound = '1';
    const minSize = Number(config.minSize ?? 0.03);

    root.querySelectorAll('[data-neo-region-index]').forEach((node) => {
      node.addEventListener('click', () => {
        if (node.dataset.neoRegionMoved === '1') {
          node.dataset.neoRegionMoved = '0';
          return;
        }
        config.onSelect?.(Number(node.dataset.neoRegionIndex || 0));
      });

      node.addEventListener('pointerdown', (event) => {
        if (node.disabled || event.button !== 0) return;
        const index = Number(node.dataset.neoRegionIndex || 0);
        const handle = event.target?.closest?.('[data-neo-region-resize]')?.getAttribute('data-neo-region-resize') || '';
        if (!handle && node.dataset.neoRegionDraggable === '0') return;
        const canvasRect = root.getBoundingClientRect();
        if (!canvasRect.width || !canvasRect.height) return;
        const startBox = clampBox(config.getBox?.(index) || {}, minSize);
        const startX = event.clientX;
        const startY = event.clientY;
        const baseInteraction = interactionSnapshot(handle, startBox, startBox, event.pointerId, minSize);
        let moved = false;
        node.setPointerCapture?.(event.pointerId);
        config.onStart?.(index, baseInteraction, node);
        event.preventDefault();
        event.stopPropagation();

        const move = (moveEvent) => {
          const dx = (moveEvent.clientX - startX) / canvasRect.width;
          const dy = (moveEvent.clientY - startY) / canvasRect.height;
          let { x, y, w, h } = startBox;
          if (handle) {
            if (handle.includes('w')) {
              const nextX = clamp(startBox.x + dx, 0, startBox.x + startBox.w - minSize);
              w = startBox.x + startBox.w - nextX;
              x = nextX;
            }
            if (handle.includes('e')) w = clamp(startBox.w + dx, minSize, 1 - startBox.x);
            if (handle.includes('n')) {
              const nextY = clamp(startBox.y + dy, 0, startBox.y + startBox.h - minSize);
              h = startBox.y + startBox.h - nextY;
              y = nextY;
            }
            if (handle.includes('s')) h = clamp(startBox.h + dy, minSize, 1 - startBox.y);
          } else {
            x = clamp(startBox.x + dx, 0, 1 - startBox.w);
            y = clamp(startBox.y + dy, 0, 1 - startBox.h);
          }
          const nextBox = clampBox({ x, y, w, h }, minSize);
          const interaction = interactionSnapshot(handle, startBox, nextBox, event.pointerId, minSize);
          moved = true;
          applyBoxStyle(node, nextBox);
          config.onPreview?.(index, nextBox, node, interaction);
          node.__neoNextRegionBox = nextBox;
        };

        const cleanup = () => {
          window.removeEventListener('pointermove', move);
          window.removeEventListener('pointerup', up);
          window.removeEventListener('pointercancel', cancel);
          try { node.releasePointerCapture?.(event.pointerId); } catch (_error) {}
        };
        const up = () => {
          cleanup();
          const nextBox = node.__neoNextRegionBox || startBox;
          delete node.__neoNextRegionBox;
          if (!moved) return;
          node.dataset.neoRegionMoved = '1';
          config.onCommit?.(index, nextBox, interactionSnapshot(handle, startBox, nextBox, event.pointerId, minSize), node);
        };
        const cancel = () => {
          cleanup();
          delete node.__neoNextRegionBox;
          applyBoxStyle(node, startBox);
          config.onCancel?.(index, baseInteraction, node);
        };
        window.addEventListener('pointermove', move, { passive: true });
        window.addEventListener('pointerup', up, { once: true });
        window.addEventListener('pointercancel', cancel, { once: true });
      });
    });
  }

  window.NeoRegionCanvas = Object.freeze({
    HANDLES,
    DEFAULT_FRAME_LIMITS,
    clamp,
    clampBox,
    sizeSnapshot,
    frameSnapshot,
    frameStyle,
    fitSnapshot,
    fit,
    observeFit,
    render,
    renderSurface,
    interactionSnapshot,
    bind,
  });
})();
