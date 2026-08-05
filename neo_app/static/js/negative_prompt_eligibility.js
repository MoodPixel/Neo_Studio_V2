(function (root) {
  'use strict';

  const SCHEMA = 'neo.image.negative_prompt_eligibility.v1';
  const PHASE = 'IP-2';
  const LIVE_UX_PHASE = 'IR-4';
  const STARTUP_RECOVERY_PHASE = 'IR-6.2';

  const STATES = Object.freeze({
    ACTIVE: 'ACTIVE',
    WEAK: 'WEAK',
    INACTIVE_CFG: 'INACTIVE_CFG',
    DISABLED_FAMILY: 'DISABLED_FAMILY',
    DISABLED_ROUTE: 'DISABLED_ROUTE',
    PROFILE_CONTROLLED: 'PROFILE_CONTROLLED',
  });

  const FAMILY_ALIASES = Object.freeze({
    'sd1.5': 'sd15',
    sd_1_5: 'sd15',
    stable_diffusion_1_5: 'sd15',
    sd_xl: 'sdxl',
    flux1: 'flux',
    flux_1: 'flux',
    'flux.1': 'flux',
    klein: 'flux2_klein',
    flux2: 'flux2_klein',
    flux_2_klein: 'flux2_klein',
    krea2_raw: 'krea2',
    krea2_base: 'krea2',
    zimage: 'z_image',
    zimage_turbo: 'z_image_turbo',
    qwen: 'qwen_image',
    qwen_2509: 'qwen_image_edit_2509',
    qwen_2511: 'qwen_image_edit_2511',
  });

  function token(value) {
    return String(value ?? '').trim().toLowerCase().replace(/[- ]/g, '_');
  }

  function normalizeFamily(value) {
    const raw = token(value);
    return FAMILY_ALIASES[raw] || raw;
  }

  function numberOrNull(value) {
    if (value === '' || value == null) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function familyPolicy(context) {
    const family = normalizeFamily(context.family);
    if (['sd15', 'sdxl', 'krea2', 'z_image'].includes(family)) {
      return { policy: 'cfg_gated', activationFields: ['cfg'], label: 'CFG', hard: 1.0, weak: 1.5 };
    }
    if (['qwen_image', 'qwen_image_edit_2509', 'qwen_image_edit_2511'].includes(family)) {
      return { policy: 'cfg_gated', activationFields: ['true_cfg', 'cfg'], label: 'True CFG', hard: 1.0, weak: 1.5 };
    }
    if (['krea2_turbo', 'z_image_turbo'].includes(family)) {
      return { policy: 'disabled_by_family', activationFields: [], label: null, hard: null, weak: null };
    }
    if (['flux', 'flux1_fill', 'flux2_klein'].includes(family)) {
      return { policy: 'disabled_by_route', activationFields: [], label: null, hard: null, weak: null };
    }
    return { policy: 'profile_controlled', activationFields: [], label: null, hard: null, weak: null };
  }

  function activationValue(policy, context) {
    for (const field of policy.activationFields || []) {
      if (!Object.prototype.hasOwnProperty.call(context, field)) continue;
      const raw = context[field];
      const parsed = numberOrNull(raw);
      if (parsed != null) return { field, value: parsed, raw };
      if (raw !== '' && raw != null) return { field, value: null, raw };
    }
    return { field: null, value: null, raw: null };
  }

  function evaluate(context) {
    const ctx = context || {};
    const family = normalizeFamily(ctx.family);
    const policy = familyPolicy(ctx);
    const activation = activationValue(policy, ctx);
    let state = STATES.PROFILE_CONTROLLED;
    let shouldSend = true;
    let severity = 'info';
    let reasonCode = 'profile_controls_negative_prompt';
    let message = 'Negative prompt behavior is controlled by the selected provider or model profile.';

    if (policy.policy === 'disabled_by_family') {
      state = STATES.DISABLED_FAMILY;
      shouldSend = false;
      severity = 'muted';
      reasonCode = 'negative_prompt_disabled_by_family';
      message = 'This model family does not execute classifier-free negative prompting on this route.';
    } else if (policy.policy === 'disabled_by_route') {
      state = STATES.DISABLED_ROUTE;
      shouldSend = false;
      severity = 'muted';
      reasonCode = 'negative_prompt_disabled_by_route';
      message = 'This route does not execute a True-CFG negative branch; model guidance is a separate control.';
    } else if (policy.policy === 'cfg_gated') {
      if (!activation.field) {
        state = STATES.ACTIVE;
        shouldSend = true;
        severity = 'info';
        reasonCode = 'activation_value_not_submitted';
        message = `Negative prompting is supported. ${policy.label} was not explicitly submitted, so provider/default resolution remains authoritative.`;
      } else if (activation.value == null) {
        state = STATES.ACTIVE;
        shouldSend = true;
        severity = 'warning';
        reasonCode = 'activation_value_unparseable';
        message = `Negative prompting is supported, but ${activation.field} could not be parsed; the user value is retained rather than silently disabled.`;
      } else if (activation.value <= policy.hard) {
        state = STATES.INACTIVE_CFG;
        shouldSend = false;
        severity = 'muted';
        reasonCode = 'cfg_not_above_hard_threshold';
        message = `Negative prompting is inactive because ${policy.label} must be greater than ${policy.hard}.`;
      } else if (activation.value < policy.weak) {
        state = STATES.WEAK;
        shouldSend = true;
        severity = 'warning';
        reasonCode = 'cfg_in_weak_negative_range';
        message = `Negative prompting is active, but ${policy.label} below ${policy.weak} may produce only weak negative influence.`;
      } else {
        state = STATES.ACTIVE;
        shouldSend = true;
        severity = 'success';
        reasonCode = 'negative_prompt_active';
        message = `Negative prompting is active through ${policy.label}.`;
      }
    }

    return Object.freeze({
      schema: SCHEMA,
      phase: PHASE,
    liveUxPhase: LIVE_UX_PHASE,
    startupRecoveryPhase: STARTUP_RECOVERY_PHASE,
      family,
      state,
      severity,
      reason_code: reasonCode,
      message,
      should_send_negative_prompt: shouldSend,
      should_disable_ui: [STATES.INACTIVE_CFG, STATES.DISABLED_FAMILY, STATES.DISABLED_ROUTE].includes(state),
      should_warn_ui: [STATES.WEAK, STATES.PROFILE_CONTROLLED].includes(state),
      user_text_retained: true,
      negative_prompt_policy: policy.policy,
      activation_field: activation.field || (policy.activationFields || [])[0] || null,
      activation_value: activation.value,
      hard_min_exclusive: policy.hard,
      weak_below: policy.weak,
    });
  }

  function preparePayload(payload) {
    const source = payload && typeof payload === 'object' ? payload : {};
    const prepared = JSON.parse(JSON.stringify(source));
    if (String(prepared.surface || '').toLowerCase() !== 'image') return prepared;
    prepared.params = prepared.params && typeof prepared.params === 'object' ? prepared.params : {};
    const params = prepared.params;
    const prior = params.negative_prompt_eligibility;
    const userNegative = prior && (prepared.negative_prompt == null || prepared.negative_prompt === '') && Object.prototype.hasOwnProperty.call(params, 'negative_prompt_input')
      ? String(params.negative_prompt_input || '')
      : String(prepared.negative_prompt || '');
    const evaluation = evaluate({
      family: prepared.family,
      loader: prepared.loader,
      mode: prepared.mode,
      variant: params.flux_variant || params.variant || params.krea2_variant || params.z_image_variant || '',
      cfg: params.cfg,
      true_cfg: params.true_cfg,
      flux_guidance: params.flux_guidance,
      guidance: params.guidance,
    });
    const effective = evaluation.should_send_negative_prompt ? userNegative : '';
    params.negative_prompt_input = userNegative;
    params.effective_negative_prompt = effective;
    params.negative_prompt_eligibility = evaluation;
    params.negative_prompt_suppressed = Boolean(userNegative && !evaluation.should_send_negative_prompt);
    prepared.negative_prompt = effective;
    return prepared;
  }

  const FIELD_SELECTORS = Object.freeze({
    // IR-4: the live Neo Image workspace uses imageWorkspaceFamily/imageFamily
    // and imageWorkspaceLoader/imageLoader. Keep earlier aliases for external
    // renderers, but the source-of-truth renderer must be first-class here.
    family: ['#imageWorkspaceFamily', '#imageFamily', '#imageModelFamily', '#modelFamily', '#model_family', '[name="family"]', '[name="model_family"]', '[data-image-field="family"]'],
    loader: ['#imageWorkspaceLoader', '#imageLoader', '#imageLoaderType', '#modelLoader', '#loaderType', '[name="loader"]', '[name="loader_type"]', '[data-image-field="loader"]'],
    mode: ['#imageWorkflowMode', '#imageWorkspaceWorkflowMode', '#imageMode', '#workflowMode', '[name="mode"]', '[name="workflow_mode"]', '[data-image-field="mode"]'],
    cfg: ['#imageCfg', '#cfg', '#cfgScale', '[name="cfg"]', '[name="cfg_scale"]', '[data-param="cfg"]'],
    true_cfg: ['#imageCfg[data-neo-guidance-semantic="true_cfg"]', '#imageParam_true_cfg', '#trueCfg', '#true_cfg', '[name="true_cfg"]', '[data-param="true_cfg"]'],
    negative: ['#imageNegativePrompt', '#negativePrompt', '#negative_prompt', 'textarea[name="negative_prompt"]', 'input[name="negative_prompt"]', '[data-image-field="negative_prompt"]'],
  });

  function findFirst(scope, selectors) {
    if (!scope || typeof scope.querySelector !== 'function') return null;
    for (const selector of selectors) {
      const found = scope.querySelector(selector);
      if (found) return found;
    }
    return null;
  }

  function valueOf(element) {
    if (!element) return undefined;
    return element.value != null ? element.value : element.getAttribute?.('value');
  }

  function contextFromDom(scope) {
    const cfgField = findFirst(scope, FIELD_SELECTORS.cfg);
    const explicitTrueCfg = findFirst(scope, FIELD_SELECTORS.true_cfg);
    const cfgSemantic = cfgField?.getAttribute?.('data-neo-guidance-semantic') || cfgField?.dataset?.neoGuidanceSemantic || '';
    const cfgValue = valueOf(cfgField);
    return {
      family: valueOf(findFirst(scope, FIELD_SELECTORS.family)),
      loader: valueOf(findFirst(scope, FIELD_SELECTORS.loader)),
      mode: valueOf(findFirst(scope, FIELD_SELECTORS.mode)),
      cfg: cfgValue,
      // Qwen intentionally reuses the single visible CFG control but labels it
      // True CFG. Treat that physical field as true_cfg without inventing a
      // duplicate control in the UI.
      true_cfg: explicitTrueCfg ? valueOf(explicitTrueCfg) : (cfgSemantic === 'true_cfg' ? cfgValue : undefined),
    };
  }

  function ensureHint(field) {
    const parent = field.parentElement || field;
    let hint = parent.querySelector?.('[data-neo-negative-eligibility-hint]');
    if (!hint && root.document) {
      hint = root.document.createElement('div');
      hint.setAttribute('data-neo-negative-eligibility-hint', 'true');
      hint.className = 'neo-negative-eligibility-hint';
      field.insertAdjacentElement?.('afterend', hint);
    }
    return hint;
  }

  function ensureStyles() {
    if (!root.document || root.document.getElementById('neo-negative-eligibility-styles')) return;
    const style = root.document.createElement('style');
    style.id = 'neo-negative-eligibility-styles';
    style.textContent = `
      .neo-negative-eligibility-hint{margin-top:.35rem;font-size:.78rem;line-height:1.35;color:var(--neo-muted,#94a3b8)}
      .neo-negative-eligibility-hint[data-tone="warning"]{color:var(--neo-warning,#f6c177)}
      .neo-negative-eligibility-hint[data-tone="success"]{color:var(--neo-success,#86efac)}
      [data-neo-negative-eligibility-container="true"][data-state="INACTIVE_CFG"],
      [data-neo-negative-eligibility-container="true"][data-state="DISABLED_FAMILY"],
      [data-neo-negative-eligibility-container="true"][data-state="DISABLED_ROUTE"]{opacity:.68}
      [data-neo-negative-eligibility-state="INACTIVE_CFG"],
      [data-neo-negative-eligibility-state="DISABLED_FAMILY"],
      [data-neo-negative-eligibility-state="DISABLED_ROUTE"]{color:var(--neo-text-muted,#94a3b8);background:rgba(148,163,184,.08);cursor:not-allowed}
    `;
    root.document.head?.appendChild(style);
  }

  function setAttributeIfChanged(element, name, value) {
    if (!element || typeof element.getAttribute !== 'function' || typeof element.setAttribute !== 'function') return;
    const next = String(value);
    if (element.getAttribute(name) !== next) element.setAttribute(name, next);
  }

  function removeAttributeIfPresent(element, name) {
    if (!element || typeof element.hasAttribute !== 'function' || typeof element.removeAttribute !== 'function') return;
    if (element.hasAttribute(name)) element.removeAttribute(name);
  }

  function resultSignature(result) {
    return [result.state, result.severity, result.reason_code, result.message, result.should_send_negative_prompt ? '1' : '0'].join('|');
  }

  function applyToDom(scope) {
    if (!root.document) return null;
    const searchRoot = scope && typeof scope.querySelector === 'function' ? scope : root.document;
    const field = findFirst(searchRoot, FIELD_SELECTORS.negative) || findFirst(root.document, FIELD_SELECTORS.negative);
    if (!field) return null;
    const imageScope = field.closest?.('[data-image-workspace], [data-surface="image"], [data-surface-id="image"], #imagePanel, .neo-image-panel, .neo-image-workspace, #surfacePanels') || root.document;
    const context = contextFromDom(imageScope);
    const result = evaluate(context);

    setAttributeIfChanged(field, 'data-neo-negative-eligibility-state', result.state);
    setAttributeIfChanged(field, 'aria-description', result.message);
    setAttributeIfChanged(field, 'data-neo-negative-user-text-retained', 'true');
    const container = field.closest?.('label') || field.parentElement || field;
    setAttributeIfChanged(container, 'data-neo-negative-eligibility-container', 'true');
    setAttributeIfChanged(container, 'data-state', result.state);
    if (result.should_disable_ui) {
      if (!field.disabled) setAttributeIfChanged(field, 'data-neo-negative-owned-disabled', 'true');
      if (!field.disabled) field.disabled = true;
      setAttributeIfChanged(field, 'aria-disabled', 'true');
    } else if (field.getAttribute('data-neo-negative-owned-disabled') === 'true') {
      if (field.disabled) field.disabled = false;
      removeAttributeIfPresent(field, 'aria-disabled');
      removeAttributeIfPresent(field, 'data-neo-negative-owned-disabled');
    }

    const hint = ensureHint(field);
    if (hint) {
      if (hint.textContent !== result.message) hint.textContent = result.message;
      setAttributeIfChanged(hint, 'data-tone', result.severity);
      setAttributeIfChanged(hint, 'data-state', result.state);
    }
    const signature = resultSignature(result);
    if (field.getAttribute('data-neo-negative-eligibility-event-signature') !== signature) {
      setAttributeIfChanged(field, 'data-neo-negative-eligibility-event-signature', signature);
      const EventCtor = root.CustomEvent || (typeof CustomEvent !== 'undefined' ? CustomEvent : null);
      if (EventCtor) root.document.dispatchEvent?.(new EventCtor('neo:negative-prompt-eligibility-changed', { detail: { ...result, live_ux_phase: LIVE_UX_PHASE, startup_recovery_phase: STARTUP_RECOVERY_PHASE } }));
    }
    return result;
  }

  function bindDom() {
    if (!root.document || root.__neoNegativePromptEligibilityBound) return;
    root.__neoNegativePromptEligibilityBound = true;
    ensureStyles();

    let observer = null;
    let observerTarget = null;
    let refreshQueued = false;
    let refreshRunning = false;

    const mutationHost = () => root.document.getElementById?.('surfacePanels') || root.document.body || root.document.documentElement;
    const reconnectObserver = () => {
      if (!observer) return;
      observer.disconnect?.();
      observerTarget = mutationHost();
      if (observerTarget) observer.observe(observerTarget, { childList: true, subtree: true });
    };
    const refresh = () => {
      if (refreshQueued || refreshRunning) return;
      refreshQueued = true;
      const enqueue = typeof root.queueMicrotask === 'function'
        ? root.queueMicrotask.bind(root)
        : (callback) => Promise.resolve().then(callback);
      enqueue(() => {
        refreshQueued = false;
        if (refreshRunning) return;
        refreshRunning = true;
        observer?.disconnect?.();
        try {
          applyToDom(observerTarget || mutationHost() || root.document);
        } finally {
          refreshRunning = false;
          reconnectObserver();
        }
      });
    };
    const refreshForFieldEvent = (event) => {
      const target = event?.target;
      if (!target || typeof target.matches !== 'function') return;
      const selectors = Object.values(FIELD_SELECTORS).flat();
      if (selectors.some((selector) => target.matches(selector))) refresh();
    };
    const mutationNeedsRefresh = (records = []) => records.some((record) => {
      const target = record?.target?.nodeType === 3 ? record.target.parentElement : record?.target;
      if (!target || typeof target.closest !== 'function') return true;
      return !target.closest('[data-neo-negative-eligibility-hint]');
    });

    root.document.addEventListener('input', refreshForFieldEvent, true);
    root.document.addEventListener('change', refreshForFieldEvent, true);
    root.document.addEventListener('neo:image-route-changed', refresh);
    root.document.addEventListener('neo:image-state-changed', refresh);
    root.document.addEventListener('neo:image-sampling-preset-authority-changed', refresh);
    root.document.addEventListener('neo:image-sampling-preset-applied', refresh);
    const ObserverCtor = root.MutationObserver || (typeof MutationObserver !== 'undefined' ? MutationObserver : null);
    observer = ObserverCtor ? new ObserverCtor((records) => { if (mutationNeedsRefresh(records)) refresh(); }) : null;

    const start = () => {
      reconnectObserver();
      refresh();
    };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }

  root.NeoNegativePromptEligibility = Object.freeze({
    schema: SCHEMA,
    phase: PHASE,
    liveUxPhase: LIVE_UX_PHASE,
    startupRecoveryPhase: STARTUP_RECOVERY_PHASE,
    states: STATES,
    normalizeFamily,
    evaluate,
    preparePayload,
    applyToDom,
    bindDom,
  });

  if (root.document) bindDom();
}(typeof window !== 'undefined' ? window : globalThis));
