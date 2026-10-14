// Neo Studio V2 — Shared declarative Comfy Space renderer (NCS-5C; NCS-11 dynamic controls)
// Consumes declarative Space contracts only. It never executes Comfy or generated per-Space code.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.NeoComfySpaceRenderer = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const RENDERER_SCHEMA_VERSION = 'neo.comfy_space.renderer.v1';
  const SUPPORTED_MANIFEST = 'neo.comfy_space.manifest.v1';
  const SUPPORTED_UI = 'neo.comfy_space.ui.v1';
  const SUPPORTED_DYNAMIC_CONTROLS = 'neo.comfy_space.dynamic_controls.v1';
  const INPUT_CONTROLS = new Set([
    'text_input', 'text_area', 'integer_input', 'number_input', 'slider', 'seed_input', 'toggle',
    'select', 'search_select', 'model_select', 'json_editor', 'image_upload', 'mask_upload',
    'video_upload', 'audio_upload', 'file_upload',
  ]);
  const OUTPUT_RENDERERS = new Set([
    'text_viewer', 'numeric_value', 'boolean_value', 'choice_value', 'json_viewer', 'image_preview',
    'mask_preview', 'video_player', 'audio_player', 'file_download',
  ]);
  const ASSET_CONTROLS = new Set(['image_upload', 'mask_upload', 'video_upload', 'audio_upload', 'file_upload']);
  const instances = new Map();

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  }
  function attr(value) { return esc(value); }
  function safeJson(value) {
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value ?? ''); }
  }
  function safeUrl(value) {
    const raw = typeof value === 'string' ? value : (value && typeof value === 'object' ? value.url : '');
    const text = String(raw || '').trim();
    if (!text) return '';
    if (/^(https?:|blob:)/i.test(text)) return text;
    if (/^data:image\/(png|jpeg|jpg|webp|gif);/i.test(text)) return text;
    if (text.startsWith('/') || text.startsWith('./')) return text;
    return '';
  }
  function labelFor(value) {
    if (value && typeof value === 'object') return value.name || value.filename || value.label || value.url || 'Result';
    return String(value ?? '');
  }
  function ordered(rows) {
    return [...(Array.isArray(rows) ? rows : [])].sort((a, b) => (Number(a?.order) || 0) - (Number(b?.order) || 0));
  }

  function validate(manifest, ui, dynamicControls = null) {
    const errors = [];
    if (!manifest || typeof manifest !== 'object') errors.push('Missing Space manifest.');
    if (!ui || typeof ui !== 'object') errors.push('Missing Space UI contract.');
    if (errors.length) return { valid: false, errors };
    if (manifest.schema_version !== SUPPORTED_MANIFEST) errors.push(`Unsupported manifest schema: ${manifest.schema_version || 'missing'}.`);
    if (ui.schema_version !== SUPPORTED_UI) errors.push(`Unsupported Space UI schema: ${ui.schema_version || 'missing'}.`);
    if (manifest.space_id !== ui.space_id) errors.push('Manifest and Space UI use different space_id values.');
    if (manifest.version !== ui.version) errors.push('Manifest and Space UI use different versions.');
    if (ui.execution_authority !== false || ui.binding_authority !== false) errors.push('Space UI must remain presentation-only.');

    const manifestInputs = new Map((manifest.inputs || []).map((item) => [item.input_id, item]));
    const manifestOutputs = new Map((manifest.outputs || []).map((item) => [item.output_id, item]));
    const sectionIds = new Set();
    for (const section of ui.layout?.sections || []) {
      if (!section?.section_id) { errors.push('Space UI contains a section without section_id.'); continue; }
      if (sectionIds.has(section.section_id)) errors.push(`Duplicate section_id: ${section.section_id}.`);
      sectionIds.add(section.section_id);
    }
    const uiInputs = new Set();
    for (const input of ui.inputs || []) {
      if (!manifestInputs.has(input.input_id)) errors.push(`Unknown UI input_id: ${input.input_id}.`);
      if (uiInputs.has(input.input_id)) errors.push(`Duplicate UI input_id: ${input.input_id}.`);
      if (!INPUT_CONTROLS.has(input.control)) errors.push(`Unsupported input control '${input.control}' for ${input.input_id}.`);
      if (!sectionIds.has(input.section_id)) errors.push(`Input '${input.input_id}' references unknown section '${input.section_id}'.`);
      uiInputs.add(input.input_id);
    }
    for (const id of manifestInputs.keys()) if (!uiInputs.has(id)) errors.push(`Manifest input '${id}' has no UI presentation.`);

    const uiOutputs = new Set();
    for (const output of ui.outputs || []) {
      if (!manifestOutputs.has(output.output_id)) errors.push(`Unknown UI output_id: ${output.output_id}.`);
      if (uiOutputs.has(output.output_id)) errors.push(`Duplicate UI output_id: ${output.output_id}.`);
      if (!OUTPUT_RENDERERS.has(output.renderer)) errors.push(`Unsupported output renderer '${output.renderer}' for ${output.output_id}.`);
      uiOutputs.add(output.output_id);
    }
    for (const id of manifestOutputs.keys()) if (!uiOutputs.has(id)) errors.push(`Manifest output '${id}' has no UI presentation.`);
    if (dynamicControls) {
      if (dynamicControls.schema_version !== SUPPORTED_DYNAMIC_CONTROLS) errors.push(`Unsupported dynamic-control schema: ${dynamicControls.schema_version || 'missing'}.`);
      if (dynamicControls.space_id !== manifest.space_id || dynamicControls.version !== manifest.version) errors.push('Dynamic controls do not match the Space identity/version.');
      if (dynamicControls.execution_authority !== false || dynamicControls.binding_authority !== false || dynamicControls.generated_neo_javascript !== false) errors.push('Dynamic controls must remain declarative presentation/value metadata only.');
    }
    return { valid: errors.length === 0, errors };
  }

  function dynamicSourceValue(predicate, values, context) {
    if (predicate?.source === 'input') return values?.[predicate.ref];
    if (predicate?.source === 'stage_enabled') return (context?.enabledStageIds || []).includes(predicate.ref);
    if (predicate?.source === 'addon_present') return (context?.addonModuleIds || []).includes(predicate.ref);
    if (predicate?.source === 'variant_active') return Boolean(context?.variantActive);
    return null;
  }
  function dynamicPredicateMatches(predicate, actual) {
    const op = predicate?.operator || 'truthy'; const expected = predicate?.value;
    if (op === 'equals') return actual === expected;
    if (op === 'not_equals') return actual !== expected;
    if (op === 'in') return Array.isArray(expected) && expected.includes(actual);
    if (op === 'not_in') return Array.isArray(expected) && !expected.includes(actual);
    if (op === 'truthy') return Boolean(actual);
    if (op === 'falsy') return !Boolean(actual);
    if (op === 'exists') return actual !== null && actual !== undefined && actual !== '';
    if (!['gt','gte','lt','lte'].includes(op) || typeof actual !== 'number' || typeof expected !== 'number') return false;
    if (op === 'gt') return actual > expected; if (op === 'gte') return actual >= expected; if (op === 'lt') return actual < expected; return actual <= expected;
  }
  function dynamicConditionMatches(condition, values, context) {
    const rows = Array.isArray(condition?.predicates) ? condition.predicates : [];
    const results = rows.map((predicate) => dynamicPredicateMatches(predicate, dynamicSourceValue(predicate, values, context)));
    let matched = condition?.mode === 'any' ? results.some(Boolean) : results.every(Boolean);
    if (condition?.negate) matched = !matched;
    return matched;
  }
  function dynamicStates(dynamicControls, values, context = {}) {
    const states = {};
    for (const behavior of dynamicControls?.behaviors || []) {
      const key = `${behavior.target_type}:${behavior.target_id}`;
      const state = states[key] ||= { visible: true, enabled: true };
      const effect = behavior.effect === 'enabled' ? 'enabled' : 'visible';
      state[effect] = Boolean(state[effect] && dynamicConditionMatches(behavior.condition, values, context));
    }
    return states;
  }
  function dynamicTargetState(states, type, id) { return states?.[`${type}:${id}`] || { visible: true, enabled: true }; }
  function dynamicCollectionFor(dynamicControls, inputId) { return (dynamicControls?.collections || []).find((item) => item.input_id === inputId) || null; }

  function initialValues(manifest, supplied = {}) {
    const values = {};
    for (const input of manifest?.inputs || []) {
      values[input.input_id] = Object.prototype.hasOwnProperty.call(supplied || {}, input.input_id)
        ? supplied[input.input_id] : input.default;
    }
    return values;
  }

  function optionValues(input, optionCatalogs, mode = 'preview') {
    const host = optionCatalogs?.[input.input_id];
    const rows = Array.isArray(host)
      ? host
      : (mode === 'runtime' ? [] : (Array.isArray(input.options?.preview) ? input.options.preview : []));
    return rows.map((item) => {
      if (item && typeof item === 'object') return { value: item.value ?? item.id ?? item.name ?? '', label: item.label ?? item.name ?? item.value ?? item.id ?? '' };
      return { value: item, label: item };
    }).filter((item) => String(item.value ?? '').length > 0);
  }
  function catalogState(input, options = {}) {
    const state = options.catalogStates?.[input.input_id];
    return state && typeof state === 'object' ? state : {};
  }
  function catalogExtraHelp(input, value, rows, options = {}) {
    if ((options.mode || 'preview') !== 'runtime') {
      const unresolved = input.options?.truncated || (input.options?.source === 'runtime_catalog' && !Array.isArray(options.optionCatalogs?.[input.input_id]));
      return unresolved ? 'Full catalog resolves from the selected runtime.' : '';
    }
    if (options.optionCatalogLoading) return 'Loading choices from the selected ComfyUI runtime…';
    if (options.optionCatalogError) return 'Catalog unavailable for the selected ComfyUI runtime.';
    const state = catalogState(input, options);
    if (!Array.isArray(options.optionCatalogs?.[input.input_id]) || state.status === 'unavailable') {
      return 'Catalog unavailable for the selected ComfyUI runtime.';
    }
    const configured = value === null || value === undefined ? '' : String(value);
    const exactCurrent = !!configured && rows.some((row) => String(row.value) === configured);
    if (configured && !exactCurrent) {
      const isSavedConfigured = String(state.configured_value ?? '') === configured;
      if (isSavedConfigured && state.configured_available === true && state.runtime_resolved_value) {
        return `Runtime resolves the saved value to ${state.runtime_resolved_value}.`;
      }
      return 'Current value is not available in this runtime. Choose a replacement before generating.';
    }
    return `${rows.length} ${rows.length === 1 ? 'option' : 'options'} from the selected ComfyUI runtime.`;
  }
  function runtimeCatalogAvailable(input, options = {}) {
    return (options.mode || 'preview') !== 'runtime' || Array.isArray(options.optionCatalogs?.[input.input_id]);
  }

  function inputEventAttrs(rendererId, inputId, control) {
    const event = ['text_input', 'text_area', 'integer_input', 'number_input', 'slider', 'seed_input', 'json_editor'].includes(control) ? 'oninput' : 'onchange';
    return `data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(inputId)}" data-neo-space-control="${attr(control)}" ${event}="NeoComfySpaceRenderer.handleInput(this)"`;
  }
  function publicHelpText(input, mode) {
    const text = String(input.help_text || input.constraints?.tooltip || '').trim();
    if (mode === 'runtime' && /^node role:/i.test(text)) return '';
    return text;
  }
  function helpHtml(input, extra = '', options = {}) {
    const text = publicHelpText(input, options.mode || 'preview');
    if (!text && !extra) return '';
    return `<small class="neo-comfy-space-help">${esc([text, extra].filter(Boolean).join(' · '))}</small>`;
  }
  function requiredMark(required) { return required ? '<span class="neo-comfy-space-required" aria-label="required">*</span>' : ''; }
  // A normal Comfy widget can carry structured data. Never stringify it into
  // '[object Object]' or send a replacement string through its typed binding.
  function structuredFallback(control, value) {
    return !ASSET_CONTROLS.has(control) && control !== 'json_editor' && value !== null &&
      typeof value === 'object';
  }
  function sourceSectionMeta(section) {
    const title = String(section?.title || 'Settings');
    const match = title.match(/^(.*?)\s*·\s*(.+?)\s+#([^#]+)$/);
    if (!match) return { title, detail: '', nodeId: '' };
    return { title: match[1].trim() || match[2].trim(), detail: match[2].trim(), nodeId: match[3].trim() };
  }


  function renderInput(rendererId, input, manifestInput, value, options) {
    const control = input.control;
    const disabledAll = options.disabled === true;
    const assetBlocked = ASSET_CONTROLS.has(control) && options.assetControlsEnabled !== true;
    const dynamicDisabled = options.dynamicDisabled === true;
    const staticDisabled = disabledAll || assetBlocked;
    const disabled = staticDisabled || dynamicDisabled;
    const disabledAttr = disabled ? ` disabled aria-disabled="true"${dynamicDisabled && !staticDisabled ? ' data-neo-dynamic-disabled="true"' : ''}` : '';
    const required = manifestInput?.required !== false;
    const isStructured = structuredFallback(control, value);
    const common = inputEventAttrs(rendererId, input.input_id, isStructured ? 'json_editor' : control);
    const friendlyLabel = input.label === 'Value' && control === 'text_area' ? 'Text content' : (input.label || input.input_id);
    const label = `<span class="neo-comfy-space-label">${esc(friendlyLabel)}${requiredMark(required)}</span>`;
    const constraints = input.constraints || {};
    const min = constraints.minimum ?? '';
    const max = constraints.maximum ?? '';
    const step = constraints.step ?? (control === 'integer_input' || control === 'seed_input' ? 1 : 'any');
    const stringValue = value === null || value === undefined ? '' : String(value);
    const advancedClass = input.advanced ? ' advanced-control' : '';
    const visibleHelp = publicHelpText(input, options.mode || 'preview');
    const runtimePlaceholder = options.mode === 'runtime' ? `Enter ${String(input.label || input.input_id).toLowerCase()}…` : visibleHelp;
    let body = '';

    if (isStructured) {
      body = `<textarea rows="7" spellcheck="false" class="neo-comfy-space-json neo-comfy-structured-fallback" ${common}${disabledAttr} aria-label="${attr(friendlyLabel)} · structured JSON">${esc(safeJson(value))}</textarea>`;
      body += '<small class="neo-comfy-space-structured-note">Structured value from ComfyUI. Keep valid JSON to preserve its original type. Review this binding in the Space Builder if a text prompt was expected.</small>';
    } else if (control === 'text_area') {
      body = `<textarea rows="4" ${common}${disabledAttr} placeholder="${attr(runtimePlaceholder)}">${esc(stringValue)}</textarea>`;
    } else if (control === 'json_editor') {
      body = `<textarea rows="6" spellcheck="false" class="neo-comfy-space-json" ${common}${disabledAttr}>${esc(value == null ? '' : safeJson(value))}</textarea>`;
    } else if (control === 'toggle') {
      body = `<label class="neo-comfy-space-toggle"><input type="checkbox" ${common}${value === true ? ' checked' : ''}${disabledAttr}><span>${esc(input.label || input.input_id)}</span>${requiredMark(required)}</label>`;
      return `<div class="neo-comfy-space-field neo-comfy-space-toggle-field${advancedClass}" data-neo-space-advanced="${input.advanced ? 'true' : 'false'}">${body}${helpHtml(input, '', options)}</div>`;
    } else if (control === 'select' || control === 'search_select' || control === 'model_select') {
      const opts = optionValues(input, options.optionCatalogs || {}, options.mode || 'preview');
      const catalogAvailable = runtimeCatalogAvailable(input, options);
      const catalogBlocked = (options.mode === 'runtime') && (!catalogAvailable || options.optionCatalogLoading || options.optionCatalogError);
      const catalogDisabled = disabled || catalogBlocked;
      const catalogDynamicOnly = dynamicDisabled && !staticDisabled && !catalogBlocked;
      const catalogDisabledAttr = catalogDisabled ? ` disabled aria-disabled="true"${catalogDynamicOnly ? ' data-neo-dynamic-disabled="true"' : ''}` : '';
      const extraHelp = catalogExtraHelp(input, value, opts, options);
      const hasCurrent = opts.some((row) => String(row.value) === stringValue);
      const stateForInput = catalogState(input, options);
      const staleCurrent = !!stringValue && options.mode === 'runtime' && catalogAvailable && !hasCurrent && stateForInput.configured_available !== true;
      if (control === 'select') {
        const preserved = (!!stringValue && !hasCurrent) ? `<option value="${attr(stringValue)}" selected>${esc(stringValue)}${catalogAvailable ? ' · unavailable' : ' · current value'}</option>` : '';
        const selectOptions = [`<option value="">Select…</option>`, preserved].concat(opts.map((row) => `<option value="${attr(row.value)}" ${String(row.value) === stringValue ? 'selected' : ''}>${esc(row.label)}</option>`)).join('');
        body = `<select ${common}${catalogDisabledAttr}>${selectOptions}</select>`;
      } else if ((options.mode || 'preview') === 'runtime') {
        const pickerId = `${rendererId}-${input.input_id}-catalog`.replace(/[^A-Za-z0-9_.:-]/g, '-');
        const pickerClass = `neo-comfy-space-catalog-picker${control === 'model_select' ? ' model-catalog' : ''}${staleCurrent ? ' stale' : ''}`;
        const buttonLabel = control === 'model_select' ? 'Open model catalog' : 'Open choices';
        body = `<div class="${pickerClass}" id="${attr(pickerId)}" data-neo-space-catalog-input="${attr(input.input_id)}">
          <div class="neo-comfy-space-catalog-control">
            <input type="search" class="neo-comfy-space-catalog-search" value="${attr(stringValue)}" placeholder="${attr(catalogBlocked ? 'Catalog unavailable' : 'Search choices…')}" data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" data-neo-space-control="${attr(control)}" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${attr(pickerId)}-menu"${catalogDisabledAttr} onfocus="NeoComfySpaceRenderer.openCatalog(this)" oninput="NeoComfySpaceRenderer.searchCatalog(this)" onkeydown="NeoComfySpaceRenderer.catalogKeydown(event,this)" onblur="NeoComfySpaceRenderer.deferCloseCatalog(this)">
            <button type="button" class="neo-comfy-space-catalog-toggle" title="${attr(buttonLabel)}" aria-label="${attr(buttonLabel)}" data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}"${catalogDisabledAttr} onclick="NeoComfySpaceRenderer.toggleCatalog(this)">⌄</button>
          </div>
          <div class="neo-comfy-space-catalog-menu" id="${attr(pickerId)}-menu" role="listbox" hidden><div class="neo-comfy-space-catalog-status">${esc(catalogBlocked ? 'Catalog unavailable.' : `${opts.length} choices available`)}</div></div>
        </div>`;
      } else {
        const listId = `${rendererId}-${input.input_id}-options`.replace(/[^A-Za-z0-9_.:-]/g, '-');
        body = `<input type="text" list="${attr(listId)}" ${common}${disabledAttr} value="${attr(stringValue)}" placeholder="Search…"><datalist id="${attr(listId)}">${opts.map((row) => `<option value="${attr(row.value)}">${esc(row.label)}</option>`).join('')}</datalist>`;
      }
      return `<label class="neo-comfy-space-field${advancedClass}" data-neo-space-advanced="${input.advanced ? 'true' : 'false'}">${label}${body}${helpHtml(input, extraHelp, options)}</label>`;
    } else if (control === 'slider') {
      const sliderMin = min === '' ? 0 : min; const sliderMax = max === '' ? 100 : max; const sliderStep = step === 'any' ? 'any' : step;
      body = `<div class="neo-comfy-space-slider"><input type="range" ${common}${disabledAttr} min="${attr(sliderMin)}" max="${attr(sliderMax)}" step="${attr(sliderStep)}" value="${attr(stringValue || sliderMin)}"><output>${esc(stringValue || sliderMin)}</output></div>`;
    } else if (control === 'seed_input') {
      body = `<div class="neo-comfy-space-seed"><input type="number" ${common}${disabledAttr} step="1" value="${attr(stringValue)}"><button type="button" class="neo-btn compact" ${disabled ? `disabled aria-disabled="true"${dynamicDisabled && !staticDisabled ? ' data-neo-dynamic-disabled="true"' : ''}` : ''} data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" onclick="NeoComfySpaceRenderer.randomizeSeed(this)">Random</button></div>`;
    } else if (control === 'integer_input' || control === 'number_input') {
      body = `<input type="number" ${common}${disabledAttr}${min !== '' ? ` min="${attr(min)}"` : ''}${max !== '' ? ` max="${attr(max)}"` : ''} step="${attr(step)}" value="${attr(stringValue)}">`;
    } else if (ASSET_CONTROLS.has(control)) {
      const accept = (input.accepted_mime_types || []).join(',') || ({ image_upload: 'image/*', mask_upload: 'image/*', video_upload: 'video/*', audio_upload: 'audio/*' }[control] || '*/*');
      const kind = ({ image_upload: 'image', mask_upload: 'mask', video_upload: 'video', audio_upload: 'audio', file_upload: 'file' }[control] || 'file');
      const selected = Array.isArray(value) ? value : [];
      const selectedSummary = selected.length ? (selected.length === 1 ? selected[0]?.name || '1 file selected' : `${selected.length} files selected`) : `Drop ${kind} here or browse`;
      body = `<span class="neo-comfy-space-upload"><input class="neo-comfy-space-upload-input" type="file" ${common}${disabledAttr} accept="${attr(accept)}"${manifestInput?.multiplicity === 'multiple' ? ' multiple' : ''}><span class="neo-comfy-space-upload-face" aria-hidden="true"><span class="neo-comfy-space-upload-icon">↑</span><span class="neo-comfy-space-upload-copy"><strong>${esc(selectedSummary)}</strong><span data-neo-space-file-summary>${selected.length ? 'Choose another file to replace it.' : `${esc(kind.toUpperCase())} input`}</span></span></span></span>`;
      return `<label class="neo-comfy-space-field neo-comfy-space-asset-field${advancedClass}" data-neo-space-advanced="${input.advanced ? 'true' : 'false'}">${label}${body}${helpHtml(input, assetBlocked ? 'Media transport is not enabled in this preview.' : '', options)}</label>`;
    } else {
      body = `<input type="text" ${common}${disabledAttr} value="${attr(stringValue)}" placeholder="${attr(runtimePlaceholder)}">`;
    }
    return `<label class="neo-comfy-space-field${advancedClass}" data-neo-space-advanced="${input.advanced ? 'true' : 'false'}">${label}${body}${helpHtml(input, '', options)}</label>`;
  }

  function renderRepeater(rendererId, input, manifestInput, value, collection, options = {}) {
    const staticDisabled = options.disabled === true;
    const dynamicDisabled = options.dynamicDisabled === true;
    const disabled = staticDisabled || dynamicDisabled;
    const dynamicAttr = (otherBlocked = false) => dynamicDisabled && !staticDisabled && !otherBlocked ? ' data-neo-dynamic-disabled="true"' : '';
    const items = Array.isArray(value) ? value : (value === null || value === undefined ? [] : [value]);
    const minItems = Number(collection?.min_items ?? 0); const maxItems = Number(collection?.max_items ?? 8);
    while (items.length < minItems) items.push(null);
    const rows = items.map((item, index) => {
      const encoded = safeJson(item);
      const reorderBlocked = collection?.allow_reorder === false;
      const upBlocked = staticDisabled || index === 0 || reorderBlocked;
      const downBlocked = staticDisabled || index >= items.length - 1 || reorderBlocked;
      const removeBlocked = staticDisabled || items.length <= minItems;
      const textareaDisabledAttr = disabled ? `disabled aria-disabled="true"${dynamicAttr()}` : '';
      const upDisabledAttr = (dynamicDisabled || upBlocked) ? `disabled aria-disabled="true"${dynamicAttr(upBlocked)}` : '';
      const downDisabledAttr = (dynamicDisabled || downBlocked) ? `disabled aria-disabled="true"${dynamicAttr(downBlocked)}` : '';
      const removeDisabledAttr = (dynamicDisabled || removeBlocked) ? `disabled aria-disabled="true"${dynamicAttr(removeBlocked)}` : '';
      return `<div class="neo-comfy-space-repeater-row"><label><span>${esc(collection?.item_label || 'Item')} ${index + 1}</span><textarea data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" data-neo-space-repeater-index="${index}" ${textareaDisabledAttr} oninput="NeoComfySpaceRenderer.handleRepeaterItem(this)">${esc(encoded)}</textarea></label><div class="neo-comfy-space-repeater-actions"><button type="button" class="neo-btn compact" ${upDisabledAttr} onclick="NeoComfySpaceRenderer.moveRepeaterItem(this,-1)" data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" data-neo-space-repeater-index="${index}">↑</button><button type="button" class="neo-btn compact" ${downDisabledAttr} onclick="NeoComfySpaceRenderer.moveRepeaterItem(this,1)" data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" data-neo-space-repeater-index="${index}">↓</button><button type="button" class="neo-btn danger compact" ${removeDisabledAttr} onclick="NeoComfySpaceRenderer.removeRepeaterItem(this)" data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" data-neo-space-repeater-index="${index}">Remove</button></div></div>`;
    }).join('');
    const addOtherBlocked = staticDisabled || items.length >= maxItems;
    const addDisabledAttr = (dynamicDisabled || addOtherBlocked) ? `disabled aria-disabled="true"${dynamicAttr(addOtherBlocked)}` : '';
    return `<div class="neo-comfy-space-field neo-comfy-space-repeater"><span class="neo-comfy-space-label">${esc(collection?.label || input.label || input.input_id)}</span>${rows || '<div class="neo-muted">No items yet.</div>'}<button type="button" class="neo-btn compact" ${addDisabledAttr} data-neo-space-renderer-id="${attr(rendererId)}" data-neo-space-input-id="${attr(input.input_id)}" onclick="NeoComfySpaceRenderer.addRepeaterItem(this)">Add ${esc(collection?.item_label || 'item')}</button>${helpHtml(input, `Repeatable JSON values · ${minItems}-${maxItems} items`, options)}</div>`;
  }

  function resultItems(value, collection) {
    if (collection === 'all' && Array.isArray(value)) return value;
    if (Array.isArray(value) && value.length === 1) return value;
    return [value];
  }
  function emptyResultHtml(output, mode) {
    if (mode !== 'runtime') return `<div class="neo-comfy-space-result-placeholder">${esc(output.previewable ? 'Awaiting preview' : 'Awaiting result')}</div>`;
    return `<div class="neo-comfy-space-result-placeholder neo-comfy-space-result-empty"><span class="neo-comfy-space-result-empty-icon">◇</span><strong>No result yet</strong><span>${esc(output.previewable ? 'Generate to see the output.' : 'Run the Space to produce this result.')}</span></div>`;
  }
  function resultActions(url, output) {
    if (!url) return '';
    const download = output.downloadable ? `<a class="neo-btn compact" href="${attr(url)}" download>Download</a>` : '';
    return `<div class="neo-comfy-space-result-actions"><a class="neo-btn compact" href="${attr(url)}" target="_blank" rel="noopener noreferrer">Open</a>${download}</div>`;
  }
  function renderOneResult(renderer, value, output, options = {}) {
    const missing = value === undefined || value === null || value === '';
    if (missing) return emptyResultHtml(output, options.mode || 'preview');
    if (renderer === 'text_viewer') return `<pre class="neo-comfy-space-text-result">${esc(String(value))}</pre>`;
    if (renderer === 'numeric_value') return `<div class="neo-comfy-space-scalar-result">${esc(value)}</div>`;
    if (renderer === 'boolean_value') return `<div class="neo-comfy-space-scalar-result">${value ? 'True' : 'False'}</div>`;
    if (renderer === 'choice_value') return `<div class="neo-comfy-space-scalar-result">${esc(labelFor(value))}</div>`;
    if (renderer === 'json_viewer') return `<pre class="neo-comfy-space-json-result">${esc(safeJson(value))}</pre>`;
    const url = safeUrl(value);
    if (renderer === 'image_preview' || renderer === 'mask_preview') return url ? `<div class="neo-comfy-space-media-result"><img class="neo-comfy-space-image-result" src="${attr(url)}" alt="${attr(output.label || 'Generated image')}" loading="lazy">${resultActions(url, output)}</div>` : `<div class="neo-comfy-space-result-placeholder">Image result unavailable</div>`;
    if (renderer === 'video_player') return url ? `<div class="neo-comfy-space-media-result"><video class="neo-comfy-space-video-result" controls preload="metadata" src="${attr(url)}"></video>${resultActions(url, output)}</div>` : `<div class="neo-comfy-space-result-placeholder">Video result unavailable</div>`;
    if (renderer === 'audio_player') return url ? `<div class="neo-comfy-space-media-result"><audio class="neo-comfy-space-audio-result" controls preload="metadata" src="${attr(url)}"></audio>${resultActions(url, output)}</div>` : `<div class="neo-comfy-space-result-placeholder">Audio result unavailable</div>`;
    if (renderer === 'file_download') return url ? `<div class="neo-comfy-space-file-result"><strong>${esc(labelFor(value) || output.label || 'Generated file')}</strong>${resultActions(url, {...output, downloadable:true})}</div>` : `<div class="neo-comfy-space-result-placeholder">File result unavailable</div>`;
    return `<pre>${esc(safeJson(value))}</pre>`;
  }
  function renderResult(output, manifestOutput, value, options = {}) {
    const values = resultItems(value, manifestOutput?.collection || 'first');
    const primary = manifestOutput?.primary ? ' primary' : '';
    return `<article class="neo-comfy-space-result${primary}" data-output-id="${attr(output.output_id)}"><header><strong>${esc(output.label || output.output_id)}</strong><span>${esc(output.renderer.replaceAll('_', ' '))}</span></header><div class="neo-comfy-space-result-body">${values.map((item) => renderOneResult(output.renderer, item, output, options)).join('')}</div></article>`;
  }

  function render(config = {}) {
    const manifest = config.manifest || {};
    const ui = config.ui || {};
    const dynamicControls = config.dynamicControls || null;
    const dynamicContext = config.dynamicContext || {};
    const validation = validate(manifest, ui, dynamicControls);
    if (!validation.valid) {
      return `<section class="neo-comfy-space-renderer invalid"><div class="neo-inline-status error"><strong>Space preview blocked.</strong><ul>${validation.errors.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></div></section>`;
    }
    const rendererId = String(config.rendererId || `space-${manifest.space_id || 'renderer'}`).replace(/[^A-Za-z0-9_.:-]/g, '-');
    const values = initialValues(manifest, config.values || {});
    for (const collection of dynamicControls?.collections || []) {
      if (collection.mode !== 'repeater') continue;
      let rows = Array.isArray(values[collection.input_id]) ? [...values[collection.input_id]] : (values[collection.input_id] === null || values[collection.input_id] === undefined ? [] : [values[collection.input_id]]);
      const minimum = Number(collection.min_items ?? 0); while (rows.length < minimum) rows.push(null); values[collection.input_id] = rows;
    }
    const previousInstance = instances.get(rendererId);
    instances.set(rendererId, {
      rendererId, manifest, ui, values: { ...values }, validationErrors: {},
      bridge: config.bridge || null,
      mode: config.mode || 'preview',
      assetFiles: previousInstance?.assetFiles || {},
      optionCatalogs: config.optionCatalogs || {},
      catalogStates: config.catalogStates || {},
      optionCatalogError: config.optionCatalogError || '',
      optionCatalogLoading: config.optionCatalogLoading === true,
      dynamicControls, dynamicContext, baseConfig: { ...config },
    });

    const manifestInputs = new Map((manifest.inputs || []).map((item) => [item.input_id, item]));
    const manifestOutputs = new Map((manifest.outputs || []).map((item) => [item.output_id, item]));
    const bySection = {};
    for (const input of ordered(ui.inputs || [])) (bySection[input.section_id] ||= []).push(input);
    const states = dynamicStates(dynamicControls, values, dynamicContext);
    const hasAdvanced = (ui.layout?.sections || []).some((section) => section.advanced) || (ui.inputs || []).some((input) => input.advanced);
    const displayedSections = ordered(ui.layout?.sections || []);
    const repeatedTitles = new Map();
    for (const section of displayedSections) {
      const meta = sourceSectionMeta(section);
      if (meta.nodeId) repeatedTitles.set(meta.title.toLowerCase(), (repeatedTitles.get(meta.title.toLowerCase()) || 0) + 1);
    }
    const sections = displayedSections.map((section) => {
      const sectionState = dynamicTargetState(states, 'section', section.section_id);
      const groupedNodeIds = new Set((bySection[section.section_id] || []).map((row) => String(row.source_node_id || '')).filter(Boolean));
      let previousNodeId = '';
      const controls = (bySection[section.section_id] || []).map((input) => {
        const inputState = dynamicTargetState(states, 'input', input.input_id);
        const dynamicDisabled = !sectionState.enabled || !inputState.enabled;
        const collection = dynamicCollectionFor(dynamicControls, input.input_id);
        const inputOptions = { ...config, dynamicDisabled };
        const rendered = collection?.mode === 'repeater'
          ? renderRepeater(rendererId, input, manifestInputs.get(input.input_id), values[input.input_id], collection, inputOptions)
          : renderInput(rendererId, input, manifestInputs.get(input.input_id), values[input.input_id], inputOptions);
        const sourceNodeId = String(input.source_node_id || '');
        const showNodeCaption = !section.section_id.startsWith('node_') && Boolean(sourceNodeId) && groupedNodeIds.size > 1 && previousNodeId !== sourceNodeId;
        previousNodeId = sourceNodeId;
        const caption = showNodeCaption ? `<div class="neo-comfy-space-node-break"><span>${esc(input.source_node_label || 'Workflow node')}</span><small>Node #${esc(sourceNodeId)}</small></div>` : '';
        return `${caption}<div class="neo-comfy-space-dynamic-target" data-neo-dynamic-target-type="input" data-neo-dynamic-target-id="${attr(input.input_id)}" ${inputState.visible ? '' : 'hidden'}>${rendered}</div>`;
      }).join('');
      if (!controls) return '';
      const classes = `neo-comfy-space-section${section.advanced ? ' advanced-section' : ''}`;
      const dynamicAttrs = `data-neo-dynamic-target-type="section" data-neo-dynamic-target-id="${attr(section.section_id)}" ${sectionState.visible ? '' : 'hidden'}`;
      const meta = sourceSectionMeta(section);
      const repeated = meta.nodeId && (repeatedTitles.get(meta.title.toLowerCase()) || 0) > 1;
      const sectionBadges = meta.nodeId ? `<span class="neo-comfy-space-node-meta">${esc(meta.detail)} · Node #${esc(meta.nodeId)}</span>` : '';
      const explanation = repeated ? `<small class="neo-comfy-space-node-note">Separate workflow node · this control is independent of other “${esc(meta.title)}” nodes.</small>` : '';
      const description = section.description ? `<p class="neo-comfy-space-section-description">${esc(section.description)}</p>` : '';
      const heading = `<span class="neo-comfy-space-section-title">${esc(meta.title)}</span>${sectionBadges}`;
      const inner = `<div class="neo-comfy-space-section-head"><div><h4>${esc(meta.title)}</h4>${sectionBadges}${description}${explanation}</div></div><div class="neo-comfy-space-fields">${controls}</div>`;
      if (section.collapsible) return `<details class="${classes}" ${dynamicAttrs} data-neo-space-advanced="${section.advanced ? 'true' : 'false'}" ${section.default_collapsed ? '' : 'open'}><summary>${heading}</summary>${description}${explanation}<div class="neo-comfy-space-fields">${controls}</div></details>`;
      return `<section class="${classes}" ${dynamicAttrs} data-neo-space-advanced="${section.advanced ? 'true' : 'false'}">${inner}</section>`;
    }).join('');

    const outputs = ordered(ui.outputs || []).map((output) => renderResult(output, manifestOutputs.get(output.output_id), config.results?.[output.output_id], config)).join('');
    const submit = config.submit || {};
    const submitVisible = submit.visible !== false;
    const submitEnabled = submit.enabled === true && !!config.bridge && config.disabled !== true;
    const showHeader = config.showHeader !== false;
    const layoutMode = String(config.layoutMode || 'default').replace(/[^A-Za-z0-9_-]/g, '-');
    const advancedToggle = hasAdvanced ? `<label class="neo-comfy-space-advanced-toggle"><input type="checkbox" data-neo-space-renderer-id="${attr(rendererId)}" onchange="NeoComfySpaceRenderer.toggleAdvanced(this)"> Show advanced controls</label>` : '';
    const modeBadge = config.mode === 'preview' ? '<span class="neo-comfy-space-mode-badge">Preview</span>' : '';
    const header = showHeader ? `<header class="neo-comfy-space-header"><div><span class="neo-kicker">Neo Comfy Space</span><h3>${esc(manifest.name || ui.space_id)}</h3>${manifest.description ? `<p>${esc(manifest.description)}</p>` : ''}</div>${modeBadge}</header>` : '';
    const submitRow = submitVisible ? `<div class="neo-comfy-space-submit-row"><button type="submit" class="neo-btn primary" ${submitEnabled ? '' : 'disabled aria-disabled="true"'}>${esc(ui.layout?.submit_label || 'Create')}</button>${config.mode === 'preview' ? '<small>Preview changes control state only. No workflow is executed.</small>' : ''}</div>` : '';
    return `<section class="neo-comfy-space-renderer" id="${attr(rendererId)}" data-neo-space-renderer-id="${attr(rendererId)}" data-advanced-visible="false" data-layout-mode="${attr(layoutMode)}" data-render-mode="${attr(config.mode || 'preview')}" data-has-advanced="${hasAdvanced ? 'true' : 'false'}">${header}${advancedToggle}<form class="neo-comfy-space-form" onsubmit="return NeoComfySpaceRenderer.handleSubmit(event,this)" novalidate>${sections}${submitRow}</form><section class="neo-comfy-space-results"><div class="neo-comfy-space-results-head"><div><span class="neo-kicker">RESULTS</span><h4>${esc(ui.layout?.results_title || 'Results')}</h4></div></div><div class="neo-comfy-space-result-grid">${outputs || '<div class="neo-comfy-space-result-placeholder">No outputs declared.</div>'}</div></section></section>`;
  }

  function valueFromElement(element, control) {
    if (control === 'toggle') return !!element.checked;
    if (control === 'integer_input' || control === 'seed_input') return element.value === '' ? null : Number.parseInt(element.value, 10);
    if (control === 'number_input' || control === 'slider') return element.value === '' ? null : Number.parseFloat(element.value);
    if (control === 'json_editor') {
      if (!element.value.trim()) return null;
      return JSON.parse(element.value);
    }
    if (ASSET_CONTROLS.has(control)) {
      return Array.from(element.files || []).map((file) => ({ name: file.name, type: file.type, size: file.size, last_modified: file.lastModified }));
    }
    return element.value;
  }
  function dispatchBridge(instance, action, payload) {
    const bridge = instance?.bridge;
    const resolvedAction = action || bridge?.action || '';
    if (!bridge || !bridge.surfaceId || !resolvedAction) return undefined;
    if (typeof globalThis.neoInvokeSurfaceModule !== 'function') return undefined;
    return globalThis.neoInvokeSurfaceModule(bridge.surfaceId, resolvedAction, payload || {});
  }
  function applyDynamicState(rendererId) {
    const instance = instances.get(String(rendererId || ''));
    const root = typeof document !== 'undefined' ? document.getElementById(rendererId) : null;
    if (!instance || !root || !instance.dynamicControls) return;
    const states = dynamicStates(instance.dynamicControls, instance.values, instance.dynamicContext || {});
    for (const target of root.querySelectorAll('[data-neo-dynamic-target-type][data-neo-dynamic-target-id]')) {
      const type = target.dataset.neoDynamicTargetType || ''; const id = target.dataset.neoDynamicTargetId || '';
      const state = dynamicTargetState(states, type, id); target.hidden = !state.visible;
      let effectiveEnabled = state.enabled;
      if (type === 'input') {
        const parent = target.closest('[data-neo-dynamic-target-type="section"]');
        if (parent) effectiveEnabled = effectiveEnabled && dynamicTargetState(states, 'section', parent.dataset.neoDynamicTargetId || '').enabled;
      }
      for (const control of target.querySelectorAll('input,select,textarea,button')) {
        if (!effectiveEnabled) { if (!control.disabled) { control.disabled = true; control.dataset.neoDynamicDisabled = 'true'; } }
        else if (control.dataset.neoDynamicDisabled === 'true') { control.disabled = false; delete control.dataset.neoDynamicDisabled; }
      }
    }
  }
  function handleInput(element) {
    const rendererId = element?.dataset?.neoSpaceRendererId || '';
    const inputId = element?.dataset?.neoSpaceInputId || '';
    const control = element?.dataset?.neoSpaceControl || '';
    const instance = instances.get(rendererId);
    if (!instance || !inputId) return;
    let value;
    try {
      value = valueFromElement(element, control);
      if (element.classList?.contains?.('neo-comfy-structured-fallback')) {
        const previous = instance.values[inputId];
        if (value === null || typeof value !== 'object' ||
            Array.isArray(value) !== Array.isArray(previous)) {
          throw new Error('Structured input must remain a JSON object or array of its original type.');
        }
      }
      if (instance.validationErrors) delete instance.validationErrors[inputId];
      if (typeof element.setCustomValidity === 'function') element.setCustomValidity('');
      element.removeAttribute?.('aria-invalid');
      const error = element.parentElement?.querySelector?.('.neo-comfy-space-json-error');
      if (error) error.remove();
    } catch (_) {
      // Do not overwrite the last valid value or dispatch malformed JSON to Neo.
      (instance.validationErrors ||= {})[inputId] = 'Invalid JSON or structured value type.';
      if (typeof element.setCustomValidity === 'function') element.setCustomValidity('Enter valid JSON before continuing.');
      element.setAttribute?.('aria-invalid', 'true');
      const wrapper = element.parentElement;
      if (wrapper && !wrapper.querySelector?.('.neo-comfy-space-json-error')) {
        wrapper.insertAdjacentHTML?.('beforeend', '<small class="neo-comfy-space-json-error" role="alert">Invalid JSON. The previous valid value is preserved.</small>');
      }
      return;
    }
    if (ASSET_CONTROLS.has(control)) {
      instance.assetFiles[inputId] = Array.from(element.files || []);
      const shell = element.closest?.('.neo-comfy-space-upload');
      const title = shell?.querySelector?.('.neo-comfy-space-upload-copy strong');
      const summary = shell?.querySelector?.('[data-neo-space-file-summary]');
      const files = instance.assetFiles[inputId];
      if (title) title.textContent = files.length ? (files.length === 1 ? files[0].name : `${files.length} files selected`) : 'Drop file here or browse';
      if (summary) summary.textContent = files.length ? (files.length === 1 ? `${Math.max(1, Math.round(files[0].size / 1024))} KB` : 'Ready to upload when you generate.') : 'No file selected';
    }
    instance.values[inputId] = value;
    if (control === 'slider') {
      const output = element.parentElement?.querySelector('output');
      if (output) output.textContent = String(value ?? '');
    }
    dispatchBridge(instance, instance.bridge?.inputAction || instance.bridge?.action, { rendererId, inputId, control, value });
    applyDynamicState(rendererId);
  }

  function repeaterPayload(element) {
    const rendererId = element?.dataset?.neoSpaceRendererId || ''; const inputId = element?.dataset?.neoSpaceInputId || '';
    const index = Number(element?.dataset?.neoSpaceRepeaterIndex ?? -1); return { rendererId, inputId, index, instance: instances.get(rendererId) };
  }
  function dispatchRepeater(instance, rendererId, inputId) {
    dispatchBridge(instance, instance?.bridge?.inputAction || instance?.bridge?.action, { rendererId, inputId, control: 'json_editor', value: [...(instance?.values?.[inputId] || [])] });
    applyDynamicState(rendererId);
  }
  function handleRepeaterItem(element) {
    const { rendererId, inputId, index, instance } = repeaterPayload(element); if (!instance || !inputId || index < 0) return;
    const rows = Array.isArray(instance.values[inputId]) ? [...instance.values[inputId]] : []; let value = element.value; try { value = JSON.parse(value); } catch (_) {}
    rows[index] = value; instance.values[inputId] = rows; dispatchRepeater(instance, rendererId, inputId);
  }
  function addRepeaterItem(button) { const { rendererId, inputId, instance } = repeaterPayload(button); if (!instance || !inputId) return; const collection = dynamicCollectionFor(instance.dynamicControls, inputId); const rows = Array.isArray(instance.values[inputId]) ? [...instance.values[inputId]] : []; if (rows.length >= Number(collection?.max_items ?? 8)) return; rows.push(null); instance.values[inputId] = rows; dispatchRepeater(instance, rendererId, inputId); rerenderInstance(rendererId); }
  function removeRepeaterItem(button) { const { rendererId, inputId, index, instance } = repeaterPayload(button); if (!instance || index < 0) return; const collection = dynamicCollectionFor(instance.dynamicControls, inputId); const rows = Array.isArray(instance.values[inputId]) ? [...instance.values[inputId]] : []; if (rows.length <= Number(collection?.min_items ?? 0)) return; rows.splice(index,1); instance.values[inputId] = rows; dispatchRepeater(instance, rendererId, inputId); rerenderInstance(rendererId); }
  function moveRepeaterItem(button, delta) { const { rendererId, inputId, index, instance } = repeaterPayload(button); if (!instance || index < 0) return; const rows = Array.isArray(instance.values[inputId]) ? [...instance.values[inputId]] : []; const next = index + Number(delta || 0); if (next < 0 || next >= rows.length) return; [rows[index], rows[next]] = [rows[next], rows[index]]; instance.values[inputId] = rows; dispatchRepeater(instance, rendererId, inputId); rerenderInstance(rendererId); }
  function rerenderInstance(rendererId) {
    const instance = instances.get(rendererId); const root = typeof document !== 'undefined' ? document.getElementById(rendererId) : null; if (!instance || !root) return;
    const html = render({ ...(instance.baseConfig || {}), rendererId, values: instance.values, dynamicControls: instance.dynamicControls, dynamicContext: instance.dynamicContext });
    const holder = document.createElement('div'); holder.innerHTML = html; const next = holder.firstElementChild; if (next) root.replaceWith(next);
  }
  function catalogMatches(rendererId, inputId, query = '', limit = 120) {
    const instance = instances.get(String(rendererId || ''));
    const raw = instance?.optionCatalogs?.[inputId];
    if (!Array.isArray(raw)) return [];
    const rows = raw.map((item) => {
      if (item && typeof item === 'object') return { value: item.value ?? item.id ?? item.name ?? '', label: item.label ?? item.name ?? item.value ?? item.id ?? '' };
      return { value: item, label: item };
    }).filter((item) => String(item.value ?? '').length > 0);
    const q = String(query || '').trim().toLowerCase();
    const filtered = q ? rows.filter((row) => `${row.label} ${row.value}`.toLowerCase().includes(q)) : rows;
    const sorted = q ? [...filtered].sort((a, b) => {
      const av = String(a.label || a.value).toLowerCase();
      const bv = String(b.label || b.value).toLowerCase();
      const ap = av.startsWith(q) ? 0 : 1;
      const bp = bv.startsWith(q) ? 0 : 1;
      return ap - bp || av.localeCompare(bv);
    }) : filtered;
    return sorted.slice(0, Math.max(1, Number(limit) || 120));
  }
  function catalogPickerFrom(element) { return element?.closest?.('.neo-comfy-space-catalog-picker') || null; }
  function renderCatalogMenu(input) {
    const picker = catalogPickerFrom(input);
    const menu = picker?.querySelector?.('.neo-comfy-space-catalog-menu');
    const rendererId = input?.dataset?.neoSpaceRendererId || '';
    const inputId = input?.dataset?.neoSpaceInputId || '';
    const instance = instances.get(rendererId);
    if (!menu || !instance || !inputId) return;
    const raw = instance.optionCatalogs?.[inputId];
    if (!Array.isArray(raw)) {
      menu.innerHTML = '<div class="neo-comfy-space-catalog-status">Catalog unavailable for the selected runtime.</div>';
      return;
    }
    const selected = String(instance.values?.[inputId] ?? '');
    const typed = String(input.value || '').trim();
    const query = typed === selected ? '' : typed;
    const rows = catalogMatches(rendererId, inputId, query, 120);
    if (!rows.length) {
      menu.innerHTML = `<div class="neo-comfy-space-catalog-status">${esc(query ? 'No matching choices.' : 'No choices available.')}</div>`;
      return;
    }
    const totalMatches = query
      ? raw.filter((item) => {
          const value = item && typeof item === 'object' ? (item.value ?? item.id ?? item.name ?? '') : item;
          const label = item && typeof item === 'object' ? (item.label ?? item.name ?? item.value ?? item.id ?? '') : item;
          return `${label} ${value}`.toLowerCase().includes(query.toLowerCase());
        }).length
      : raw.length;
    const buttons = rows.map((row) => {
      const isSelected = String(row.value) === selected;
      return `<button type="button" class="neo-comfy-space-catalog-option${isSelected ? ' selected' : ''}" role="option" aria-selected="${isSelected ? 'true' : 'false'}" data-neo-space-catalog-value="${attr(row.value)}" onmousedown="event.preventDefault()" onclick="NeoComfySpaceRenderer.chooseCatalog(this)"><span>${esc(row.label)}</span>${isSelected ? '<small>Selected</small>' : ''}</button>`;
    }).join('');
    const footer = totalMatches > rows.length ? `<div class="neo-comfy-space-catalog-footer">Showing ${rows.length} of ${totalMatches}. Keep typing to narrow the catalog.</div>` : `<div class="neo-comfy-space-catalog-footer">${totalMatches} ${totalMatches === 1 ? 'choice' : 'choices'}</div>`;
    menu.innerHTML = `${buttons}${footer}`;
  }
  function openCatalog(input) {
    if (!input || input.disabled) return;
    const picker = catalogPickerFrom(input);
    const menu = picker?.querySelector?.('.neo-comfy-space-catalog-menu');
    if (!menu) return;
    menu.hidden = false;
    input.setAttribute?.('aria-expanded', 'true');
    renderCatalogMenu(input);
  }
  function closeCatalog(input, { restore = true } = {}) {
    const picker = catalogPickerFrom(input);
    const menu = picker?.querySelector?.('.neo-comfy-space-catalog-menu');
    if (menu) menu.hidden = true;
    input?.setAttribute?.('aria-expanded', 'false');
    if (restore) {
      const rendererId = input?.dataset?.neoSpaceRendererId || '';
      const inputId = input?.dataset?.neoSpaceInputId || '';
      const instance = instances.get(rendererId);
      if (input && instance && inputId) input.value = String(instance.values?.[inputId] ?? '');
    }
  }
  function deferCloseCatalog(input) { setTimeout(() => closeCatalog(input), 160); }
  function searchCatalog(input) { openCatalog(input); }
  function toggleCatalog(button) {
    if (!button || button.disabled) return;
    const picker = catalogPickerFrom(button);
    const input = picker?.querySelector?.('.neo-comfy-space-catalog-search');
    const menu = picker?.querySelector?.('.neo-comfy-space-catalog-menu');
    if (!input || !menu) return;
    if (menu.hidden) { input.focus?.(); openCatalog(input); }
    else closeCatalog(input);
  }
  function chooseCatalog(button) {
    const picker = catalogPickerFrom(button);
    const input = picker?.querySelector?.('.neo-comfy-space-catalog-search');
    if (!input) return;
    const rendererId = input.dataset?.neoSpaceRendererId || '';
    const inputId = input.dataset?.neoSpaceInputId || '';
    const control = input.dataset?.neoSpaceControl || '';
    const instance = instances.get(rendererId);
    if (!instance || !inputId) return;
    const value = String(button?.dataset?.neoSpaceCatalogValue ?? '');
    instance.values[inputId] = value;
    input.value = value;
    dispatchBridge(instance, instance.bridge?.inputAction || instance.bridge?.action, { rendererId, inputId, control, value });
    closeCatalog(input, { restore: false });
  }
  function catalogKeydown(event, input) {
    if (!event || !input) return;
    const picker = catalogPickerFrom(input);
    const menu = picker?.querySelector?.('.neo-comfy-space-catalog-menu');
    if (event.key === 'Escape') { event.preventDefault?.(); closeCatalog(input); return; }
    if (event.key === 'ArrowDown') { event.preventDefault?.(); openCatalog(input); return; }
    if (event.key === 'Enter' && menu && !menu.hidden) {
      const first = menu.querySelector?.('.neo-comfy-space-catalog-option');
      if (first) { event.preventDefault?.(); chooseCatalog(first); }
    }
  }

  function randomizeSeed(button) {
    const rendererId = button?.dataset?.neoSpaceRendererId || '';
    const inputId = button?.dataset?.neoSpaceInputId || '';
    const instance = instances.get(rendererId);
    const root = typeof document !== 'undefined' ? document.getElementById(rendererId) : null;
    const field = root?.querySelector(`[data-neo-space-input-id="${typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(inputId) : inputId}"]`);
    if (!instance || !field) return;
    const value = Math.floor(Math.random() * 2147483647);
    field.value = String(value); instance.values[inputId] = value;
    dispatchBridge(instance, instance.bridge?.inputAction || instance.bridge?.action, { rendererId, inputId, control: 'seed_input', value });
  }
  function toggleAdvanced(toggle) {
    const rendererId = toggle?.dataset?.neoSpaceRendererId || '';
    const root = typeof document !== 'undefined' ? document.getElementById(rendererId) : null;
    if (root) root.dataset.advancedVisible = toggle.checked ? 'true' : 'false';
  }
  function handleSubmit(event, form) {
    if (event?.preventDefault) event.preventDefault();
    const rendererId = form?.closest?.('[data-neo-space-renderer-id]')?.dataset?.neoSpaceRendererId || '';
    const instance = instances.get(rendererId);
    if (!instance?.bridge?.submitAction) return false;
    dispatchBridge(instance, instance.bridge.submitAction, { rendererId, values: { ...instance.values } });
    return false;
  }
  function getValues(rendererId) { return { ...(instances.get(rendererId)?.values || {}) }; }
  function getValidationErrors(rendererId) { return { ...(instances.get(rendererId)?.validationErrors || {}) }; }
  function getAssetFiles(rendererId, inputId) { return [...(instances.get(rendererId)?.assetFiles?.[inputId] || [])]; }
  function reset(rendererId) { instances.delete(rendererId); }
  function capabilities() {
    return {
      schema_version: RENDERER_SCHEMA_VERSION,
      manifest_schema: SUPPORTED_MANIFEST,
      ui_schema: SUPPORTED_UI,
      shared_renderer: true,
      builder_preview: true,
      public_runner_reusable: true,
      host_header_visibility: true,
      host_submit_visibility: true,
      host_split_layout: true,
      native_neo_runtime_polish: true,
      runtime_diagnostics_suppressed: true,
      native_asset_dropzone: true,
      live_runtime_catalog_controls: true,
      searchable_runtime_catalogs: true,
      stale_catalog_value_warning: true,
      workflow_execution: false,
      asset_transport: false,
      local_asset_selection_state: true,
      conditional_dynamic_controls: true,
      declarative_repeaters: true,
      generated_per_space_javascript: false,
      controls: [...INPUT_CONTROLS],
      outputs: [...OUTPUT_RENDERERS],
    };
  }

  return { RENDERER_SCHEMA_VERSION, SUPPORTED_DYNAMIC_CONTROLS, validate, initialValues, render, handleInput, applyDynamicState, dynamicStates, handleRepeaterItem, addRepeaterItem, removeRepeaterItem, moveRepeaterItem, catalogMatches, openCatalog, closeCatalog, deferCloseCatalog, searchCatalog, toggleCatalog, chooseCatalog, catalogKeydown, randomizeSeed, toggleAdvanced, handleSubmit, getValues, getValidationErrors, getAssetFiles, reset, capabilities, safeUrl };
});
